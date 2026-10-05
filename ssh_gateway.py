"""Stellar SSH Gateway (Phase 11).

Provides direct, interactive terminal access to user sandbox containers via
standard SSH clients (e.g., `ssh user@localhost -p 2222`).

Two ways to sign in:
- With an SSH password set in Stellar's settings: connect as your email,
  `ssh you@example.com@localhost -p 2222`, and type that password.
- Otherwise, a browser-based device code flow:
  1. User connects with any SSH client and any name.
  2. Gateway displays a temporary device code (e.g., `stellar-a1b2c3d4`).
  3. User opens the /device link while signed in and confirms the session.
Either way the gateway then hooks the SSH channel into a bash PTY in the
sandbox of the user's latest chat.
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
from werkzeug.security import check_password_hash, generate_password_hash

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

# Limits. Every connection holds a thread and, while it waits for approval,
# a Redis key, before anyone has proven who they are, so the gateway caps
# how many it will hold at once and how fast one address may open them.
MAX_SESSIONS = 50          # connections at once, across everyone
MAX_PER_ADDRESS = 5        # connections at once from one address
MAX_NEW_PER_MINUTE = 20    # new connections per address per minute
HANDSHAKE_TIMEOUT = 30     # seconds to finish SSH negotiation
IDLE_TIMEOUT = 30 * 60     # a shell nobody types into is closed
APPROVAL_RECHECK = 30      # seconds between checks that the account still has access

_admission_lock = threading.Lock()
_active_by_address: dict[str, int] = {}
_recent_by_address: dict[str, list[float]] = {}


def _admit(address: str) -> str | None:
    """Reserve a slot for a new connection, or say why there is none."""
    now = time.time()
    with _admission_lock:
        if sum(_active_by_address.values()) >= MAX_SESSIONS:
            return "the gateway is full"
        if _active_by_address.get(address, 0) >= MAX_PER_ADDRESS:
            return "too many connections from this address"
        recent = [t for t in _recent_by_address.get(address, []) if now - t < 60]
        if len(recent) >= MAX_NEW_PER_MINUTE:
            return "connecting too often"
        recent.append(now)
        _recent_by_address[address] = recent
        _active_by_address[address] = _active_by_address.get(address, 0) + 1
    return None


def _release(address: str) -> None:
    with _admission_lock:
        left = _active_by_address.get(address, 1) - 1
        if left > 0:
            _active_by_address[address] = left
        else:
            _active_by_address.pop(address, None)


def _db_path() -> Path:
    return stellar_app.PROJECT_ROOT / stellar_app.env("DATABASE_NAME", "stellar_local.db")


def _redis():
    return redis.from_url(stellar_app.env("REDIS_URL", "redis://localhost:6379/0"),
                          decode_responses=True)


# Password sign-in. The username must be the account's email and the
# password the SSH password from Stellar's settings. Which way a connection
# signs in depends only on whether its name contains an @, never on whether
# an account exists, so the gateway does not reveal which accounts do.
PASSWORD_FAILURES_ALLOWED = 5      # wrong passwords before a lockout
PASSWORD_LOCKOUT = 15 * 60         # seconds, per account and per address
# Checked when the account is missing or has no SSH password, so a wrong
# name costs as long as a wrong password and timing gives nothing away.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def _failure_keys(email: str, address: str) -> tuple[str, str]:
    return f"ssh_fail:acct:{email}", f"ssh_fail:addr:{address}"


def _locked_out(email: str, address: str) -> bool:
    try:
        r = _redis()
        return any(int(r.get(k) or 0) >= PASSWORD_FAILURES_ALLOWED
                   for k in _failure_keys(email, address))
    except Exception:
        # Without Redis the failures cannot be counted, and an uncounted
        # password prompt is an unlimited guessing machine.
        return True


def _record_failure(email: str, address: str) -> None:
    try:
        r = _redis()
        for key in _failure_keys(email, address):
            r.incr(key)
            r.expire(key, PASSWORD_LOCKOUT, nx=True)
    except Exception as exc:
        logger.warning("Could not count a failed SSH password: %s", exc)


def password_login(username: str, password: str, address: str) -> int | None:
    """The account id when the SSH password is right, else None."""
    email = (username or "").strip().lower()
    if not email or not password or _locked_out(email, address):
        return None
    import sqlite3
    row = None
    try:
        con = sqlite3.connect(_db_path(), timeout=5)
        try:
            row = con.execute("SELECT id, is_approved, ssh_password_hash FROM users"
                              " WHERE lower(username) = ?", (email,)).fetchone()
        finally:
            con.close()
    except Exception as exc:
        logger.warning("Could not check an SSH password: %s", exc)
        return None
    stored = row[2] if row and row[2] else None
    right = check_password_hash(stored or _DUMMY_HASH, password)
    if not (right and stored and row[1]):
        _record_failure(email, address)
        return None
    return int(row[0])


def _still_allowed(db_path, user_id: int) -> bool:
    """Is this account still approved? Revoking access ends SSH sessions too."""
    import sqlite3
    try:
        con = sqlite3.connect(db_path, timeout=5)
        try:
            row = con.execute("SELECT is_approved FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            con.close()
    except Exception:
        return True          # a database hiccup is not a revocation
    return bool(row and row[0])


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
        self.user_id: int | None = None      # set by a right SSH password
        self.channel: paramiko.Channel | None = None
        self.exec_id: str | None = None
        self.docker_api = None

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_none(self, username: str) -> int:
        # An email asks for its SSH password. Any other name connects with
        # no password and is then authorized by the browser device-code
        # challenge.
        if "@" in (username or ""):
            return paramiko.AUTH_FAILED
        self.username = username or "user"
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_password(self, username: str, password: str) -> int:
        user_id = password_login(username, password, self.client_addr[0])
        if user_id is None:
            logger.warning("Refused an SSH password for %s from %s", username, self.client_addr[0])
            return paramiko.AUTH_FAILED
        self.username = username
        self.user_id = user_id
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        return "password" if "@" in (username or "") else "none"

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
    # A client that connects and then says nothing must not hold a thread
    # forever.
    transport.banner_timeout = HANDSHAKE_TIMEOUT
    transport.handshake_timeout = HANDSHAKE_TIMEOUT
    transport.auth_timeout = HANDSHAKE_TIMEOUT

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

    if server.user_id is not None:
        user_id = server.user_id
        channel.send(b"\r\n\x1b[1;32mSigned in with your SSH password. "
                     b"Opening your sandbox...\x1b[0m\r\n\r\n")
        logger.info("SSH password sign-in for user_id=%s from %s", user_id, addr)
    else:
        user_id = _approve_in_browser(channel, transport, server, addr)
        if user_id is None:
            return

    _attach_sandbox(channel, transport, server, addr, user_id)


def _approve_in_browser(channel, transport, server: StellarSSHServer, addr) -> int | None:
    """The device-code flow: show a code, wait for approval in the browser.

    The account id once approved; None (with the connection closed) if the
    code expires, is refused, or the client leaves.
    """
    r = _redis()

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
        b"\r\n"
        b"  To skip this step, set an SSH password in Stellar's Settings and\r\n"
        b"  connect with your email as the user name.\r\n"
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
            return None

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
        return None

    channel.send(b"\r\n\x1b[1;32mAuthorization confirmed! Spawning sandbox container PTY...\x1b[0m\r\n\r\n")
    logger.info("Device code %s approved for user_id=%s from %s", device_code, user_id, addr)
    return user_id


def _attach_sandbox(channel, transport, server: StellarSSHServer, addr, user_id: int) -> None:
    """Hook the SSH channel to a shell in the user's latest chat's sandbox."""
    try:
        client = stellar_app._docker()
        db_path = _db_path()
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

        # A recognisable name, so the shell can be ended when the connection
        # drops: closing the socket alone left bash running in the container.
        shell_name = f"stellar-ssh-{secrets.token_hex(4)}"
        exec_inst = api.exec_create(
            container.id,
            cmd=["/bin/bash", "-c", f"exec -a {shell_name} /bin/bash -l"],
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

    # An SSH shell is use of the sandbox: marked busy while it lasts, so the
    # idle reaper and the per-user cap never stop the container under it.
    lab_redis = stellar_app._current_redis_url()
    stellar_app._lab_busy(lab_redis, container.name, APPROVAL_RECHECK * 3)
    last_input = last_check = time.time()
    try:
        while not stop_event.is_set() and not channel.closed:
            now = time.time()
            if now - last_input > IDLE_TIMEOUT:
                channel.send(b"\r\nClosed after 30 minutes without input.\r\n")
                break
            if now - last_check > APPROVAL_RECHECK:
                last_check = now
                if not _still_allowed(db_path, user_id):
                    channel.send(b"\r\nThis account no longer has access to Stellar.\r\n")
                    break
                stellar_app._lab_busy(lab_redis, container.name, APPROVAL_RECHECK * 3)
            # Channel receive with short timeout
            if channel.recv_ready():
                user_data = channel.recv(4096)
                if not user_data:
                    break
                last_input = now
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
        stellar_app._lab_free(lab_redis, container.name)
        try:
            raw_sock.close()
        except Exception:
            pass
        # bash ignores the SIGTERM a closed socket implies; SIGHUP, aimed at
        # this session's shell by name, actually ends it.
        try:
            hup = api.exec_create(container.id, cmd=["pkill", "-HUP", "-f", "^" + shell_name])
            api.exec_start(hup["Id"])
        except Exception as exc:
            logger.debug("Could not end shell %s: %s", shell_name, exc)
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

    def serve(client_sock, addr):
        try:
            handle_client(client_sock, addr, host_key)
        finally:
            _release(addr[0])

    while True:
        try:
            client_sock, addr = server_sock.accept()
            refused = _admit(addr[0])
            if refused:
                logger.warning("Refused SSH connection from %s: %s", addr[0], refused)
                client_sock.close()
                continue
            t = threading.Thread(
                target=serve,
                args=(client_sock, addr),
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
