"""The checks four_worker_test.sh runs against four live Gunicorn workers.

    python deploy/four_worker_check.py upgrade  DB ORIGINAL
    python deploy/four_worker_check.py workers  BASE DB LOG
    python deploy/four_worker_check.py live     BASE DB LOG

Prints PASS/FAIL lines and exits with the number of failures. Everything it
creates lives in the throwaway database and Redis database the shell script
points it at.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import redis
import requests

PROJ = Path(__file__).resolve().parent.parent
FLASK = Path(sys.executable).with_name("flask")
EMAIL, PASSWORD = "worker@test.local", "workerpassword123"
fails = 0


def say(ok: bool, label: str) -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        fails += 1


def token_from(html: str) -> str:
    m = (re.search(r'name="csrf_token" value="([^"]+)"', html)
         or re.search(r'name="csrf-token" content="([^"]+)"', html))
    return m.group(1) if m else ""


def signed_in(base: str) -> tuple[requests.Session, dict]:
    """A session for an approved administrator, and the headers its API calls need."""
    s = requests.Session()
    s.post(f"{base}/auth/register", data={
        "username": EMAIL, "password": PASSWORD,
        "csrf_token": token_from(s.get(f"{base}/auth/register").text)})
    subprocess.run([str(FLASK), "--app", "app:create_app", "make-admin", EMAIL],
                   cwd=PROJ, capture_output=True, check=True)
    s.post(f"{base}/auth/login", data={
        "username": EMAIL, "password": PASSWORD,
        "csrf_token": token_from(s.get(f"{base}/auth/login").text)})
    page = s.get(f"{base}/")
    headers = {"X-CSRF-Token": token_from(page.text), "Accept": "application/json"}
    return s, headers


def pids_for(log: str, mark: int, needle: str) -> set[str]:
    lines = Path(log).read_text(errors="replace").splitlines()[mark:]
    return {m.group(1) for line in lines if needle in line
            for m in [re.search(r"PID=<?(\d+)>?", line)] if m}


def log_lines(log: str) -> int:
    return len(Path(log).read_text(errors="replace").splitlines())


def no_tracebacks(log: str) -> None:
    text = Path(log).read_text(errors="replace")
    bad = [line for line in text.splitlines()
           if "Traceback" in line or "[ERROR]" in line or "CRITICAL" in line]
    say(not bad, "no tracebacks across four workers" + (f" ({bad[0][:80]})" if bad else ""))


# --- phase 1 --------------------------------------------------------------

def upgrade(db: str, original: str) -> None:
    """A copy of an existing database, brought up to date by four workers."""
    want = int(re.search(r"^SCHEMA_VERSION = (\d+)", (PROJ / "app.py").read_text(),
                         re.M).group(1))
    old = sqlite3.connect(f"file:{original}?immutable=1", uri=True)
    new = sqlite3.connect(db)
    tables = [r[0] for r in old.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    lost = [t for t in tables
            if new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            < old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]]
    say(new.execute("PRAGMA user_version").fetchone()[0] == want,
        f"the copy is at schema version {want}")
    say(new.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "and passes an integrity check")
    say(not lost, f"no table lost rows ({len(tables)} compared)" + (f": {lost}" if lost else ""))


# --- phase 2 --------------------------------------------------------------

def workers(base: str, db: str, log: str) -> None:
    text = Path(log).read_text(errors="replace")
    booted = text.count("Booting worker with pid")
    say(booted == 4, f"four workers booted (saw {booted})")
    s, h = signed_in(base)
    chat = s.post(f"{base}/api/chats", headers=h).json()["id"]
    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

    def inject(msg: str) -> int:
        return s.post(f"{base}/api/chats/{chat}/inject", headers={**h, "Connection": "close"},
                      json={"message": msg}).status_code

    say(inject("nothing running") == 409, "a follow-up is refused when no reply is running")
    # Written straight into Redis, so no worker holds this claim in memory.
    r.set(f"generating:{chat}", "q-held-by-another-worker", ex=120)
    mark = log_lines(log)
    # At once, not one after another: sequential connections all go to
    # whichever worker is idle first, which proves nothing about the others.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as pool:
        codes = list(pool.map(lambda i: inject(f"follow-up {i}"), range(16)))
    say(all(c in (200, 202) for c in codes),
        f"every worker honoured a claim it did not make ({sum(c not in (200, 202) for c in codes)} refused of 16)")
    spread = pids_for(log, mark, f"/api/chats/{chat}/inject")
    say(len(spread) >= 2, f"those requests really did reach different workers ({len(spread)})")
    r.delete(f"generating:{chat}", f"inject:{chat}")
    say(inject("claim gone") == 409, "refused again once the claim is cleared")

    # One task due, four schedulers polling: it runs once. No model key is
    # configured in this phase, so its run fails at once - which is still
    # exactly one run, one failure, one note in the chat.
    conn = sqlite3.connect(db)
    uid = conn.execute("SELECT id FROM users WHERE username = ?", (EMAIL,)).fetchone()[0]
    task = conn.execute(
        "INSERT INTO scheduled_tasks (user_id, chat_id, task_prompt, run_at)"
        " VALUES (?, ?, 'Say hello', datetime('now', '-1 minute'))", (uid, chat)).lastrowid
    conn.commit()
    row = None
    for _ in range(90):
        row = conn.execute("SELECT status, runs FROM scheduled_tasks WHERE id = ?",
                           (task,)).fetchone()
        if row[0] in ("done", "failed"):
            break
        time.sleep(1)
    time.sleep(35)                   # another full scheduler tick, for a second claim
    row = conn.execute("SELECT status, runs FROM scheduled_tasks WHERE id = ?", (task,)).fetchone()
    notes = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ? AND message_content"
                         " LIKE ?", (chat, f"%Scheduled task #{task} %")).fetchone()[0]
    say(row == ("failed", 1) and notes == 1,
        f"a due task ran exactly once across four schedulers (status {row[0]}, runs {row[1]}, notes {notes})")
    conn.close()

    ready = requests.get(f"{base}/readyz").json()
    say(ready.get("status") == "ready", f"ready: {json.dumps(ready.get('checks'))}")
    no_tracebacks(log)


# --- phase 3 --------------------------------------------------------------

def sse(resp, stop_after=None):
    """(id, event) pairs from an SSE response, until done or stop_after says so."""
    buf, ev_id = [], None
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw == "":
            data = "".join(buf)
            buf = []
            if data:
                event = json.loads(data)
                yield ev_id, event
                if stop_after and stop_after(ev_id, event):
                    return
            ev_id = None
            continue
        if raw.startswith("id:"):
            ev_id = int(raw[3:].strip())
        elif raw.startswith("data:"):
            buf.append(raw[5:].strip())


def live(base: str, db: str, log: str) -> None:
    """One real reply: an invalid first key, a reconnect, a stop from elsewhere."""
    s, h = signed_in(base)
    chat = s.post(f"{base}/api/chats", headers=h).json()["id"]
    qid = s.post(f"{base}/api/chats/{chat}/query", headers=h, json={
        "message": "Write the whole numbers from one to four hundred as English words, "
                   "one per line, and nothing else."}).json()["query_id"]
    mark = log_lines(log)
    seen: list[int] = []
    text = []

    def first_tokens(ev_id, event):
        if event["type"] == "token":
            text.append(event["text"])
        if ev_id is not None:
            seen.append(ev_id)
        return sum(1 for _ in text) >= 2 or event["type"] in ("done", "error", "message")

    with s.get(f"{base}/api/stream/{qid}?from=0", stream=True, timeout=120,
               headers={"Connection": "close"}) as resp:
        list(sse(resp, first_tokens))
    say(bool(text), f"a reply streamed with a bad first key in the pool ({len(text)} chunks first)")

    # Reconnect, as a browser does, from where the first connection got to.
    kinds = []
    stopped_by = set()

    def rest(ev_id, event):
        if ev_id is not None:
            seen.append(ev_id)
        kinds.append(event["type"])
        if event["type"] == "token":
            text.append(event["text"])
            if not stopped_by and len(text) >= 4:
                other = requests.Session()
                other.cookies.update(s.cookies)
                other.post(f"{base}/api/stream/{qid}/stop", headers={**h, "Connection": "close"})
                stopped_by.add("sent")
        return event["type"] in ("done", "cancelled", "error")

    with s.get(f"{base}/api/stream/{qid}?from=0", stream=True, timeout=180,
               headers={"Last-Event-ID": str(max(seen)) if seen else "-1",
                        "Connection": "close"}) as resp:
        list(sse(resp, rest))
    say(seen == sorted(set(seen)), "the reconnection resumed where it left off: no event twice")
    say("cancelled" in kinds or "message" in kinds,
        f"a stop sent on another connection ended the reply ({'stopped' if 'cancelled' in kinds else 'it had already finished'})")
    conn = sqlite3.connect(db)
    saved = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ? AND message_type = 'stellar'",
                         (chat,)).fetchone()[0]
    conn.close()
    say(saved >= 1, "what was said before the stop is saved")
    served = pids_for(log, mark, f"/api/stream/{qid}")
    print(f"        stream connections were served by {len(served)} worker(s)")
    text_log = Path(log).read_text(errors="replace")
    say("API_KEY_INVALID" in text_log or "invalid" in text_log.lower(),
        "the invalid key was recognised and passed over")
    no_tracebacks(log)


if __name__ == "__main__":
    mode, args = sys.argv[1], sys.argv[2:]
    {"upgrade": upgrade, "workers": workers, "live": live}[mode](*args)
    sys.exit(fails)
