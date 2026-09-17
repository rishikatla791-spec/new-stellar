"""SQLite access layer.

One connection per request, stored on Flask's request-scoped ``g`` object
and closed automatically when the request ends.

Why per-request and not one global connection: Stellar runs under Gunicorn
with threaded workers, and a single sqlite3 connection is not safe to share
across threads. Per-request connections sidestep that entirely, and SQLite
connections are cheap to open (no network handshake, no auth).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
from flask import current_app, g


def get_db() -> sqlite3.Connection:
    """Return this request's database connection, opening it if needed."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            # Let SQLite itself wait when another writer holds the lock,
            # instead of raising "database is locked" immediately.
            timeout=5.0,
            # Return native datetime objects as text; we format in SQL.
            detect_types=0,
        )
        # Rows behave like dicts: row["message_content"] instead of row[3].
        # Worth it purely so queries stay readable when columns are added.
        g.db.row_factory = sqlite3.Row
        _apply_pragmas(g.db)

    return g.db


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Configure a fresh connection.

    PRAGMAs are per-connection, not stored in the database file (with the
    exception of journal_mode, which is persistent). They must therefore be
    re-applied every time a connection is opened.
    """
    # WAL: readers never block the writer and the writer never blocks
    # readers. Essential once SSE streams hold long-running reads open while
    # the agent loop writes messages. This setting persists in the file.
    conn.execute("PRAGMA journal_mode = WAL")

    # Wait up to 5s for a lock before raising OperationalError. Under
    # concurrent workers the alternative is spurious "database is locked"
    # errors during ordinary traffic.
    conn.execute("PRAGMA busy_timeout = 5000")

    # SQLite ships with foreign key enforcement OFF for backwards
    # compatibility. Without this, ON DELETE CASCADE is silently ignored and
    # deleting a chat leaves its messages orphaned forever.
    conn.execute("PRAGMA foreign_keys = ON")

    # NORMAL means the OS is trusted to flush to disk. Combined with WAL
    # this is still crash-safe (you can lose the last transaction on power
    # loss, never the database), and it is markedly faster than FULL.
    conn.execute("PRAGMA synchronous = NORMAL")


def close_db(exc: BaseException | None = None) -> None:
    """Close the request's connection. Registered as a teardown handler."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Apply schema.sql. Idempotent - safe to run against an existing db."""
    schema = Path(current_app.root_path) / "schema.sql"
    db = get_db()
    db.executescript(schema.read_text(encoding="utf-8"))
    db.commit()


@click.command("init-db")
def init_db_command() -> None:
    """flask init-db - create the database file and tables."""
    init_db()
    click.echo(f"Initialised {current_app.config['DATABASE']}")


def register(app) -> None:
    """Wire this module into an app instance."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
