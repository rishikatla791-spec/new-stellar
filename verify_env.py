"""Phase 0 environment check.

Run this before writing any application code, and again whenever something
mysteriously breaks:

    .venv/Scripts/python.exe verify_env.py     (Windows)
    .venv/bin/python verify_env.py             (Linux/WSL)

Most "the AI doesn't work" bugs are really environment bugs. This script
separates the two so you never debug the wrong layer.

Each check reports one of:
    PASS  - working
    SKIP  - not configured yet, and not needed until a later phase
    FAIL  - configured but broken, or required now and missing
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

# Phase that first requires each check. Anything above CURRENT_PHASE is
# advisory: a failure there is reported but does not fail the run.
CURRENT_PHASE = 2

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
)

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    colour = {"PASS": GREEN, "SKIP": YELLOW, "FAIL": RED}[status]
    print(f"  {colour}{status:<4}{RESET}  {name}")
    if detail:
        print(f"        {DIM}{detail}{RESET}")


# ----------------------------------------------------------------------
# 1. Python version
# ----------------------------------------------------------------------
def check_python() -> None:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) < (3, 10):
        record("FAIL", f"Python {version}", "Need 3.10+. Recreate the venv.")
    elif not sys.prefix.endswith(".venv"):
        record(
            "FAIL",
            f"Python {version}",
            f"Not running inside the project venv (prefix={sys.prefix}). "
            "Use .venv/Scripts/python.exe explicitly.",
        )
    else:
        record("PASS", f"Python {version}", f"venv at {sys.prefix}")


# ----------------------------------------------------------------------
# 2. Config file
# ----------------------------------------------------------------------
def check_config() -> dict[str, str]:
    env_path = PROJECT_ROOT / "keys.env"
    if not env_path.exists():
        record(
            "FAIL",
            "keys.env",
            "Missing. Run: cp keys.env.example keys.env  then fill it in.",
        )
        return {}

    try:
        from dotenv import dotenv_values
    except ImportError:
        record("FAIL", "keys.env", "python-dotenv not installed.")
        return {}

    # dotenv_values reads the file without mutating os.environ, so this
    # check cannot accidentally leak config into the rest of the process.
    values = {k: v for k, v in dotenv_values(env_path).items() if v}
    record("PASS", "keys.env", f"{len(values)} value(s) set")
    return values


# ----------------------------------------------------------------------
# 3. Gemini API  (phase 0 - the critical path)
# ----------------------------------------------------------------------
def check_gemini(config: dict[str, str]) -> None:
    key = config.get("PRIMARY_API_KEY") or os.environ.get("PRIMARY_API_KEY")
    if not key:
        record(
            "FAIL",
            "Gemini API",
            "PRIMARY_API_KEY not set. Get one free at aistudio.google.com/apikey",
        )
        return

    try:
        from google import genai
    except ImportError:
        record("FAIL", "Gemini API", "google-genai not installed.")
        return

    try:
        client = genai.Client(api_key=key)
        # Smallest possible real call: proves the key, the network path and
        # the SDK all work, for a negligible number of tokens.
        resp = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents="Reply with the single word: OK",
        )
        text = (resp.text or "").strip()
        record("PASS", "Gemini API", f"model replied: {text!r}")
    except Exception as exc:
        msg = str(exc)
        hint = ""
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            hint = " -> the key itself is wrong; regenerate it in AI Studio."
        elif "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            hint = " -> key is valid but rate-limited. This is phase 7's problem."
        elif "NOT_FOUND" in msg or "404" in msg:
            hint = " -> that model name is not available to your key."
        record("FAIL", "Gemini API", f"{type(exc).__name__}: {msg[:160]}{hint}")


# ----------------------------------------------------------------------
# 4. Redis  (phase 2)
# ----------------------------------------------------------------------
def check_redis(config: dict[str, str]) -> None:
    url = config.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as redis_lib
    except ImportError:
        record("SKIP", "Redis", "redis package not installed (needed in phase 2)")
        return

    try:
        client = redis_lib.from_url(url, socket_connect_timeout=2)
        client.ping()
        # Prove a real round trip, not just a handshake.
        client.set("stellar:verify", "1", ex=10)
        assert client.get("stellar:verify") in (b"1", "1")
        record("PASS", "Redis", f"ping + set/get ok at {url}")
    except Exception as exc:
        status = "FAIL" if CURRENT_PHASE >= 2 else "SKIP"
        record(
            status,
            "Redis",
            f"not reachable at {url} ({type(exc).__name__}). "
            "Needed from phase 2. Start with: docker run -d -p 6379:6379 --name stellar-redis redis:7-alpine",
        )


# ----------------------------------------------------------------------
# 5. Docker  (phase 5)
# ----------------------------------------------------------------------
def check_docker() -> None:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        status = "FAIL" if CURRENT_PHASE >= 5 else "SKIP"
        record(status, "Docker", "docker CLI not on PATH. Needed from phase 5.")
        return

    try:
        out = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}/{{.OSType}}"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            record("PASS", "Docker", f"daemon {out.stdout.strip()}")
        else:
            status = "FAIL" if CURRENT_PHASE >= 5 else "SKIP"
            record(
                status,
                "Docker",
                "CLI present but daemon not responding. Start Docker Desktop. "
                "Needed from phase 5.",
            )
    except Exception as exc:
        status = "FAIL" if CURRENT_PHASE >= 5 else "SKIP"
        record(status, "Docker", f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------
def main() -> int:
    print(f"\n  Stellar environment check {DIM}(phase {CURRENT_PHASE}){RESET}\n")

    check_python()
    config = check_config()
    check_gemini(config)
    check_redis(config)
    check_docker()

    failed = [name for status, name, _ in results if status == "FAIL"]
    skipped = [name for status, name, _ in results if status == "SKIP"]

    print()
    if failed:
        print(f"  {RED}{len(failed)} check(s) failed:{RESET} {', '.join(failed)}")
        print(f"  {DIM}Fix these before starting phase 1.{RESET}\n")
        return 1

    msg = f"  {GREEN}Environment ready.{RESET}"
    if skipped:
        msg += f" {DIM}({len(skipped)} deferred: {', '.join(skipped)}){RESET}"
    print(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
