"""Back up Stellar's database while it is running, and keep two weeks.

    .venv/bin/python deploy/backup_db.py            # one backup now
    .venv/bin/python deploy/backup_db.py --check    # and prove it opens

Uses SQLite's online backup API, which copies a consistent snapshot even
while four workers are writing. Copying the .db file with cp does not: in
WAL mode the latest writes are still in the -wal file, and a copy taken
mid-write can be torn.

Run daily by deploy/stellar-backup.timer. It covers the database only;
keys.env and the uploads, outputs and deployments folders are backed up as
files (see the deploy guide).
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP_DAYS = 14


def database_path() -> Path:
    name = os.environ.get("DATABASE_NAME", "").strip() or "stellar_local.db"
    return (ROOT / name).resolve()


def backup(dest_dir: Path) -> Path:
    src = database_path()
    if not src.exists():
        raise SystemExit(f"No database at {src}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"stellar-{stamp}.db"
    live, copy = sqlite3.connect(src), sqlite3.connect(dest)
    try:
        live.backup(copy)
    finally:
        copy.close()
        live.close()
    os.chmod(dest, 0o600)          # it holds password hashes and every chat
    return dest


def check(path: Path) -> None:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    finally:
        conn.close()
    if ok != "ok":
        raise SystemExit(f"{path.name}: integrity check failed: {ok}")
    print(f"{path.name}: integrity ok, {users} users, {chats} chats")


def prune(dest_dir: Path) -> int:
    cutoff = dt.datetime.now() - dt.timedelta(days=KEEP_DAYS)
    removed = 0
    for old in dest_dir.glob("stellar-*.db"):
        if dt.datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
            old.unlink()
            removed += 1
    return removed


def main() -> None:
    dest_dir = ROOT / "backups"
    made = backup(dest_dir)
    print(f"Backed up to {made}")
    if "--check" in sys.argv:
        check(made)
    gone = prune(dest_dir)
    if gone:
        print(f"Removed {gone} backups older than {KEEP_DAYS} days")


if __name__ == "__main__":
    main()
