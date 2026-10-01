"""Build the sandbox image, and secure the local Redis.

    .venv/Scripts/python.exe docker_setup.py                 # build if missing
    .venv/Scripts/python.exe docker_setup.py --rebuild       # force a rebuild
    .venv/Scripts/python.exe docker_setup.py --secure-redis  # Redis with a password

Run once before using lab_execute, and again whenever Dockerfile.lab changes.
Building takes a few minutes the first time and seconds after that, because
Docker caches every layer that has not changed.

--secure-redis recreates the stellar-redis container listening on
127.0.0.1 only and requiring a password, generates that password, and
writes the matching REDIS_URL into keys.env. It never prints the password.
Code running in a sandbox can reach the host, so a Redis without a
password is a Redis every sandbox can read and write: live replies,
terminal keystrokes, SSH approvals. Redis keeps only short-lived state for
Stellar, so recreating it loses nothing that matters.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent
KEYS_ENV = PROJECT_ROOT / "keys.env"

LAB_IMAGE = "stellar-lab:latest"
LAB_DOCKERFILE = "Dockerfile.lab"

REDIS_CONTAINER = "stellar-redis"
REDIS_IMAGE = "redis:7-alpine"
REDIS_PORT = 6379


def _client():
    import docker
    try:
        c = docker.from_env()
        c.ping()
        return c
    except Exception as exc:
        print(f"  Cannot reach Docker: {exc}")
        print("  Start Docker Desktop and try again.")
        sys.exit(1)


def build_image(client, force: bool = False) -> None:
    existing = {t for img in client.images.list() for t in (img.tags or [])}

    if LAB_IMAGE in existing and not force:
        print(f"  image {LAB_IMAGE} already built (--rebuild to force)")
        return

    print(f"  building {LAB_IMAGE} from dockerfiles/{LAB_DOCKERFILE} ...")
    print("  first build pulls python:3.12-slim and compiles nothing; "
          "expect a few minutes")

    # The low-level API streams build output; the high-level one blocks
    # silently until it finishes, which looks like a hang on a long build.
    stream = client.api.build(
        path=str(PROJECT_ROOT / "dockerfiles"),
        dockerfile=LAB_DOCKERFILE,
        tag=LAB_IMAGE,
        rm=True,
        nocache=force,
        decode=True,
    )

    for chunk in stream:
        if "stream" in chunk:
            line = chunk["stream"].rstrip()
            if line:
                print(f"    {line[:140]}")
        elif "error" in chunk:
            print(f"  BUILD FAILED: {chunk['error'][:300]}")
            sys.exit(1)

    print(f"  built {LAB_IMAGE}")


# --- keys.env ---------------------------------------------------------------
def _env_value(name: str) -> str:
    if not KEYS_ENV.exists():
        return ""
    m = re.search(rf"^{re.escape(name)}=([^\r\n]*)", KEYS_ENV.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def _set_env_value(name: str, value: str) -> None:
    """Replace NAME=... in keys.env, or append it, keeping everything else.

    [^\\r\\n]* rather than .*: in a file saved with Windows line endings, .*
    would swallow the \\r and leave that one line with a different ending.
    """
    text = KEYS_ENV.read_text(encoding="utf-8") if KEYS_ENV.exists() else ""
    line = f"{name}={value}"
    pattern = re.compile(rf"^{re.escape(name)}=[^\r\n]*", re.M)
    if pattern.search(text):
        text = pattern.sub(lambda _m: line, text, count=1)
    else:
        newline = "\r\n" if "\r\n" in text else "\n"
        text = (text.rstrip("\r\n") + newline if text else "") + line + newline
    tmp = KEYS_ENV.with_name(KEYS_ENV.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, KEYS_ENV)


# --- Redis --------------------------------------------------------------------
def redis_status(client) -> tuple[bool, str]:
    """(secured?, explanation) for the stellar-redis container and keys.env."""
    import docker.errors
    try:
        c = client.containers.get(REDIS_CONTAINER)
    except docker.errors.NotFound:
        return False, "there is no stellar-redis container"
    bindings = (c.attrs.get("HostConfig", {}).get("PortBindings") or {}).get(f"{REDIS_PORT}/tcp") or []
    if not bindings or any(b.get("HostIp") not in ("127.0.0.1",) for b in bindings):
        return False, "Redis is published on every network interface"
    if "--requirepass" not in (c.attrs.get("Config", {}).get("Cmd") or []):
        return False, "Redis accepts connections without a password"
    if not urlsplit(_env_value("REDIS_URL")).password:
        return False, "REDIS_URL in keys.env has no password"
    return True, "local-only, password required, REDIS_URL matches"


def secure_redis(client) -> None:
    import docker.errors
    import redis as redis_lib

    ok, why = redis_status(client)
    if ok and "--force" not in sys.argv:
        print(f"  redis: already secured ({why})")
        return
    print(f"  redis: securing ({why})")

    password = secrets.token_urlsafe(32)          # URL-safe: no escaping needed
    try:
        client.containers.get(REDIS_CONTAINER).remove(force=True)
        print("  redis: removed the old container")
    except docker.errors.NotFound:
        pass

    client.containers.run(
        REDIS_IMAGE,
        name=REDIS_CONTAINER,
        detach=True,
        command=["redis-server", "--requirepass", password],
        ports={f"{REDIS_PORT}/tcp": ("127.0.0.1", REDIS_PORT)},
        restart_policy={"Name": "unless-stopped"},
        labels={"stellar": "redis"},
    )

    url = f"redis://:{password}@127.0.0.1:{REDIS_PORT}/0"
    deadline = time.time() + 20
    while True:
        try:
            redis_lib.from_url(url, socket_connect_timeout=2).ping()
            break
        except Exception as exc:
            if time.time() > deadline:
                print(f"  redis: did not answer in time ({type(exc).__name__}); "
                      f"keys.env was NOT changed")
                sys.exit(1)
            time.sleep(0.5)

    _set_env_value("REDIS_URL", url)
    print(f"  redis: running on 127.0.0.1:{REDIS_PORT} with a new password")
    print("  redis: REDIS_URL in keys.env updated (the password is only in that file)")
    print("  redis: restart Stellar and the SSH gateway so they pick it up")


def main() -> int:
    force = "--rebuild" in sys.argv
    print("\n  Stellar sandbox setup\n")

    client = _client()
    print(f"  docker {client.version().get('Version', '?')}")

    if "--secure-redis" in sys.argv:
        secure_redis(client)
    else:
        ok, why = redis_status(client)
        print(f"  redis: {'secured' if ok else 'NOT secured'} ({why})"
              + ("" if ok else " -> run with --secure-redis"))

    build_image(client, force)

    # Sandbox networks are created by the app on first use, with the
    # bridge names and address ranges the firewall rules expect.
    workspace = PROJECT_ROOT / "sandbox_runs"
    workspace.mkdir(exist_ok=True)
    print(f"  workspace root {workspace}")

    print("\n  Ready. lab_execute can now start containers.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
