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
CURRENT_PHASE = 8

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
)

results: list[tuple[str, str, str]] = []


# Checks that are genuinely optional: the app works without them, so a
# missing one reports SKIP at any phase rather than FAIL.
OPTIONAL = {"YouTube API", "Email (SMTP)"}


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
def _pool(config: dict[str, str], name: str) -> list[str]:
    """Collect a numbered credential family, mirroring app.collect_keys."""
    found = []
    exact = (config.get(name) or "").strip()
    if exact:
        found.append(exact)
    i = 1
    while (val := (config.get(f"{name}_{i}") or "").strip()):
        found.append(val)
        i += 1
    return list(dict.fromkeys(found))


def check_gemini(config: dict[str, str]) -> None:
    """Prove at least one key can reach the model.

    Every key in the pool is tried, not just the first. The whole point of
    phase 7 is that an exhausted key is skipped rather than fatal, so a
    check that gives up on key one contradicts the app it is checking - and
    on the free tier, key one is exhausted most evenings.
    """
    pool = _pool(config, "PRIMARY_API_KEY") + _pool(config, "BACKUP_API_KEY")
    pool = list(dict.fromkeys(pool))
    if not pool:
        record(
            "FAIL",
            "Gemini API",
            "No Gemini key. Set PRIMARY_API_KEY or BACKUP_API_KEY_1 in keys.env "
            "- free at aistudio.google.com/apikey",
        )
        return

    try:
        from google import genai
    except ImportError:
        record("FAIL", "Gemini API", "google-genai not installed.")
        return

    limited, broken = [], []
    for i, key in enumerate(pool, 1):
        try:
            # Smallest possible real call: proves the key, the network path
            # and the SDK all work, for a negligible number of tokens.
            # The client must stay referenced for the whole call. Chaining
            # off a temporary lets it be collected mid-request, and the SDK
            # then raises "client has been closed" - which looks like a
            # broken key and is nothing of the sort.
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents="Reply with the single word: OK",
            )
            detail = f"key {i} of {len(pool)} answered: {(resp.text or '').strip()!r}"
            if limited:
                detail += (f"; rate-limited today: {', '.join(map(str, limited))}"
                           f" (expected on the free tier)")
            if broken:
                detail += f"; broken: {', '.join(broken)}"
            record("PASS", "Gemini API", detail)
            return
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                limited.append(i)
            elif "API_KEY_INVALID" in msg or "API key not valid" in msg:
                broken.append(f"{i} (invalid key)")
            elif "NOT_FOUND" in msg or "404" in msg:
                broken.append(f"{i} (model not available)")
            else:
                broken.append(f"{i} ({type(exc).__name__})")

    if limited and not broken:
        record(
            "FAIL",
            "Gemini API",
            f"all {len(pool)} key(s) are rate-limited right now. They are valid; "
            f"the free daily quota resets at midnight US Pacific.",
        )
    else:
        record(
            "FAIL",
            "Gemini API",
            f"no key reached the model. Rate-limited: "
            f"{', '.join(map(str, limited)) or 'none'}. Broken: "
            f"{', '.join(broken) or 'none'}.",
        )


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
# 6. Tavily web search  (phase 4)
# ----------------------------------------------------------------------
def check_tavily(config: dict[str, str]) -> None:
    pool = _pool(config, "TAVILY_API_KEY")
    if not pool:
        status = "FAIL" if CURRENT_PHASE >= 4 else "SKIP"
        record(status, "Tavily search",
               "No TAVILY_API_KEY. web_search cannot run. Free key at tavily.com")
        return

    import requests

    working, dead = [], []
    for i, key in enumerate(pool, 1):
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": "test", "max_results": 1},
                timeout=20,
            )
            (working if resp.status_code == 200 else dead).append(
                i if resp.status_code == 200 else f"{i} (HTTP {resp.status_code})")
        except Exception as exc:
            dead.append(f"{i} ({type(exc).__name__})")

    if working:
        detail = f"{len(working)} of {len(pool)} key(s) answering"
        if dead:
            detail += f"; not working: {', '.join(str(d) for d in dead)}"
        record("PASS", "Tavily search", detail)
    else:
        record("FAIL", "Tavily search",
               f"none of {len(pool)} key(s) answered: {', '.join(str(d) for d in dead)}")


# ----------------------------------------------------------------------
# 7. YouTube Data API  (phase 8, optional)
# ----------------------------------------------------------------------
def check_youtube(config: dict[str, str]) -> None:
    key = (config.get("YOUTUBE_API_KEY") or "").strip()
    if not key:
        record("SKIP", "YouTube API",
               "No YOUTUBE_API_KEY. analyze_youtube_video can still WATCH videos "
               "(that uses the Gemini key); only its search falls back to Tavily.")
        return

    import requests

    try:
        # videos.list costs 1 unit of the 10,000-per-day quota.
        # search.list, which the tool uses, costs 100 - so this check is
        # deliberately not the same call.
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "id", "id": "jNQXAC9IVRw", "key": key},
            timeout=15,
        )
    except Exception as exc:
        record("FAIL", "YouTube API", f"{type(exc).__name__}: {exc}")
        return

    if resp.status_code == 200:
        record("PASS", "YouTube API",
               "key accepted; ~100 searches a day on the free quota")
        return

    body = resp.json().get("error", {}) if resp.headers.get(
        "Content-Type", "").startswith("application/json") else {}
    reasons = {e.get("reason") for e in body.get("errors", [])}
    hint = ""
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in str(body):
        hint = (" -> the key is fine but YouTube Data API v3 is not enabled on "
                "its Google Cloud project. Enable it in the console, then wait "
                "a minute.")
    elif "keyInvalid" in reasons or "API_KEY_INVALID" in str(body):
        hint = " -> the key itself is wrong; make a new one in the Cloud console."
    elif "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
        hint = " -> valid, but today's quota is spent. It resets at midnight Pacific."
    elif "ipRefererBlocked" in reasons or "API_KEY_HTTP_REFERRER_BLOCKED" in str(body):
        hint = (" -> the key has an application restriction. A server key must be "
                "unrestricted or restricted by IP, not by HTTP referrer.")
    record("FAIL", "YouTube API",
           f"HTTP {resp.status_code}: {body.get('message', resp.text[:120])}{hint}")


# ----------------------------------------------------------------------
# 8. Outbound email  (phase 8, optional)
# ----------------------------------------------------------------------
def check_email(config: dict[str, str]) -> None:
    """Log in to the mail server without sending anything.

    An SMTP login is the whole of what send_self_email needs, so proving it
    here means the tool will work - and a test email to yourself is not
    something a check should send on its own.
    """
    user = (config.get("EMAIL_USER") or "").strip()
    password = (config.get("EMAIL_PASS") or "").strip()
    if not user or not password:
        record("SKIP", "Email (SMTP)",
               "EMAIL_USER / EMAIL_PASS not set. send_self_email will tell the "
               "user it is unconfigured. EMAIL_PASS must be a Google App "
               "Password (Google Account > Security > 2-Step Verification > "
               "App passwords), not the account password.")
        return

    host = (config.get("SMTP_HOST") or "smtp.gmail.com").strip()
    try:
        port = int((config.get("SMTP_PORT") or "465").strip())
    except ValueError:
        port = 465

    import re
    import smtplib

    # A Google App Password is exactly sixteen lowercase letters, usually
    # shown in four groups of four. Anything else against Gmail is almost
    # certainly the account password, which Gmail refuses over SMTP.
    #
    # This is checked BEFORE connecting, deliberately. Trying it anyway
    # spends a failed sign-in against a real Google account, which is how
    # you collect "suspicious sign-in attempt" mail and, if repeated, a
    # temporary block. There is nothing to learn from an attempt whose
    # outcome is already known.
    if "gmail" in host or "google" in host:
        compact = re.sub(r"\s+", "", password)
        if not re.fullmatch(r"[a-z]{16}", compact):
            record(
                "FAIL",
                "Email (SMTP)",
                f"EMAIL_PASS is {len(compact)} character(s) and does not look like "
                f"a Google App Password, which is exactly 16 lowercase letters. "
                f"Gmail refuses account passwords over SMTP, so this would fail. "
                f"Make one at Google Account > Security > 2-Step Verification "
                f"(turn it on first) > App passwords, and paste it without spaces. "
                f"No sign-in was attempted.",
            )
            return

    try:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            smtp.login(user, password)
        record("PASS", "Email (SMTP)", f"logged in to {host}:{port} as {user}")
    except smtplib.SMTPAuthenticationError as exc:
        detail = str(getattr(exc, "smtp_error", b"") or "")
        hint = (" -> Gmail refuses account passwords over SMTP. Use an App "
                "Password: Google Account > Security > 2-Step Verification "
                "(must be on) > App passwords. Paste it with no spaces."
                if "gmail" in host else "")
        record("FAIL", "Email (SMTP)", f"login rejected by {host}. {detail[:120]}{hint}")
    except Exception as exc:
        record("FAIL", "Email (SMTP)",
               f"{type(exc).__name__} talking to {host}:{port}: {str(exc)[:120]}")


# ----------------------------------------------------------------------
def main() -> int:
    print(f"\n  Stellar environment check {DIM}(phase {CURRENT_PHASE}){RESET}\n")

    check_python()
    config = check_config()
    check_gemini(config)
    check_redis(config)
    check_docker()
    check_tavily(config)
    check_youtube(config)
    check_email(config)

    failed = [name for status, name, _ in results if status == "FAIL"]
    skipped = [name for status, name, _ in results if status == "SKIP"]
    optional = [n for n in skipped if n in OPTIONAL]
    deferred = [n for n in skipped if n not in OPTIONAL]

    print()
    if failed:
        print(f"  {RED}{len(failed)} check(s) failed:{RESET} {', '.join(failed)}")
        print(f"  {DIM}Everything above phase {CURRENT_PHASE} is advisory.{RESET}\n")
        return 1

    msg = f"  {GREEN}Environment ready.{RESET}"
    if deferred:
        msg += f" {DIM}({len(deferred)} deferred: {', '.join(deferred)}){RESET}"
    if optional:
        msg += f" {DIM}({len(optional)} optional, not set up: {', '.join(optional)}){RESET}"
    print(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
