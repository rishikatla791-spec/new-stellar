"""Keep the live copy of Stellar current and running, inside main Stellar.

    cd /app && setsid nohup python3 deploy/auto_pull.py > /dev/null 2>&1 < /dev/null &
    python3 deploy/auto_pull.py --once        # one check, printed, then exit

setsid matters: started from the console without it, this script (and a
Gunicorn started there by hand) belong to the console's session, and are
killed when the console closes. Let this script start Gunicorn: it starts
it in a session of its own.

new-stellar.stellarai.site runs inside a main-Stellar container whose own
command is `sleep infinity`: Redis and Gunicorn were started by hand, and
nothing restarts them or picks up new commits. This script does both, every
30 seconds:

  1. Redis and Gunicorn are running; if either is not, it is started (Redis
     with the password from REDIS_URL in keys.env).
  2. origin/main has a commit this copy lacks: pull it, check the Python
     still compiles, and send Gunicorn SIGHUP. Gunicorn then starts new
     workers, which import the new code (the app is not preloaded), and
     retires the old ones once their requests finish: no downtime. If the
     new code does not compile, the old workers keep serving.

Local edits to tracked files that block a pull are stashed (git stash), not
thrown away. Only one copy runs at a time (a lock file). Its log is
/app/auto_pull.log.

It does not survive the container being restarted, since nothing in the
container starts it; start it again with the first command above.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "auto_pull.log"
LOCK = Path("/tmp/stellar_auto_pull.lock")
EVERY = 30
BRANCH = "main"
GUNICORN = ["gunicorn", "-k", "gthread", "--workers", "2", "--threads", "10",
            "--timeout", "3600", "--bind", "0.0.0.0:5000", "app:create_app()"]
COMPILE = ["app.py", "routing.py", "chess_ui.py", "chess_engine.py", "tavily.py"]


def log(text: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + text
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def processes() -> list[tuple[int, int, str]]:
    """(pid, parent pid, command line) of every process, from /proc."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            stat = Path(f"/proc/{pid}/stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        found.append((int(pid), ppid, cmd.strip()))
    return found


def gunicorn_master() -> int | None:
    """The Gunicorn process whose parent is not itself Gunicorn."""
    procs = processes()
    pids = {pid for pid, _, cmd in procs if "gunicorn" in cmd and "auto_pull" not in cmd}
    for pid, ppid, _ in procs:
        if pid in pids and ppid not in pids:
            return pid
    return None


def redis_running() -> bool:
    return any("redis-server" in cmd for _, _, cmd in processes())


def redis_password() -> str:
    for line in (ROOT / "keys.env").read_text().splitlines() if (ROOT / "keys.env").exists() else []:
        if line.strip().startswith("REDIS_URL="):
            return urlsplit(line.split("=", 1)[1].strip().strip("'\"")).password or ""
    return ""


def start_redis() -> None:
    args = ["redis-server", "--daemonize", "yes"]
    password = redis_password()
    if password:
        args[1:1] = ["--requirepass", password]
    subprocess.run(args, cwd=ROOT, capture_output=True, timeout=30)
    log("Redis was not running: started it")


def start_gunicorn() -> None:
    out = (ROOT / "app.log").open("a")
    subprocess.Popen(GUNICORN, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    log("Gunicorn was not running: started it on port 5000")


def compiles() -> bool:
    files = [f for f in COMPILE if (ROOT / f).exists()]
    r = subprocess.run([sys.executable, "-m", "py_compile", *files], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        log("The new code does not compile; the old workers keep serving:\n" + r.stderr.strip()[-1500:])
    return r.returncode == 0


def update() -> None:
    """Pull origin/main if it moved, and reload Gunicorn onto it."""
    if git("fetch", "--quiet", "origin", BRANCH).returncode:
        return                  # offline for now; try next time
    head, remote = git("rev-parse", "HEAD").stdout.strip(), git("rev-parse", f"origin/{BRANCH}").stdout.strip()
    if not remote or head == remote:
        return
    changed = git("diff", "--name-only", head, remote).stdout.split()
    pulled = git("pull", "--ff-only", "--quiet", "origin", BRANCH)
    if pulled.returncode:
        git("stash", "push", "--quiet", "-m", f"auto_pull {time.strftime('%Y-%m-%d %H:%M')}")
        pulled = git("pull", "--ff-only", "--quiet", "origin", BRANCH)
        if pulled.returncode:
            log("Pull failed even after stashing local edits: " + pulled.stderr.strip()[-500:])
            return
        log("Local edits blocked the pull; they are in `git stash list`")
    log(f"Pulled {head[:7]} -> {remote[:7]} ({len(changed)} files)")
    if "requirements.txt" in changed:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
        log("requirements.txt changed: " + ("installed" if r.returncode == 0 else
                                            "install FAILED: " + r.stderr.strip()[-500:]))
    if not compiles():
        return
    master = gunicorn_master()
    if master:
        os.kill(master, signal.SIGHUP)
        log(f"Reloaded Gunicorn (pid {master}) onto the new code")


def check() -> None:
    if not redis_running():
        start_redis()
    update()
    if not gunicorn_master():
        start_gunicorn()


def main() -> None:
    if "--once" in sys.argv:
        check()
        return
    lock = LOCK.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("auto_pull is already running.")
        return
    log(f"auto_pull started (pid {os.getpid()}), checking origin/{BRANCH} every {EVERY}s")
    while True:
        try:
            check()
        except Exception as exc:        # keep watching whatever went wrong
            log(f"Check failed: {type(exc).__name__}: {exc}")
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
