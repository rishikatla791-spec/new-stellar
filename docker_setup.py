"""Build the sandbox image and create the isolated network.

    .venv/Scripts/python.exe docker_setup.py            # build if missing
    .venv/Scripts/python.exe docker_setup.py --rebuild  # force a rebuild

Run once before using lab_execute, and again whenever Dockerfile.lab changes.
Building takes a few minutes the first time and seconds after that, because
Docker caches every layer that has not changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

LAB_IMAGE = "stellar-lab:latest"
LAB_DOCKERFILE = "Dockerfile.lab"

# The network every lab container joins when no per-user network applies.
FALLBACK_NETWORK = "stellar_isolated"


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


def ensure_network(client, name: str) -> None:
    """Create a bridge network with inter-container communication disabled.

    Docker's default bridge lets every container reach every other one. That
    is convenient for an app talking to its own database, and wrong here:
    each of these containers runs code chosen by someone else, so one user's
    sandbox must not be able to port-scan another's. com.docker.network.
    bridge.enable_icc=false is the switch that forbids it.
    """
    try:
        client.networks.get(name)
        print(f"  network {name} exists")
        return
    except Exception:
        pass

    client.networks.create(
        name,
        driver="bridge",
        options={"com.docker.network.bridge.enable_icc": "false"},
        labels={"stellar": "sandbox"},
    )
    print(f"  created network {name} (inter-container comms disabled)")


def main() -> int:
    force = "--rebuild" in sys.argv
    print("\n  Stellar sandbox setup\n")

    client = _client()
    print(f"  docker {client.version().get('Version', '?')}")

    build_image(client, force)
    ensure_network(client, FALLBACK_NETWORK)

    workspace = PROJECT_ROOT / "sandbox_runs"
    workspace.mkdir(exist_ok=True)
    print(f"  workspace root {workspace}")

    print("\n  Ready. lab_execute can now start containers.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
