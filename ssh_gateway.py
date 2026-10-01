"""Stellar SSH Gateway (Phase 11).

Provides direct, interactive terminal access to user sandbox containers via
standard SSH clients (e.g., `ssh user@localhost -p 2222`).

Authenticates using a browser-based device code flow:
1. User connects with any SSH client.
2. Gateway displays a temporary device code (e.g., `stellar-a1b2`).
3. User navigates to http://127.0.0.1:5000/device and confirms the session.
4. Gateway hooks the SSH channel into the user's Docker sandbox container bash PTY.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

from dotenv import load_dotenv
import paramiko
import redis

# Ensure stellar app module is importable
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / "keys.env")

import app as stellar_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [ssh_gateway] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stellar.ssh_gateway")

DEFAULT_SSH_PORT = int(stellar_app.env("STELLAR_SSH_PORT", "2222"))
HOST_KEY_FILE = PROJECT_ROOT / "ssh_host_rsa.key"


def get_or_create_host_key() -> paramiko.RSAKey:
    """Load existing host key or generate a fresh 2048-bit RSA key."""
    if HOST_KEY_FILE.exists():
        try:
            return paramiko.RSAKey(filename=str(HOST_KEY_FILE))
        except Exception as exc:
            logger.warning("Could not read host key (%s); regenerating", exc)

    logger.info("Generating fresh SSH host key at %s", HOST_KEY_FILE)
    key = paramiko.RSAKey.generate(2048)
    try:
        key.write_private_key_file(str(HOST_KEY_FILE))
    except Exception as exc:
        logger.warning("Could not persist host key to disk: %s", exc)
    return key


class StellarSSHServer(paramiko.ServerInterface):
    """Paramiko server handling PTY negotiation and shell launch."""

    def __init__(self, client_addr: tuple[str, int]):
        self.client_addr = client_addr
        self.event = threading.Event()
        self.term = "xterm-256color"
        self.width = 80
        self.height = 24
        self.username = "user"
        self.channel: paramiko.Channel | None = None
        self.exec_id: str | None = None
        self.docker_api = None

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_none(self, username: str) -> int:
        # Initial connect is allowed without a static password; authorization
        # is performed via the browser device-code challenge.
        self.username = username or "user"
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        return "none"

    def check_channel_pty_request(
        self, channel, term, width, height, pixelwidth, pixelheight, modes
    ) -> bool:
        self.term = term or "xterm-256color"
        self.width = width or 80
        self.height = height or 24
        return True

    def check_channel_window_change_request(
        self, channel, width, height, pixelwidth, pixelheight
    ) -> bool:
        self.width = width or 80
        self.height = height or 24
        if self.docker_api and self.exec_id:
            try:
                self.docker_api.exec_resize(self.exec_id, height=self.height, width=self.width)
            except Exception:
                pass
        return True

    def check_channel_shell_request(self, channel) -> bool:
        self.channel = channel
        self.event.set()
        return True


def handle_client(client_sock: socket.socket, addr: tuple[str, int], host_key: paramiko.RSAKey) -> None:
    """Handle one incoming SSH connection."""
    logger.info("Incoming SSH connection from %s:%s", addr[0], addr[1])
    transport = paramiko.Transport(client_sock)
    transport.add_server_key(host_key)

    server = StellarSSHServer(addr)
    try:
        transport.start_server(server=server)
    except Exception as exc:
        logger.error("SSH negotiation failed with %s: %s", addr, exc)
        transport.close()
        return

    channel = transport.accept(20)
    if channel is None:
        logger.warning("No channel requested by %s", addr)
        transport.close()
        return

    server.event.wait(10)
    if not server.event.is_set():
        channel.send(b"\r\nTimeout waiting for shell request.\r\n")
        channel.close()
        transport.close()
        return

    # Phase 11 Device Code Authorization Flow
    redis_url = stellar_app.env("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url, decode_responses=True)

    # Eight hex characters, not four. Four is 65,536 codes, few enough to
    # guess while one is pending.
    device_code = f"stellar-{secrets.token_hex(4)}"
    code_info = {
        "status": "pending",
        "username": server.username,
        "remote_addr": addr[0],
        "created_at": time.time(),
    }
    r.setex(f"ssh_device:{device_code}", 300, json.dumps(code_info))

    # The approval page lives on the main site. Locally that is
    # localhost:5000, the address Google sign-in also expects, so the link
    # opens in the browser session the user is already signed in to.
    domain = stellar_app.env("STELLAR_DOMAIN", "localhost:5000")
    protocol = "https" if stellar_app.env("SESSION_COOKIE_SECURE") == "1" else "http"
    auth_url = f"{protocol}://{domain}/device?code={device_code}"

    banner = (
        b"\r\n\x1b[1;36m======================================================================\x1b[0m\r\n"
        b"\x1b[1;37m                        STELLAR SSH GATEWAY\x1b[0m\r\n"
        b"\r\n"
        b"  To authorize this terminal session, open your browser and visit:\r\n"
        b"  \x1b[1;32m" + auth_url.encode("utf-8") + b"\x1b[0m\r\n"
        b"\r\n"
        b"  Authorization Code: \x1b[1;33m" + device_code.encode("utf-8") + b"\x1b[0m\r\n"
        b"\x1b[1;36m======================================================================\x1b[0m\r\n"
        b"Waiting for browser approval (expires in 5 minutes)...\r\n"
    )
    channel.send(banner)

    # Poll for approval
    user_id = None
    start_time = time.time()
    approved = False

    while time.time() - start_time < 300:
        if channel.closed or not transport.is_active():
            logger.info("Client disconnected while waiting for authorization: %s", addr)
            r.delete(f"ssh_device:{device_code}")
            return

        raw = r.get(f"ssh_device:{device_code}")
        if raw:
            try:
                data = json.loads(raw)
                if data.get("status") == "approved":
                    user_id = data.get("user_id")
                    approved = True
                    break
                if data.get("status") == "refused":
                    break
            except Exception:
                pass
        time.sleep(1)

    if not approved or user_id is None:
        channel.send(b"\r\n\x1b[1;31mSession authorization timed out or was refused.\x1b[0m\r\n")
        channel.close()
        transport.close()
        return

    channel.send(b"\r\n\x1b[1;32mAuthorization confirmed! Spawning sandbox container PTY...\x1b[0m\r\n\r\n")
    logger.info("Device code %s approved for user_id=%s from %s", device_code, user_id, addr)

    # Connect to user's sandbox container
    try:
        client = stellar_app._docker()
        # Find user's latest chat or default to chat 1
        db_path = stellar_app.PROJECT_ROOT / stellar_app.env("DATABASE_NAME", "stellar_local.db")
        chat_id = None
        if db_path.exists():
            import sqlite3
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            row = cur.execute("SELECT id FROM chats WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1", (user_id,)).fetchone()
            if row:
                chat_id = row[0]
            con.close()
        if chat_id is None:
            # There used to be a silent default of chat 1, which created a
            # workspace for a chat this user does not have.
            channel.send(b"\r\nYou have no chats yet. Start one in Stellar first; "
                         b"the SSH session opens that chat's sandbox.\r\n")
            channel.close()
            transport.close()
            return

        container = stellar_app._get_or_create_lab(client, user_id, chat_id)
        api = client.api
        server.docker_api = api

        exec_inst = api.exec_create(
            container.id,
            cmd=["/bin/bash"],
            stdin=True,
            tty=True,
            environment={"TERM": server.term, "COLORTERM": "truecolor"},
        )
        server.exec_id = exec_inst["Id"]
        sock = api.exec_start(server.exec_id, socket=True, tty=True)
        raw_sock = getattr(sock, "_sock", sock)

        # Apply initial window geometry
        time.sleep(0.1)
        try:
            api.exec_resize(server.exec_id, height=server.height, width=server.width)
        except Exception:
            pass

    except Exception as exc:
        logger.error("Failed to spawn container PTY for user %s: %s", user_id, exc)
        channel.send(f"\r\n\x1b[1;31mContainer error: {exc}\x1b[0m\r\n".encode("utf-8"))
        channel.close()
        transport.close()
        return

    # Pipe channel <-> Docker socket
    stop_event = threading.Event()

    def container_to_ssh():
        try:
            while not stop_event.is_set() and not channel.closed:
                data = None
                if hasattr(raw_sock, "recv"):
                    data = raw_sock.recv(4096)
                elif hasattr(sock, "read"):
                    data = sock.read(4096)
                if not data:
                    break
                channel.send(data)
        except Exception:
            pass
        finally:
            stop_event.set()

    t_read = threading.Thread(target=container_to_ssh, name=f"ssh-to-c{chat_id}", daemon=True)
    t_read.start()

    try:
        while not stop_event.is_set() and not channel.closed:
            # Channel receive with short timeout
            if channel.recv_ready():
                user_data = channel.recv(4096)
                if not user_data:
                    break
                if hasattr(raw_sock, "sendall"):
                    raw_sock.sendall(user_data)
                elif hasattr(sock, "write"):
                    sock.write(user_data)
                    sock.flush()
            else:
                time.sleep(0.02)
    except Exception as exc:
        logger.debug("SSH session loop ended: %s", exc)
    finally:
        stop_event.set()
        try:
            raw_sock.close()
        except Exception:
            pass
        channel.close()
        transport.close()
        logger.info("SSH session closed for user %s (%s)", user_id, addr)


# Loopback unless told otherwise. Binding every interface by default put
# an SSH port on the local network of whatever laptop ran this.
DEFAULT_SSH_HOST = stellar_app.env("STELLAR_SSH_HOST", "127.0.0.1")


def start_ssh_server(host: str = DEFAULT_SSH_HOST, port: int = DEFAULT_SSH_PORT) -> None:
    """Run the Paramiko SSH server listener."""
    host_key = get_or_create_host_key()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_sock.bind((host, port))
        server_sock.listen(50)
        logger.info("Stellar SSH Gateway listening on %s:%d", host, port)
    except Exception as exc:
        logger.error("Failed to bind SSH port %d: %s", port, exc)
        return

    while True:
        try:
            client_sock, addr = server_sock.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, addr, host_key),
                daemon=True,
                name=f"ssh-client-{addr[0]}",
            )
            t.start()
        except KeyboardInterrupt:
            logger.info("Shutting down SSH gateway.")
            break
        except Exception as exc:
            logger.error("Error accepting SSH connection: %s", exc)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SSH_PORT
    start_ssh_server(port=port)
