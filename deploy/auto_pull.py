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
     with the password from REDIS_URL in keys.env). If port 5000 is held by
     a stale copy of this app (python3 app.py, flask run, an orphaned
     Gunicorn), that is stopped first; anything else on the port is only
     named in the log, never killed.
  2. origin/main has a commit this copy lacks: pull it, check the Python
     still compiles, and upgrade Gunicorn in place: SIGUSR2 starts a second
     master with workers on the new code, and only once it has had time to
     load is the old master sent SIGTERM (it finishes its requests, then
     exits). SIGHUP was used first, but it stops the old workers before the
     new ones have loaded the app: seen live as a few seconds of 502 on
     every deploy. If the new code does not compile, or the new master
     dies while loading, the old one keeps serving.

Local edits to tracked files that block a pull are stashed (git stash), not
thrown away. When a pull changes this script, it restarts into the new
version. Only one copy runs at a time (a lock file). Its log is
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
# How long a new Gunicorn gets to import the app before the old one goes.
WARMUP = 15
EVERY = 30
BRANCH = "main"
PORT = 5000
GUNICORN = ["gunicorn", "-k", "gthread", "--workers", "2", "--threads", "10",
            "--timeout", "3600", "--bind", "0.0.0.0:5000", "app:create_app()"]
COMPILE = ["app.py", "routing.py", "chess_ui.py", "chess_engine.py", "tavily.py",
           "deploy/auto_pull.py"]
# What a stale copy of this app looks like on the port (seen live: after a
# container restart something else held 5000 and every Gunicorn start failed).
OUR_SERVERS = ("app.py", "flask", "create_app", "gunicorn")
_children: list[subprocess.Popen] = []
_last_note = [""]


def note(text: str) -> None:
    """log(), but not the same line every 30 seconds."""
    if text != _last_note[0]:
        _last_note[0] = text
        log(text)


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


def port_holders(port: int) -> list[tuple[int, str]]:
    """(pid, command line) of each process listening on the port, from
    /proc (the image has no ss, lsof or fuser)."""
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in Path(table).read_text().splitlines()[1:]:
                f = line.split()
                if f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
                    inodes.add(f[9])
        except (OSError, IndexError, ValueError):
            pass
    found = []
    for pid, _, cmd in processes():
        if pid == os.getpid():
            continue
        try:
            links = [os.readlink(f"/proc/{pid}/fd/{fd}") for fd in os.listdir(f"/proc/{pid}/fd")]
        except OSError:
            continue
        if any(link.startswith("socket:[") and link[8:-1] in inodes for link in links):
            found.append((pid, cmd))
    return found


def free_port() -> bool:
    """True when Gunicorn can bind the port: nothing holds it, or what did
    was a stale copy of this app and has been stopped."""
    holders = port_holders(PORT)
    others = [(p, c) for p, c in holders if not any(k in c for k in OUR_SERVERS)]
    if others:
        note(f"Port {PORT} is held by pid {others[0][0]} ({others[0][1][:160]}), which is not "
             "this app, so it is left alone and Gunicorn cannot start")
        return False
    for pid, cmd in holders:
        log(f"Port {PORT} was held by a stale server, pid {pid} ({cmd[:160]}): stopping it")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(10):
        if not holders or not port_holders(PORT):
            return True
        time.sleep(1)
    for pid, _ in port_holders(PORT):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(1)
    return not port_holders(PORT)


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
    with (ROOT / "app.log").open("a") as out:
        _children.append(subprocess.Popen(GUNICORN, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, start_new_session=True))
    note(f"Gunicorn was not running: started it on port {PORT}")


def reap() -> None:
    """Collect Gunicorns this script started that have exited, so they do
    not stay behind as zombies."""
    for child in list(_children):
        if child.poll() is not None:
            _children.remove(child)


def alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def upgrade_gunicorn(master: int) -> None:
    """Move Gunicorn onto the new code with no gap in service."""
    os.kill(master, signal.SIGUSR2)
    new = None
    for _ in range(30):
        time.sleep(1)
        procs = processes()
        parents = {ppid for _, ppid, _ in procs}
        # The new master is the old master's child that has children of its own.
        new = next((pid for pid, ppid, cmd in procs
                    if ppid == master and "gunicorn" in cmd and pid in parents), None)
        if new:
            break
    if not new:
        log(f"Gunicorn (pid {master}) did not start a new master; it keeps serving the old code")
        return
    time.sleep(WARMUP)
    if not alive(new):
        log(f"The new Gunicorn (pid {new}) died while loading; the old one keeps serving (see app.log)")
        return
    os.kill(master, signal.SIGTERM)
    log(f"Upgraded Gunicorn onto the new code: pid {new} serves, old pid {master} finishing its requests")


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
        upgrade_gunicorn(master)
    if "deploy/auto_pull.py" in changed and "--once" not in sys.argv:
        log("auto_pull itself changed: restarting into the new version")
        # The lock file was opened close-on-exec, so the new copy can take it.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def check() -> None:
    reap()
    if not redis_running():
        start_redis()
    update()
    if not gunicorn_master() and free_port():
        start_gunicorn()
    elif gunicorn_master():
        _last_note[0] = ""


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
