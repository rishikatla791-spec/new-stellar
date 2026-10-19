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

Relay mode (deploy/relay.py): when keys.env has RELAY_SECRET, this copy only
fronts for the runner, a machine with Docker that serves the site through
it. Gunicorn then moves to 127.0.0.1:5002 (it serves the landing pages while
the runner is away) and the relay takes port 5000. Remove RELAY_SECRET and
it moves back. On the runner itself this script runs with
STELLAR_RELAY_ROLE=runner (and STELLAR_BIND=127.0.0.1), and serves as usual.
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
LOCAL_PORT = 5002           # Gunicorn's port in relay mode
ROLE = os.environ.get("STELLAR_RELAY_ROLE", "")         # "runner" on the machine with Docker
BIND = os.environ.get("STELLAR_BIND", "0.0.0.0")
RELAY = ["deploy/relay.py", "front"]
COMPILE = ["app.py", "routing.py", "chess_ui.py", "chess_engine.py", "tavily.py",
           "agent_workflow.py", "build_quality.py", "project_review.py",
           "deploy/auto_pull.py", "deploy/relay.py"]
# What a stale copy of this app looks like on the port (seen live: after a
# container restart something else held 5000 and every Gunicorn start failed).
OUR_SERVERS = ("app.py", "flask", "create_app", "gunicorn", "relay.py")


def gunicorn_cmd(bind: str) -> list[str]:
    return ["gunicorn", "-k", "gthread", "--workers", "2", "--threads", "10",
            "--timeout", "3600", "--bind", bind, "app:create_app()"]


def relay_mode() -> bool:
    """keys.env has RELAY_SECRET, and this is not the runner itself."""
    return ROLE != "runner" and bool(keys_value("RELAY_SECRET"))


def gunicorn_bind() -> str:
    return f"127.0.0.1:{LOCAL_PORT}" if relay_mode() else f"{BIND}:{PORT}"
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


def free_port(port: int = PORT) -> bool:
    """True when the port can be bound: nothing holds it, or what did was a
    stale copy of this app and has been stopped."""
    holders = port_holders(port)
    others = [(p, c) for p, c in holders if not any(k in c for k in OUR_SERVERS)]
    if others:
        note(f"Port {port} is held by pid {others[0][0]} ({others[0][1][:160]}), which is not "
             "this app, so it is left alone and nothing of ours can start there")
        return False
    for pid, cmd in holders:
        log(f"Port {port} was held by a stale server, pid {pid} ({cmd[:160]}): stopping it")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(10):
        if not holders or not port_holders(port):
            return True
        time.sleep(1)
    for pid, _ in port_holders(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(1)
    return not port_holders(port)


def redis_running() -> bool:
    return any("redis-server" in cmd for _, _, cmd in processes())


def keys_value(name: str) -> str:
    """One setting from keys.env, read fresh (it can be edited live)."""
    for line in (ROOT / "keys.env").read_text().splitlines() if (ROOT / "keys.env").exists() else []:
        if line.strip().startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def redis_password() -> str:
    url = keys_value("REDIS_URL")
    return (urlsplit(url).password or "") if url else ""


def start_redis() -> None:
    """Redis on the port REDIS_URL names, for this machine only (seen on a
    laptop: Docker Desktop already held 6379, so the runner uses another)."""
    url = keys_value("REDIS_URL")
    port = (urlsplit(url).port if url else None) or 6379
    args = ["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--daemonize", "yes"]
    password = redis_password()
    if password:
        args[1:1] = ["--requirepass", password]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if r.returncode:
        note(f"Redis would not start on port {port}: " + (r.stderr or r.stdout).strip()[-300:])
    else:
        log(f"Redis was not running: started it on 127.0.0.1:{port}")


def start_gunicorn(bind: str) -> None:
    # In relay mode this Gunicorn is the front's: it runs no scheduled tasks
    # (the runner does) - see app.py, STELLAR_RELAY_ROLE.
    child_env = {**os.environ, "STELLAR_RELAY_ROLE": "front"} if relay_mode() else None
    with (ROOT / "app.log").open("a") as out:
        _children.append(subprocess.Popen(gunicorn_cmd(bind), cwd=ROOT, stdout=out,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          start_new_session=True, env=child_env))
    note(f"Gunicorn was not running: started it on {bind}")


def master_bind(pid: int) -> str:
    """The --bind a running Gunicorn master was started with."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return args[args.index(b"--bind") + 1].decode()
    except (OSError, ValueError, IndexError):
        return ""


def stop_and_wait(pid: int, seconds: int = 20) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(seconds):
        if not alive(pid):
            return
        time.sleep(1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def relay_pid() -> int | None:
    """This checkout's relay front (another checkout's, on the same machine,
    is not ours to stop)."""
    for pid, _, cmd in processes():
        if " ".join(RELAY) in cmd and pid != os.getpid():
            try:
                if Path(os.readlink(f"/proc/{pid}/cwd")).resolve() == ROOT:
                    return pid
            except OSError:
                continue
    return None


def start_relay() -> None:
    with (ROOT / "relay.log").open("a") as out:
        _children.append(subprocess.Popen([sys.executable, *RELAY], cwd=ROOT, stdout=out,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          start_new_session=True))
    note(f"Relay mode: the relay front was not running; started it on port {PORT}")


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
    relay = relay_pid()
    if relay and "deploy/relay.py" in changed:
        log("relay.py changed: restarting the relay front onto it")
        stop_and_wait(relay, 10)
        if free_port(PORT):
            start_relay()
    if "deploy/auto_pull.py" in changed and "--once" not in sys.argv:
        log("auto_pull itself changed: restarting into the new version")
        # The lock file was opened close-on-exec, so the new copy can take it.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def check() -> None:
    reap()
    if not redis_running():
        start_redis()
    update()
    want = gunicorn_bind()
    master = gunicorn_master()
    if master and master_bind(master) not in ("", want):
        log(f"Gunicorn moves from {master_bind(master)} to {want} ("
            + ("relay mode: RELAY_SECRET is set)" if relay_mode() else "serving directly again)"))
        stop_and_wait(master)
        master = None
    relay = relay_pid()
    if relay_mode():
        if not master and free_port(LOCAL_PORT):
            start_gunicorn(want)
        if not relay and free_port(PORT):
            start_relay()
    else:
        if relay:
            log("RELAY_SECRET is gone: stopping the relay front, serving directly again")
            stop_and_wait(relay, 10)
        if not master and free_port(PORT):
            start_gunicorn(want)
    if master:
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
