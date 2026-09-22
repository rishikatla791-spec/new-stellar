"""Stellar - Complete Application (Single-File Architecture).

Contains all subsystems in one cohesive module:
- Database Layer: SQLite with WAL mode, foreign keys, connection lifecycle
- Authentication Layer: Registration, login, session auth, approval gate
- Resumable Streaming: Redis list event queue, two-phase query/stream, SSE
- Phase 3 LLM Engine: Live streaming via Google GenAI SDK
- Phase 4 Tool Loop: Manual function-calling loop with persisted tool calls
- Chat Management: Chat sessions, messages, title generation, and REST API
"""

from __future__ import annotations

import functools
import json
import logging
import sys
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urljoin, quote

import click
from dotenv import load_dotenv
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    flash,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from google import genai
from google.genai import types
import redis
from werkzeug.security import check_password_hash, generate_password_hash

PROJECT_ROOT = Path(__file__).parent

# Load configuration from keys.env early
load_dotenv(PROJECT_ROOT / "keys.env")

logger = logging.getLogger("stellar")

# ---------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------
_TITLE_MAX = 48
STREAM_TTL = 60 * 60          # 1 hour for Redis event stream
# A follow-up typed mid-turn is only meaningful to the turn it was
# typed at. Matches the longest a single turn can block.
INJECT_TTL = 600
QUERY_ARGS_TTL = 60 * 60 * 24  # 24 hours for query registration arguments
POLL_INTERVAL = 0.05          # 50ms Redis polling interval
# Must exceed the longest a tool may block without emitting anything,
# or the browser is told the stream died while the work is still running.
# request_user_interaction and chess_play wait up to INTERACTION_TIMEOUT
# (600s) for a click, and lab_execute up to LAB_MAX_TIMEOUT (600s); at the
# old 120s a user who thought for two minutes over a chess move got
# "Stream timed out." and lost the rest of the turn. Dead clients are
# still detected within KEEPALIVE_INTERVAL, by the write itself failing.
IDLE_TIMEOUT = 900            # give up on a silent stream after 15 minutes
# How often to write a comment frame while a stream is silent. This doubles
# as dead-client detection, so it wants to be short: an abandoned stream
# holds its worker thread and socket until the next write fails.
KEEPALIVE_INTERVAL = 10

DEFAULT_MODEL = "gemini-3-flash-preview"
FALLBACK_MODEL = "gemini-2.5-flash"

# Retries per model before falling through to the next one. Transient
# "Server disconnected" failures are common enough on the free tier that
# without this a normal turn fails outright every so often.
MAX_LLM_ATTEMPTS = 3
LLM_RETRY_BACKOFF = 1.5   # seconds, exponential: 1.0, 1.5, 2.25 ...

# How many times the model may call tools and be asked again within one turn.
# A bound is required, not defensive: a model that misreads a tool result can
# retry the same call forever, and each pass costs a full request.
MAX_TOOL_ITERATIONS = 8

# Tool output is fed straight back into context, so a single large page can
# eat the window. Truncate at the tool, and let read_tool_output (phase 8)
# page through the stored full text when more is genuinely needed.
TOOL_OUTPUT_LIMIT = 12000
# fetch_url keeps this much of a page in the tool_calls row. The model sees
# TOOL_OUTPUT_LIMIT of it and reads the rest through read_tool_output.
FETCH_STORE_LIMIT = 80_000

SYSTEM_INSTRUCTION = (
    "You are Stellar, a capable, sharp, and concise AI assistant. "
    "Respond helpfully and accurately using clean, well-structured Markdown. "
    "Avoid unnecessary conversational filler and focus on direct, high-quality answers."
)

# Appended whenever the interactive tools are available. Written as
# instruction rather than description: the model reads this as its brief for
# how a widget should look and behave, and vague guidance here produces
# exactly the generic purple-gradient widget everyone has seen.
GENERATIVE_UI_GUIDE = """

### BUILDING INTERACTIVE WIDGETS

request_user_interaction renders HTML in the chat and pauses you until the
user acts. Reach for it whenever the next step depends on a person - a game,
a choice between options, a form, confirming a direction before you build
something. A widget beats asking in prose and hoping for a parseable answer.

**Mechanics that are not optional**

- Self-contained: markup, one <style> block, one <script>. No external
  scripts, no CDN libraries, no frameworks. Plain DOM and CSS.
- The script MUST call window.stellar.finish(data) with what the user chose.
  A widget that never calls it can never return, and you will wait for ten
  minutes on a dead button.
- Every widget needs an exit: a Cancel or Close control calling
  window.stellar.finish({exit: true}). Never trap someone in a loop.
- Give immediate feedback on click - disable the control, change its label -
  before calling finish. The wait for your reply is seconds long and an
  unresponsive button invites a second click.
- It renders inside a sandboxed frame on a DARK background. These variables
  are already defined and are the house palette: --bg #14171f, --surface
  #1a1e28, --border #252a36, --text #e6e8ee, --text-dim #8b91a1, --accent
  #6d8cff, --good #4fc79f, --bad #ff6b6b, --font, --mono. Use them.

**Making it good rather than generic**

Aim for something that looks designed, not generated. Specifically:

- No purple-to-blue gradients, no glow, no glassmorphism, no pulsing status
  dots, no "AI Assistant v2.0" headers, no emoji as section markers. These
  are the house style of generated UI and they read as such immediately.
- One accent colour, used sparingly, on the one thing that matters. Everything
  else in neutrals.
- Spacing does the work. Generous padding, consistent gaps, aligned edges.
  Cramped is the most common failure and the easiest to avoid.
- Type: two sizes and two weights is plenty. 400 for text, 500 for emphasis.
  Never bold everything.
- Interactive things must look interactive - a hover state, a cursor change,
  a visible focus ring. Dead-looking buttons get clicked twice.
- Animate only what communicates: a selected square, a card lifting on hover.
  Decoration that moves is noise.
- It must work at 400px wide. The chat column is not a desktop canvas.

**Games**

Build the board properly. A chessboard is an 8x8 CSS grid with real
alternating squares, pieces as Unicode glyphs at a size you can actually see
(36px or more), file and rank labels, a highlight for the selected square and
for legal destinations, and a visible record of the last move. Click a piece,
then click a destination - do not make people type coordinates.

### PLAYING CHESS

This section applies ONLY when the user has asked to play chess. They have
to actually ask - "let's play chess", "give me a game", "rematch", "play
again", or an answer to your own offer of a game. A greeting, a question
about chess, a question about anything else, or an empty message is NOT a
request to play. Never open a board unasked: it takes over the conversation
and the user has to resign to get out of it.

When they have asked, call chess_play. That is then the whole instruction.

It draws the board, takes the user's moves by click, replies instantly, and
runs the game by itself at whatever strength was asked for. Do not build a
board with request_user_interaction, do not call chess_move per move, and do
not track the position yourself - every one of those is slower and worse.

chess_play returns to you only when words are needed: the game ended, the
user asked something about the position, they resigned, or started over.
When it does, talk like a player - name the opening, point out the turning
move, credit a good idea - and if the game is still on, call chess_play
again to resume. When the user asks a question mid-game, answer it in a few
sentences and then resume; the board is waiting for them.

If the user names a level, pick a number: beginner 1400, casual 1700, club
2000, strong 2400, master 2800. Say what you chose. Games have a 10-minute
clock per side by default; pass minutes=0 if they want an untimed game, or
another number if they name one.

chess_move exists for analysis outside a live game - "what is the best move
in this position" - not for playing.
"""


# ---------------------------------------------------------------------
# Database Layer
# ---------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    """Return this request's database connection, opening it if needed."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            timeout=5.0,
            detect_types=0,
        )
        g.db.row_factory = sqlite3.Row
        _apply_pragmas(g.db)
    return g.db


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Configure a fresh SQLite connection with production-safe pragmas."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")


def close_db(exc: BaseException | None = None) -> None:
    """Close the request's connection. Registered as a teardown handler."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Columns added to the core tables after they were first created. SQLite
# can only ADD COLUMN, so every entry must be nullable or carry a constant
# default - and that is exactly why the list is explicit rather than
# derived from schema.sql.
#
# Without this, upgrading a database written by an earlier phase left it
# half-built: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op on an
# existing table, so the new columns never appeared, the CREATE INDEX on
# one of them aborted init_db, and schema_drift() then reported the
# database as perfectly up to date.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("display_name", "TEXT"),
        ("is_approved", "INTEGER NOT NULL DEFAULT 0"),
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TEXT"),
    ],
    "chats": [
        ("name", "TEXT"),
        ("is_temp", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "messages": [
        ("hidden", "INTEGER NOT NULL DEFAULT 0"),
        ("timestamp", "TEXT"),
    ],
    "tool_calls": [
        ("message_id", "INTEGER"),
        ("duration_ms", "INTEGER"),
        ("is_error", "INTEGER NOT NULL DEFAULT 0"),
        ("hidden", "INTEGER NOT NULL DEFAULT 0"),
        ("timestamp", "TEXT"),
    ],
}


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Migrate legacy tables to match current schema expectations."""
    # 0. Core tables, added-column by added-column.
    for table, columns in _ADDED_COLUMNS.items():
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not exists:
            continue
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, ddl in columns:
            if column not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                except sqlite3.OperationalError as exc:
                    # Every Gunicorn worker runs the factory, so four of
                    # them can read "the column is missing" in the same
                    # instant and all four try to add it. Three lose, and
                    # an uncaught loss propagates out of create_app and
                    # into a systemd restart loop. Losing is fine: the
                    # column exists either way.
                    if "duplicate column" not in str(exc).lower():
                        raise
                    logger.info("Schema upgrade: %s.%s added by another worker",
                                table, column)
                else:
                    logger.info("Schema upgrade: added %s.%s", table, column)
        # A timestamp column added to existing rows is NULL, and the
        # sidebar orders by it, so backfill rather than leave the order
        # undefined.
        if table == "chats" and "updated_at" not in have:
            conn.execute("UPDATE chats SET updated_at = COALESCE(created_at, datetime('now'))"
                         " WHERE updated_at IS NULL")
        if "timestamp" in dict(columns) and "timestamp" not in have:
            conn.execute(f"UPDATE {table} SET timestamp = datetime('now')"
                         f" WHERE timestamp IS NULL")

    # 1. scheduled_tasks
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_tasks'"
    ).fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()}
        if "run_at" not in cols:
            count = conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]
            if count == 0:
                conn.execute("DROP TABLE scheduled_tasks")
            else:
                if "execute_at" in cols and "run_at" not in cols:
                    conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN run_at TEXT")
                    conn.execute("UPDATE scheduled_tasks SET run_at = execute_at WHERE run_at IS NULL")
                if "recurring_minutes" in cols and "every_minutes" not in cols:
                    conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN every_minutes INTEGER NOT NULL DEFAULT 0")
                    conn.execute("UPDATE scheduled_tasks SET every_minutes = recurring_minutes WHERE every_minutes = 0")
                if "claimed_at" not in cols:
                    conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN claimed_at TEXT")
                if "runs" not in cols:
                    conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN runs INTEGER NOT NULL DEFAULT 0")
        # Outside the "run_at is missing" branch on purpose: a database
        # migrated by an earlier build has run_at but no lock_id, and
        # run_due_tasks' first statement names lock_id. Without this the
        # scheduler raised "no such column" on every tick and, because
        # that was swallowed, ran nothing and said nothing.
        if "lock_id" not in cols:
            conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN lock_id TEXT")

    # 2. repo_history
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='repo_history'"
    ).fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(repo_history)").fetchall()}
        if "host_port" not in cols:
            conn.execute("ALTER TABLE repo_history ADD COLUMN host_port INTEGER")
        if "subdomain" not in cols:
            conn.execute("ALTER TABLE repo_history ADD COLUMN subdomain TEXT")
        if "files_snapshot" not in cols:
            conn.execute("ALTER TABLE repo_history ADD COLUMN files_snapshot TEXT")

    # ALTER TABLE self-commits, but the UPDATEs that copy legacy values
    # into the new columns do not. Leaving that transaction open meant the
    # copy was rolled back on close: every pre-existing scheduled task
    # ended up with run_at NULL, never matched "run_at <= now", and never
    # fired again - and the repair could not re-run, because run_at now
    # existed.
    conn.commit()


def schema_drift(conn: sqlite3.Connection) -> list[str]:
    """Names in schema.sql that do not exist in this database yet."""
    _migrate_columns(conn)
    sql = (PROJECT_ROOT / "schema.sql").read_text(encoding="utf-8")
    wanted = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    wanted |= set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", sql))
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    return sorted(wanted - have)


def init_db(database_path: str | Path | None = None) -> None:
    """Apply schema.sql idempotently to initialize tables.

    Safe to run against an existing database, and it is run on every start
    for exactly that reason: every statement is IF NOT EXISTS, so applying
    the schema only when the file is absent means a database created in an
    earlier phase never receives tables added in a later one. That is how
    a live database ended up without tool_calls while every test - always a
    fresh temp file - passed.

    Note the limit: this adds missing tables and indexes, not missing
    COLUMNS on tables that already exist. Adding a column to an existing
    table needs an explicit ALTER, because CREATE TABLE IF NOT EXISTS will
    quietly do nothing.
    """
    schema_path = PROJECT_ROOT / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Missing schema file at {schema_path}")

    sql = schema_path.read_text(encoding="utf-8")
    if database_path:
        conn = sqlite3.connect(str(database_path))
        _apply_pragmas(conn)
        _migrate_columns(conn)
        conn.executescript(sql)
        conn.commit()
        conn.close()
    else:
        db = get_db()
        _migrate_columns(db)
        db.executescript(sql)
        db.commit()


@click.command("init-db")
def init_db_command() -> None:
    """flask init-db - create the database file and tables."""
    init_db()
    click.echo(f"Initialised database: {current_app.config['DATABASE']}")


# ---------------------------------------------------------------------
# Authentication Layer
# ---------------------------------------------------------------------
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def login_required(view):
    """Redirect anonymous visitors to the login page."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def require_approval(view):
    """Gate a view on an approved account."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return {"error": "Authentication required"}, 401
            return redirect(url_for("auth.login", next=request.path))
        if not g.user["is_approved"]:
            return {"error": "Your account is awaiting approval."}, 403
        return view(**kwargs)
    return wrapped


@auth_bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        display_name = (request.form.get("display_name") or "").strip() or None

        error = None
        if not username:
            error = "Email is required."
        elif "@" not in username:
            error = "That does not look like an email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."

        if error is None:
            database = get_db()
            is_first = database.execute(
                "SELECT COUNT(*) AS n FROM users"
            ).fetchone()["n"] == 0

            try:
                database.execute(
                    "INSERT INTO users (username, password_hash, display_name, is_approved, is_admin)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        username,
                        generate_password_hash(password),
                        display_name,
                        1 if is_first else 0,
                        1 if is_first else 0,
                    ),
                )
                database.commit()
            except sqlite3.IntegrityError:
                error = f"{username} is already registered."
            else:
                if is_first:
                    flash("Account created and approved - you are the admin.")
                else:
                    flash("Account created. An admin must approve it before you can chat.")
                return redirect(url_for("auth.login"))

        flash(error)

    return render_template("login.html", mode="register")


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = user["display_name"] or user["username"]
            session.permanent = True

            nxt = request.args.get("next")
            # A backslash has to be rejected too: "/\\evil.com" passes a
            # naive "starts with one slash" test, and every browser
            # normalises it to "//evil.com" - an off-site redirect from a
            # link that shows the real hostname.
            if (nxt and nxt.startswith("/") and not nxt.startswith("//")
                    and "\\" not in nxt):
                return redirect(nxt)
            return redirect(url_for("index"))

    return render_template("login.html", mode="login")


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------
# Resumable Redis SSE Streaming Layer
# ---------------------------------------------------------------------
def _redis_client(url: str) -> redis.Redis:
    return redis.from_url(url, decode_responses=True)


def _k_args(qid: str) -> str:
    return f"query_args:{qid}"


def _k_events(qid: str) -> str:
    return f"stream:{qid}"


def _k_started(qid: str) -> str:
    return f"stream_started:{qid}"


def register_query(redis_url: str, payload: dict) -> str:
    """Record a turn's parameters and return its id. Starts no work."""
    qid = str(uuid.uuid4())
    _redis_client(redis_url).setex(_k_args(qid), QUERY_ARGS_TTL, json.dumps(payload))
    return qid


def get_query_args(redis_url: str, qid: str) -> dict | None:
    raw = _redis_client(redis_url).get(_k_args(qid))
    return json.loads(raw) if raw else None


def emit(r: redis.Redis, qid: str, event: dict) -> None:
    """Append one event to a Redis event list."""
    r.rpush(_k_events(qid), json.dumps(event))
    r.expire(_k_events(qid), STREAM_TTL)


def claim_stream(redis_url: str, qid: str) -> bool:
    """Try to become the worker for this query. True for exactly one caller."""
    r = _redis_client(redis_url)
    return bool(r.set(_k_started(qid), "1", nx=True, ex=STREAM_TTL))


def sse_frame(event: dict, index: int) -> str:
    """Format one event as an SSE frame."""
    return f"id: {index}\ndata: {json.dumps(event)}\n\n"


def consume_stream(redis_url: str, qid: str, start_index: int = 0):
    """Yield SSE frames for a stream beginning at start_index."""
    r = _redis_client(redis_url)
    key = _k_events(qid)
    index = start_index
    last_event = time.time()
    last_yield = time.time()

    yield ": open\n\n"

    while True:
        batch = r.lrange(key, index, index + 99)
        if batch:
            last_event = last_yield = time.time()
            for raw in batch:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                yield sse_frame(event, index)
                index += 1

                if event.get("type") == "done":
                    return
            continue

        now = time.time()

        if now - last_event > IDLE_TIMEOUT:
            # Deliberately WITHOUT an id. `index` is the next unread
            # position and this frame is never appended to the Redis list,
            # so giving it that id would make the browser resume from
            # index+1 and skip the real event the worker writes there.
            yield "data: " + json.dumps(
                {"type": "error", "message": "Stream timed out."}) + "\n\n"
            return

        # Periodic comment frame during silence. Two jobs, and the second
        # one is the important one:
        #
        #   1. Stops proxies idling the connection out before the first
        #      token arrives.
        #   2. Writing is the ONLY way this generator discovers that the
        #      client has gone away. WSGI gives no disconnect callback, so
        #      a generator that merely sleeps will keep its worker thread
        #      and its socket until IDLE_TIMEOUT - and because the browser
        #      may reuse that connection, requests queued behind it hang
        #      too. A write to a dead socket raises, which ends the
        #      generator promptly.
        if now - last_yield >= KEEPALIVE_INTERVAL:
            last_yield = now
            yield ": keepalive\n\n"

        time.sleep(POLL_INTERVAL)


def sse_headers() -> dict[str, str]:
    """Headers required for SSE streaming."""
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


# ---------------------------------------------------------------------
# Phase 6: cooperative cancellation
# ---------------------------------------------------------------------
# A Python thread cannot be killed from outside. What it can do is check a
# flag at points where stopping is safe - between a model call and a tool
# call, never halfway through writing a row. That is cooperative
# cancellation, and every "stop" button worth having works this way.
#
# The second problem is harder. Under Gunicorn the generation runs in one
# process and the stop request may arrive at another, which cannot reach
# the first one's threading.Event because they do not share memory. So the
# stop is broadcast over a Redis pub/sub channel that every worker
# subscribes to; whichever one owns that chat sets its own local Event.
#
# Pub/sub, not a key: this is a broadcast to whoever is listening right
# now. There is nothing to store and nothing to clean up afterwards.

CANCEL_CHANNEL = "stellar_cancellations"

# How long a claim in Redis survives without being refreshed, and how often
# its owner refreshes it. A turn can legitimately run for ten minutes - a
# tool may block that long - so the claim cannot simply be given a
# generous fixed lifetime. It is heartbeated instead, which means a worker
# killed mid-turn (an out-of-memory kill, a deploy restart) leaves a claim
# that evaporates within GENERATION_TTL rather than one that blocks the
# chat forever.
GENERATION_TTL = 60
GENERATION_HEARTBEAT = 20

# chat_id -> (threading.Event, query_id). Process-local ON PURPOSE: an Event
# is a way to poke a thread in THIS process and means nothing outside it.
#
# What every worker does need to agree on - "chat 7 is generating, and the
# turn that owns it is query q" - is a different fact, and it lives in
# Redis under generating:{chat_id}. Keeping the two apart is the whole
# point: under Gunicorn there are four of these dictionaries and they
# share nothing, so anything that asked this one whether a chat was busy
# got the right answer one time in four.
ACTIVE_GENERATIONS: dict[int, tuple[threading.Event, str]] = {}
_ACTIVE_LOCK = threading.Lock()


def _k_generating(chat_id) -> str:
    return f"generating:{int(chat_id)}"


def _write_claim(redis_url: str, chat_id: int, query_id: str) -> None:
    try:
        _redis_client(redis_url).setex(
            _k_generating(chat_id), GENERATION_TTL, query_id)
    except Exception as exc:
        logger.warning("Could not record the generation claim: %s", exc)


def chat_is_generating(redis_url: str | None, chat_id: int) -> bool:
    """Is a turn running in this chat, anywhere in the cluster?

    Redis first, because it is the only view that spans workers. A local
    check still follows: it covers the instant between claiming a chat and
    writing that claim, and it is the fallback when Redis is unreachable.
    """
    if redis_url:
        try:
            if _redis_client(redis_url).exists(_k_generating(chat_id)):
                return True
        except Exception as exc:
            logger.warning("Falling back to local generation state: %s", exc)

    with _ACTIVE_LOCK:
        return any(k in ACTIVE_GENERATIONS
                   for k in (chat_id, str(chat_id), _as_int(chat_id))
                   if k is not None)


def _heartbeat_claim(redis_url: str, chat_id: int, query_id: str,
                     event: threading.Event) -> None:
    """Keep this turn's claim alive for as long as the turn is."""
    def beat() -> None:
        # event.wait doubles as the sleep so a cancelled turn stops
        # refreshing immediately rather than one interval later.
        while not event.wait(GENERATION_HEARTBEAT):
            with _ACTIVE_LOCK:
                current = ACTIVE_GENERATIONS.get(chat_id)
            if not current or current[1] != query_id:
                return          # finished, or superseded by a newer turn
            _write_claim(redis_url, chat_id, query_id)

    threading.Thread(target=beat, name=f"claim-{query_id[:8]}",
                     daemon=True).start()


def register_generation(chat_id: int, query_id: str,
                        redis_url: str | None = None) -> threading.Event:
    """Claim a chat for this thread, cancelling any generation it replaces."""
    event = threading.Event()
    with _ACTIVE_LOCK:
        previous = ACTIVE_GENERATIONS.get(chat_id)
        if previous:
            # A second generation in the same chat supersedes the first.
            # Leaving both running would interleave two replies into one
            # transcript.
            previous[0].set()
        ACTIVE_GENERATIONS[chat_id] = (event, query_id)

    if redis_url:
        _write_claim(redis_url, chat_id, query_id)
        # The generation being superseded may be running in a DIFFERENT
        # worker, where the local check above cannot see it. Broadcasting
        # the supersede is what stops two workers answering one chat at
        # once. Excluding our own query id keeps it from cancelling us.
        try:
            _redis_client(redis_url).publish(CANCEL_CHANNEL, json.dumps({
                "chat_id": chat_id, "exclude_query_id": query_id}))
        except Exception as exc:
            logger.warning("Could not broadcast the supersede: %s", exc)
        _heartbeat_claim(redis_url, chat_id, query_id, event)

    return event


def release_generation(chat_id: int, query_id: str,
                       redis_url: str | None = None) -> None:
    with _ACTIVE_LOCK:
        current = ACTIVE_GENERATIONS.get(chat_id)
        # Only clear our own claim - a newer generation may already own it.
        if current and current[1] == query_id:
            ACTIVE_GENERATIONS.pop(chat_id, None)

    if redis_url:
        try:
            r = _redis_client(redis_url)
            # Same rule in Redis: delete only a claim still stamped with
            # our own query id, or a turn that superseded us would lose
            # its claim the moment we finished.
            if r.get(_k_generating(chat_id)) == query_id:
                r.delete(_k_generating(chat_id))
        except Exception as exc:
            logger.warning("Could not clear the generation claim: %s", exc)


def signal_cancel(redis_url: str, chat_id: int, query_id: str | None = None,
                  exclude_query_id: str | None = None) -> None:
    """Stop a generation, wherever in the cluster it is running."""
    if query_id:
        # Durable flag as well as the broadcast: a worker that starts late,
        # or reconnects after the message was published, still sees it.
        try:
            _redis_client(redis_url).setex(f"stop:{query_id}", STREAM_TTL, "1")
        except Exception:
            pass

    try:
        _redis_client(redis_url).publish(CANCEL_CHANNEL, json.dumps({
            "chat_id": chat_id, "exclude_query_id": exclude_query_id}))
    except Exception as exc:
        logger.error("Could not publish cancellation: %s", exc)

    _apply_cancel(chat_id, exclude_query_id)


def _apply_cancel(chat_id, exclude_query_id: str | None = None) -> None:
    """Set the local Event for a chat, if this process owns it."""
    with _ACTIVE_LOCK:
        for key in {chat_id, str(chat_id), _as_int(chat_id)}:
            if key is None:
                continue
            entry = ACTIVE_GENERATIONS.get(key)
            if not entry:
                continue
            event, active_qid = entry
            # A new stream starting in this chat cancels the old one, and
            # must not cancel itself.
            if exclude_query_id and active_qid == exclude_query_id:
                continue
            logger.info("Cancelling generation chat=%s query=%s",
                        key, active_qid)
            event.set()


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def is_stopped(redis_url: str, query_id: str) -> bool:
    try:
        return bool(_redis_client(redis_url).exists(f"stop:{query_id}"))
    except Exception:
        return False


def start_cancel_listener(redis_url: str) -> None:
    """Subscribe this process to cancellations from the others."""
    def listen():
        while True:
            try:
                pubsub = _redis_client(redis_url).pubsub(
                    ignore_subscribe_messages=True)
                pubsub.subscribe(CANCEL_CHANNEL)
                logger.info("Listening for cancellations on %s", CANCEL_CHANNEL)
                for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        data = json.loads(msg["data"])
                        _apply_cancel(data.get("chat_id"),
                                      data.get("exclude_query_id"))
                    except Exception as exc:
                        logger.error("Bad cancellation message: %s", exc)
            except Exception as exc:
                # Redis restarts, networks blip. Without this loop the
                # worker would go permanently deaf to stop requests.
                logger.warning("Cancel listener dropped (%s); retrying in 5s", exc)
                time.sleep(5)

    threading.Thread(target=listen, name="cancel-listener", daemon=True).start()


def run_worker(app: Flask, qid: str, produce_fn) -> None:
    """Run producer in a daemon thread, piping everything to Redis."""
    redis_url = app.config["REDIS_URL"]

    def target() -> None:
        with app.app_context():
            r = _redis_client(redis_url)
            args = get_query_args(redis_url, qid) or {}
            # The producer needs its own id to honour a stop aimed at it.
            args["_query_id"] = qid
            args["_redis_url"] = redis_url
            try:
                for event in produce_fn(r, args):
                    emit(r, qid, event)
            except Exception as exc:
                app.logger.exception("Stream worker failed for %s", qid)
                emit(r, qid, {
                    "type": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                })
            finally:
                # Releasing the generation claim is the producer's own job
                # now (see gemini_producer), so there is nothing to undo here.
                emit(r, qid, {"type": "done"})

    threading.Thread(target=target, name=f"stream-{qid[:8]}", daemon=True).start()


# ---------------------------------------------------------------------
# Phase 7: Key rotation
# ---------------------------------------------------------------------
# Free-tier Gemini keys rate limit constantly - 20 requests per day, per
# model, per project. One key is unusable; three keys used properly is a
# working development budget. This is what makes the difference.
#
# Blocks are tracked per (key, model) pair, never per key. Google meters
# GenerateRequestsPerDayPerProjectPerModel, so a key exhausted on
# gemini-3-flash is untouched on gemini-3.6-flash. Blocking the whole key
# would discard two thirds of the available capacity.

PACIFIC_TZ = "America/Los_Angeles"

# Default block when the API gives no usable hint.
DEFAULT_RPM_BLOCK = 61          # just past a one-minute window
OVERLOAD_BLOCK = 600            # model is busy, not the key's fault
INVALID_BLOCK = 24 * 60 * 60    # a bad key stays bad


def seconds_until_pacific_midnight() -> int:
    """Seconds until Google's daily quota reset.

    Per-day quotas reset at midnight US Pacific, not at the caller's local
    midnight and not 24 hours after the block. Blocking for a flat day would
    keep a key idle long after it recovered.
    """
    import datetime as _dt
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(PACIFIC_TZ)
    except Exception:
        return 6 * 60 * 60        # tzdata missing: fall back to a coarse wait

    now = _dt.datetime.now(tz)
    tomorrow = (now + _dt.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def parse_quota_block(error_text: str) -> tuple[int, str]:
    """Decide how long to block a key, and why, from an API error.

    Returns (seconds, reason) with reason one of RPD, RPM, OVERLOAD,
    INVALID.

    The subtlety that matters: a per-DAY 429 still carries a short
    retryDelay. A real response to an exhausted daily quota looked like

        quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
        limit: 20
        Please retry in 4.389960791s.

    Obeying that four seconds would retry against a quota with nothing left
    in it, all day. The quotaId is the trustworthy signal, not the delay.
    """
    t = (error_text or "").lower()

    if any(x in t for x in ("permission_denied", "api_key_invalid",
                            "api key not valid", "unauthenticated",
                            "401", "403")):
        return INVALID_BLOCK, "INVALID"

    if "overloaded" in t or "503" in t or "unavailable" in t:
        return OVERLOAD_BLOCK, "OVERLOAD"

    # Daily exhaustion. Check this BEFORE reading any retry delay.
    if "perday" in t or "per day" in t or "requestsperday" in t:
        return seconds_until_pacific_midnight(), "RPD"

    # Per-minute: the delay the API supplies is accurate and worth using.
    m = re.search(r"retry in ([0-9.]+)s", t) or re.search(r"retrydelay['\"]?:\s*['\"]?(\d+)s", t)
    if m:
        try:
            return max(5, int(float(m.group(1))) + 2), "RPM"
        except ValueError:
            pass

    if "429" in t or "resource_exhausted" in t or "quota" in t:
        return DEFAULT_RPM_BLOCK, "RPM"

    return DEFAULT_RPM_BLOCK, "RPM"


# Per-model rate limits, minus a deliberate reserve.
#
# Counting ahead of the limit is what turns a 429 from a certainty into an
# exception. The reserve absorbs the race between four Gunicorn workers
# deciding simultaneously that a key still has room: without it, the last
# few requests of a window are a coin flip.
MODEL_LIMITS = {
    # substring match -> (requests per minute, requests per day)
    "gemini-3-flash":      (4, 15),     # observed: 5 rpm / 20 rpd
    "gemini-3.5-flash":    (4, 15),
    "gemini-3.6-flash":    (4, 15),
    "gemini-2.5-flash":    (9, 245),
    "flash-lite":          (14, 495),   # 15 rpm / 500 rpd
    "gemma":               (14, 1495),  # 15 rpm / 1500 rpd
}
DEFAULT_LIMITS = (4, 15)


def get_limits(model: str | None) -> tuple[int, int]:
    """(rpm, rpd) for a model, erring low."""
    if not model:
        return DEFAULT_LIMITS
    m = model.lower()
    for frag, limits in MODEL_LIMITS.items():
        if frag in m:
            return limits
    return DEFAULT_LIMITS


def pacific_day_bucket() -> str:
    """Today's date in US Pacific, the bucket daily quota is counted against.

    Counting against the caller's local date would roll over at the wrong
    moment - for a user in IST that is mid-afternoon Pacific, less than half
    way through the quota day.
    """
    import datetime as _dt
    import zoneinfo
    try:
        tz = zoneinfo.ZoneInfo(PACIFIC_TZ)
        return _dt.datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


class KeyManager:
    """Tracks which (key, model) pairs are currently unusable.

    State lives in Redis because Gunicorn runs four worker processes. If
    worker 2 discovers a key is exhausted, workers 1, 3 and 4 must not go
    on retrying it - and a Python dict is invisible across processes.

    Redis TTLs do the expiry: a block simply stops existing when its time is
    up, so there is no sweeper to run and no clock to reconcile.

    When Redis is unreachable the manager degrades to a process-local dict
    and says so once. Single-process development still works; production
    correctness needs Redis, and the warning makes the difference visible
    rather than silent.
    """

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url
        self._client = None
        self._local: dict[tuple[str, str], tuple[float, str]] = {}
        self._counts_rpd: dict[tuple[str, str, str], int] = {}
        self._counts_rpm: dict[tuple[str, str], list[float]] = {}
        self._model_blocks: dict[str, float] = {}
        self._warned = False
        self._lock = threading.Lock()

    # -- storage ------------------------------------------------------
    def _r(self):
        if self._client is None and self._redis_url:
            try:
                c = redis.from_url(self._redis_url, decode_responses=True,
                                   socket_connect_timeout=2)
                c.ping()
                self._client = c
            except Exception:
                if not self._warned:
                    logger.warning(
                        "KeyManager: Redis unavailable, falling back to "
                        "process-local blocks. Multiple workers will not "
                        "share rate-limit state.")
                    self._warned = True
        return self._client

    @staticmethod
    def _fingerprint(key: str) -> str:
        """Short digest of a key, so raw credentials never land in Redis."""
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _redis_key(self, key: str, model: str) -> str:
        return f"keyblock:{self._fingerprint(key)}:{model}"

    # -- api ----------------------------------------------------------
    def block(self, key: str, model: str, seconds: int, reason: str) -> None:
        seconds = max(1, int(seconds))
        # An invalid or revoked key is invalid everywhere. Scoping that to
        # one model would leave the pool cycling a dead credential through
        # every other model before giving up.
        if reason == "INVALID":
            model = "*"
        r = self._r()
        if r is not None:
            try:
                r.setex(self._redis_key(key, model), seconds, reason)
            except Exception:
                self._client = None
        with self._lock:
            self._local[(self._fingerprint(key), model)] = (
                time.time() + seconds, reason)

        logger.warning("Key %s blocked on %s for %ds (%s)",
                       self._fingerprint(key), model, seconds, reason)

    def is_blocked(self, key: str, model: str) -> tuple[bool, str | None]:
        # Counters first: this is the proactive path, and it is what keeps a
        # key from being handed out for the call that would 429.
        over = self._over_limit(key, model)
        if over:
            return True, over

        r = self._r()
        if r is not None:
            try:
                val = r.get(self._redis_key(key, model))
                if val:
                    return True, val
                # A key can also be blocked across every model - an invalid
                # or revoked credential is not a per-model condition.
                gval = r.get(self._redis_key(key, "*"))
                if gval:
                    return True, gval
                return False, None
            except Exception:
                self._client = None

        with self._lock:
            now = time.time()
            for scope in (model, "*"):
                entry = self._local.get((self._fingerprint(key), scope))
                if entry and entry[0] > now:
                    return True, entry[1]
        return False, None

    # -- proactive counting -------------------------------------------
    def record_request(self, key: str, model: str) -> None:
        """Count a request against this (key, model) before it is sent.

        Reactive blocking - waiting for a 429 and then rotating - throws
        away one request per key per window, because the failing call still
        costs a round trip. Counting ahead means a key is retired quietly
        when it approaches its limit and the 429 mostly stops happening.
        """
        fp = self._fingerprint(key)
        day = pacific_day_bucket()
        now = time.time()

        r = self._r()
        if r is not None:
            try:
                pipe = r.pipeline()
                # Daily counter, expiring when the quota itself resets.
                rpd_key = f"keycount:rpd:{fp}:{model}:{day}"
                pipe.incr(rpd_key)
                pipe.expire(rpd_key, seconds_until_pacific_midnight())
                # Per-minute as a sorted set keyed by timestamp: a true
                # sliding window, not a fixed bucket that resets on the
                # minute and lets a burst through at the boundary.
                rpm_key = f"keycount:rpm:{fp}:{model}"
                pipe.zadd(rpm_key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
                pipe.zremrangebyscore(rpm_key, "-inf", now - 60)
                pipe.expire(rpm_key, 120)
                pipe.execute()
                return
            except Exception:
                self._client = None

        with self._lock:
            self._counts_rpd[(fp, model, day)] = \
                self._counts_rpd.get((fp, model, day), 0) + 1
            window = [t for t in self._counts_rpm.get((fp, model), []) if t > now - 60]
            window.append(now)
            self._counts_rpm[(fp, model)] = window

    def _over_limit(self, key: str, model: str) -> str | None:
        """'RPD', 'RPM', or None - based on counters, before any API call."""
        rpm_limit, rpd_limit = get_limits(model)
        fp = self._fingerprint(key)
        day = pacific_day_bucket()
        now = time.time()

        r = self._r()
        if r is not None:
            try:
                used_rpd = int(r.get(f"keycount:rpd:{fp}:{model}:{day}") or 0)
                if used_rpd >= rpd_limit:
                    return "RPD"
                rpm_key = f"keycount:rpm:{fp}:{model}"
                r.zremrangebyscore(rpm_key, "-inf", now - 60)
                if r.zcard(rpm_key) >= rpm_limit:
                    return "RPM"
                return None
            except Exception:
                self._client = None

        with self._lock:
            if self._counts_rpd.get((fp, model, day), 0) >= rpd_limit:
                return "RPD"
            window = [t for t in self._counts_rpm.get((fp, model), []) if t > now - 60]
            if len(window) >= rpm_limit:
                return "RPM"
        return None

    def usage(self, key: str, model: str) -> dict:
        """How much of this (key, model) budget is spent. For the admin view."""
        rpm_limit, rpd_limit = get_limits(model)
        fp = self._fingerprint(key)
        day = pacific_day_bucket()
        used_rpd = 0
        r = self._r()
        if r is not None:
            try:
                used_rpd = int(r.get(f"keycount:rpd:{fp}:{model}:{day}") or 0)
            except Exception:
                self._client = None
        else:
            with self._lock:
                used_rpd = self._counts_rpd.get((fp, model, day), 0)
        return {"rpd_used": used_rpd, "rpd_limit": rpd_limit, "rpm_limit": rpm_limit}

    # -- model-wide overload ------------------------------------------
    def block_model(self, model: str, seconds: int) -> None:
        """Mark a model unusable for every key.

        A 503 means Google is overloaded, not that the key is spent.
        Rotating keys against an overloaded model just burns the whole pool
        on the same failure.
        """
        r = self._r()
        if r is not None:
            try:
                r.setex(f"modelblock:{model}", max(1, int(seconds)), "OVERLOAD")
            except Exception:
                self._client = None
        with self._lock:
            self._model_blocks[model] = time.time() + seconds
        logger.warning("Model %s marked overloaded for %ds", model, seconds)

    def is_model_blocked(self, model: str) -> bool:
        r = self._r()
        if r is not None:
            try:
                return bool(r.get(f"modelblock:{model}"))
            except Exception:
                self._client = None
        with self._lock:
            return self._model_blocks.get(model, 0) > time.time()

    def first_available(self, keys: list[str], model: str) -> int | None:
        """Index of the lowest-numbered usable key on this model, or None.

        Earliest-available rather than round-robin, and the difference is
        deliberate. Each key carries its own daily allowance, so the goal is
        to drain one at a time: key 1 is used exclusively until it blocks,
        then key 2, and the moment key 1's window expires the scan returns
        to it. Round-robin would spread usage evenly and leave every key
        partially spent.
        """
        # An overloaded model has no usable key by definition - the failure
        # is upstream of the credential.
        if self.is_model_blocked(model):
            return None

        for i, k in enumerate(keys):
            blocked, _ = self.is_blocked(k, model)
            if not blocked:
                return i
        return None

    def status(self, keys: list[str], models: list[str]) -> list[dict]:
        """Per-key state, for the admin view. Never returns a raw key."""
        out = []
        for i, k in enumerate(keys):
            entry = {"index": i, "fingerprint": self._fingerprint(k),
                     "blocked_on": {}}
            for m in models:
                blocked, reason = self.is_blocked(k, m)
                if blocked:
                    entry["blocked_on"][m] = reason
            entry["usable"] = len(entry["blocked_on"]) < len(models)
            out.append(entry)
        return out


KEY_MANAGER = KeyManager()


# ---------------------------------------------------------------------
# Phase 4: Agent tools
# ---------------------------------------------------------------------
# Each tool is a plain Python function. google-genai reads its signature
# and docstring to build the function-calling schema the model sees, so the
# docstring is not a comment - it is the interface. A vague description
# produces a tool the model calls at the wrong moments.
#
# Every tool takes `status`: a short present-tense line the MODEL writes and
# the UI shows while the call runs. Letting the model narrate its own work
# costs one argument and is most of why the interface feels alive.

def _is_safe_url(url: str) -> tuple[bool, str]:
    """Reject URLs that point back into private network space.

    The model chooses these URLs, and anything that can steer the
    conversation can steer them - a page telling the agent to "check
    http://169.254.169.254/" is a cloud credential exfiltration attempt, and
    http://localhost:6379/ is the Redis this app runs on. Resolving the
    hostname first is the point: a public name is free to resolve to
    127.0.0.1, so checking the string alone proves nothing.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL"

    if parsed.scheme not in ("http", "https"):
        return False, f"Only http and https are allowed, got {parsed.scheme!r}"
    if not parsed.hostname:
        return False, "URL has no host"

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False, f"Could not resolve {parsed.hostname}"

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False, f"{parsed.hostname} resolves to a private address"

    return True, ""


def get_current_time(timezone: str, status: str) -> str:
    """Get the current date and time, optionally in a specific timezone.

    Use this whenever the answer depends on what the date or time is now.
    The model has no clock of its own, so it cannot answer this by reasoning.

    Args:
        timezone: An IANA timezone name such as 'Asia/Kolkata' or 'UTC'.
            Pass 'UTC' if the user did not specify one.
        status: A short present-tense line shown to the user while this runs,
            for example 'Checking the current time'.

    Returns:
        The current date and time as a human-readable string.
    """
    import datetime as _dt
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        # Distinguish "you typed a bad name" from "this machine has no tz
        # database at all". Reporting the second as the first sends the model
        # in circles retrying names that were correct to begin with.
        try:
            zoneinfo.ZoneInfo("UTC")
        except Exception:
            return ("Timezone database unavailable on this host. "
                    "Install the tzdata package.")
        return f"Unknown timezone {timezone!r}. Use an IANA name like 'Asia/Kolkata'."
    except Exception as exc:
        return f"Could not resolve timezone {timezone!r}: {exc}"

    now = _dt.datetime.now(tz)
    return now.strftime("%A, %d %B %Y at %H:%M:%S %Z")


def fetch_url(url: str, status: str) -> str:
    """Fetch a web page and return its readable text content.

    Use this when the user gives a URL, or when a search result needs
    reading in full. Returns text only - scripts, styles and navigation are
    stripped.

    Args:
        url: The full http or https URL to fetch.
        status: A short present-tense line shown to the user while this runs,
            for example 'Reading the article'.

    Returns:
        The page title followed by its readable text, truncated if very long.
    """
    import requests
    from bs4 import BeautifulSoup

    ok, why = _is_safe_url(url)
    if not ok:
        return f"Refused to fetch {url}: {why}"

    try:
        resp = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Stellar/1.0 (+https://github.com/rishikatla791-spec/new-stellar)"},
            # Redirects are followed BY HAND, one hop at a time, because
            # _is_safe_url only ever sees the URL it is given. Letting
            # requests follow them meant a public host could answer 302
            # http://169.254.169.254/ and hand cloud credentials straight
            # back to the model: the guard checked the decoy, not the
            # destination. Every hop is now re-checked.
            allow_redirects=False,
        )
        hops = 0
        while resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            hops += 1
            if hops > 5:
                return f"{url} redirected more than 5 times; giving up."
            target = urljoin(resp.url or url, resp.headers.get("Location", ""))
            ok, why = _is_safe_url(target)
            if not ok:
                return (f"{url} redirects to {target}, which is not allowed: "
                        f"{why}. Refused.")
            resp = requests.get(
                target, timeout=20,
                headers={"User-Agent": "Stellar/1.0 (+https://github.com/rishikatla791-spec/new-stellar)"},
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        return f"Could not fetch {url}: {type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return f"{url} returned HTTP {resp.status_code}"

    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype and "text" not in ctype:
        return f"{url} is {ctype or 'an unknown type'}, not readable text."

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))

    if len(text) > FETCH_STORE_LIMIT:
        text = text[:FETCH_STORE_LIMIT] + f"\n\n[page cut at {FETCH_STORE_LIMIT} characters]"

    return f"# {title}\nSource: {url}\n\n{text}" if title else f"Source: {url}\n\n{text}"


def web_search(query: str, status: str, max_results: int = 5) -> str:
    """Search the web and return ranked results with summaries.

    Use this for anything you do not know, anything that may have changed
    recently, and anything where being wrong matters. Prefer searching over
    guessing.

    Args:
        query: What to search for, phrased as a search query rather than a
            question.
        status: A short present-tense line shown to the user while this runs,
            for example 'Searching for recent coverage'.
        max_results: How many results to return, between 1 and 10.

    Returns:
        A numbered list of results with titles, URLs and content snippets.
    """
    import requests

    keys = tavily_keys()
    if not keys:
        return (
            "Web search is not configured: no TAVILY_API_KEY in keys.env. "
            "Tell the user to get a free key at tavily.com and add it."
        )
    api_key = keys[0]

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max(1, min(int(max_results or 5), 10)),
                "include_answer": True,
            },
            timeout=25,
        )
    except requests.RequestException as exc:
        return f"Search failed: {type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return f"Search failed with HTTP {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    lines = []
    if data.get("answer"):
        lines.append(f"Summary: {data['answer']}\n")

    for i, item in enumerate(data.get("results", []), 1):
        lines.append(f"{i}. {item.get('title', 'Untitled')}")
        lines.append(f"   {item.get('url', '')}")
        snippet = (item.get("content") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"   {snippet[:400]}")
        lines.append("")

    return "\n".join(lines) if lines else f"No results for {query!r}."


# ---------------------------------------------------------------------
# Phase 5: the Docker sandbox
# ---------------------------------------------------------------------
# lab_execute is different in kind from the tools above. get_current_time
# runs code you wrote; this runs code the MODEL wrote, which you have never
# seen. The container is what makes that a reasonable thing to do.
#
# Three layers do the containing:
#   filesystem  only /lab reaches the host, via a bind mount
#   network     a per-user bridge with inter-container comms disabled
#   resources   memory, CPU and process caps, so a runaway cannot take the
#               machine down
#
# Root inside the container is fine. It is root over a filesystem we chose,
# on a network that reaches nothing, in a process tree that cannot grow past
# its cap.

LAB_IMAGE = "stellar-lab:latest"
LAB_MOUNT = "/lab"

# A command may install a toolchain; it may also loop forever. The cap is
# enforced INSIDE the container with coreutils timeout, so the process is
# actually killed rather than merely abandoned by a client that gave up.
LAB_DEFAULT_TIMEOUT = 60
LAB_MAX_TIMEOUT = 600

# Resource ceilings, applied at creation. A sandbox without these is not a
# sandbox: `while true; do :; done` would take a core, and a fork bomb the
# whole host.
LAB_MEMORY = "2g"
LAB_CPUS = 2.0
LAB_PIDS = 512

# A build log runs to tens of thousands of lines. Everything is stored in
# tool_calls; only the tail is fed back to the model, because context is the
# scarce resource, not disk.
LAB_OUTPUT_LIMIT = 8000


def _docker():
    """A Docker client, or an error a human can act on.

    The import lives inside the try deliberately. It used to sit above it,
    so a missing package escaped as a bare ModuleNotFoundError - which the
    model has no way to interpret. It retried three times, gave up, and
    apologised to the user about "a technical issue with the sandbox". An
    error the model can read is an error the model can report accurately.
    """
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError(
            "The docker package is not installed in the interpreter running "
            "this server. Tell the user the server is running with the wrong "
            "Python: it must be started with .venv/Scripts/python.exe app.py, "
            "not a system-wide python."
        ) from exc

    try:
        c = docker.from_env()
        c.ping()
        return c
    except Exception as exc:
        raise RuntimeError(
            f"Docker is not reachable ({type(exc).__name__}). "
            "Tell the user to start Docker Desktop, then run docker_setup.py."
        ) from exc


def _lab_identity() -> tuple[int, int]:
    """Whose sandbox this is.

    Tools receive only the arguments the model chose, so the owning user and
    chat have to arrive another way. gemini_producer puts them on Flask's
    request-scoped g before the loop starts. Without it a tool could not
    tell whose container to use - and guessing would be a data leak.
    """
    uid = getattr(g, "lab_user_id", None)
    cid = getattr(g, "lab_chat_id", None)
    if uid is None or cid is None:
        raise RuntimeError("No lab identity on this request.")
    return int(uid), int(cid)


def _lab_container_name(user_id: int, chat_id: int) -> str:
    # Per CHAT, not per user. Two conversations are separate workspaces:
    # packages installed while debugging one should not appear in the other,
    # and a wrecked environment should cost one conversation, not all of them.
    return f"stellar-lab-u{user_id}-c{chat_id}"


def _lab_workspace(user_id: int, chat_id: int) -> Path:
    """Host directory bind-mounted at /lab. Survives the container."""
    d = PROJECT_ROOT / "sandbox_runs" / f"u{user_id}_c{chat_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_network(client, user_id: int) -> str:
    """One bridge network per user, created on demand, ICC disabled.

    Per user rather than one shared network: with inter-container
    communication off, containers cannot reach each other anyway, but a
    separate network per user means a misconfiguration leaks at most to that
    user's own sandboxes.
    """
    name = f"stellar_net_u{user_id}"
    try:
        client.networks.get(name)
    except Exception:
        try:
            client.networks.create(
                name, driver="bridge",
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels={"stellar": "sandbox", "user": str(user_id)},
            )
            logger.info("Created isolated network %s", name)
        except Exception as exc:
            logger.warning("Could not create %s (%s); using %s",
                           name, exc, "stellar_isolated")
            return "stellar_isolated"
    return name


def _get_or_create_lab(client, user_id: int, chat_id: int):
    """Return this chat's container, starting or creating it as needed."""
    name = _lab_container_name(user_id, chat_id)

    try:
        c = client.containers.get(name)
        if c.status != "running":
            # Exists but stopped - a host reboot, or Docker restarting.
            # /lab is on the host, so restarting loses nothing that matters.
            logger.info("Restarting lab container %s (was %s)", name, c.status)
            c.start()
        return c
    except Exception:
        pass

    workspace = _lab_workspace(user_id, chat_id)
    network = _user_network(client, user_id)

    logger.info("Creating lab container %s on %s", name, network)
    return client.containers.run(
        LAB_IMAGE,
        name=name,
        detach=True,
        network=network,
        volumes={str(workspace): {"bind": LAB_MOUNT, "mode": "rw"}},
        working_dir=LAB_MOUNT,
        mem_limit=LAB_MEMORY,
        nano_cpus=int(LAB_CPUS * 1_000_000_000),
        pids_limit=LAB_PIDS,
        # The image's CMD is `tail -f /dev/null`: the container has no job of
        # its own, it exists to be exec'd into.
        labels={"stellar": "lab", "user": str(user_id), "chat": str(chat_id)},
    )


def _run_in_lab(container, command: str, timeout: int) -> tuple[int, str]:
    """Execute one shell command, returning (exit_code, combined output).

    stdout and stderr are combined deliberately. The model is reading this
    as a terminal transcript, and a traceback split away from the output
    that preceded it is much harder to reason about.
    """
    result = container.exec_run(
        # coreutils timeout, inside the container, so the process is killed
        # rather than merely orphaned. Exit 124 means it hit the limit.
        cmd=["timeout", "--signal=KILL", str(timeout), "bash", "-lc", command],
        workdir=LAB_MOUNT,
        demux=False,
        tty=False,
    )
    output = result.output or b""
    if isinstance(output, bytes):
        # Container output is whatever the command emitted - a binary blob
        # from a stray `cat` should not raise a UnicodeDecodeError and kill
        # the turn.
        output = output.decode("utf-8", errors="replace")
    return result.exit_code, output


def lab_execute(command: str, status: str, timeout: int = 60) -> str:
    """Run a bash command inside this chat's private Linux sandbox.

    Use this to actually do things rather than describe them: run Python,
    install packages with pip or apt-get, clone repositories, process data,
    generate files and plots.

    The sandbox is a container that persists for this chat. Files written to
    /lab survive between commands and between messages, so work can be built
    up over several turns. The working directory is always /lab. Python 3.12
    is installed along with pandas, numpy, matplotlib, requests and
    beautifulsoup4; anything else can be installed with pip.

    Args:
        command: The bash command to run, for example
            'python3 analysis.py' or 'pip install seaborn && python3 plot.py'.
        status: A short present-tense line shown to the user while this runs,
            for example 'Installing dependencies' or 'Running the analysis'.
        timeout: Seconds to allow before the command is killed. Default 60.
            Use more for installs or long jobs, up to 600.

    Returns:
        The combined stdout and stderr of the command, plus its exit code
        when it failed.
    """
    command = (command or "").strip()
    if not command:
        return "No command given."

    try:
        timeout = max(1, min(int(timeout or LAB_DEFAULT_TIMEOUT), LAB_MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = LAB_DEFAULT_TIMEOUT

    try:
        user_id, chat_id = _lab_identity()
        client = _docker()
    except RuntimeError as exc:
        return str(exc)

    try:
        container = _get_or_create_lab(client, user_id, chat_id)
        code, output = _run_in_lab(container, command, timeout)
    except Exception as exc:
        # Exit 128 and its relatives mean the container's mount namespace
        # has broken - it exists and answers, but nothing inside it works.
        # Recreating and retrying once turns a dead chat into a hiccup the
        # user never sees. This is the single most common sandbox failure.
        msg = str(exc)
        if "128" in msg or "not running" in msg.lower() or "no such container" in msg.lower():
            logger.warning("Lab container broken (%s); recreating", msg[:120])
            try:
                old = client.containers.get(_lab_container_name(user_id, chat_id))
                old.remove(force=True)
            except Exception:
                pass
            try:
                container = _get_or_create_lab(client, user_id, chat_id)
                code, output = _run_in_lab(container, command, timeout)
            except Exception as exc2:
                return f"Sandbox failed even after recreating it: {exc2}"
        else:
            return f"Sandbox error: {type(exc).__name__}: {exc}"

    if code == 124 or code == 137:
        return (f"Command killed after {timeout}s.\n\n{output[-2000:]}\n\n"
                f"[timed out - raise the timeout argument, or split the work "
                f"into smaller commands]")

    if len(output) > LAB_OUTPUT_LIMIT:
        # Keep the tail: the error and the final lines are almost always what
        # matters, and the head of a build log rarely is.
        omitted = len(output) - LAB_OUTPUT_LIMIT
        output = (f"[{omitted} characters trimmed from the start]\n"
                  + output[-LAB_OUTPUT_LIMIT:])

    if code == 0:
        return output or "(command produced no output)"
    return f"Exit code {code}\n\n{output}"


# ---------------------------------------------------------------------
# Phase 6: context compression
# ---------------------------------------------------------------------
# Every turn resends the whole conversation, so a long chat eventually
# stops fitting. Truncating the oldest messages blindly loses exactly the
# thing that matters - what the agent was in the middle of doing.
#
# Instead the model is told how full its context is, and compresses its own
# memory: it writes a structured summary of the current state, and only then
# are old rows hidden. The summary stays visible, so the work survives even
# though the transcript does not.

# Characters per token, roughly, for English plus code. An estimate is used
# rather than the API's counter because counting is itself a billed request,
# and spending quota to discover you are low on quota is a poor trade.
CHARS_PER_TOKEN = 4
CONTEXT_LIMIT_TOKENS = 1_000_000
# Warn at 70%: late enough not to nag, early enough that there is room to
# write the summary and keep working.
CONTEXT_WARN_RATIO = 0.70

# What stays visible after a compression.
KEEP_RECENT_MESSAGES = 4
KEEP_RECENT_TOOL_CALLS = 10

COMPRESSED_PREFIX = "[COMPRESSED MEMORY STATE]"


def estimate_context_usage(database, chat_id: int) -> tuple[int, float]:
    """(estimated tokens, fraction of the window used) for a chat."""
    row = database.execute(
        "SELECT COALESCE(SUM(LENGTH(message_content)), 0) AS n"
        " FROM messages WHERE chat_id = ? AND hidden = 0", (chat_id,)
    ).fetchone()
    chars = row["n"] or 0

    row = database.execute(
        "SELECT COALESCE(SUM(LENGTH(COALESCE(result,'')) + LENGTH(arguments)), 0) AS n"
        " FROM tool_calls WHERE chat_id = ? AND hidden = 0", (chat_id,)
    ).fetchone()
    chars += row["n"] or 0

    tokens = chars // CHARS_PER_TOKEN
    return tokens, tokens / CONTEXT_LIMIT_TOKENS


def compress_memory(target: str, state_document: str, status: str) -> str:
    """Archive older parts of this conversation to free up context.

    Call this when told that context usage is high. Write the state document
    first and make it thorough: everything hidden by this call becomes
    invisible to you, and the document is what remains.

    Args:
        target: What to archive. One of 'tool_logs', 'chat_messages', or 'both'.
            Prefer 'tool_logs' first - tool output is usually the bulk of the
            context and the least valuable to keep verbatim.
        state_document: A structured summary of where this conversation has
            got to: the current objective, what has been discovered, which
            files were created or changed, and what is still outstanding.
            This is the only memory that survives, so write it properly.
        status: A short present-tense line shown to the user, for example
            'Compressing earlier context'.

    Returns:
        A note of how much was archived.
    """
    if target not in ("tool_logs", "chat_messages", "both"):
        return (f"Invalid target {target!r}. "
                "Use 'tool_logs', 'chat_messages', or 'both'.")

    # Refusing a thin summary is the whole safety mechanism. A model under
    # context pressure will happily write "continuing the task" and delete
    # everything that gave those words meaning.
    if not state_document or len(state_document.strip()) < 80:
        return ("The state document is too short. Write a real summary of the "
                "objective, findings, files touched and open questions before "
                "compressing - everything else is about to become invisible "
                "to you.")

    try:
        chat_id = int(getattr(g, "lab_chat_id", None))
    except (TypeError, ValueError):
        return "No active chat to compress."

    database = get_db()
    tools_hidden = msgs_hidden = 0

    if target in ("tool_logs", "both"):
        keep = [r["id"] for r in database.execute(
            "SELECT id FROM tool_calls WHERE chat_id = ? AND hidden = 0"
            " ORDER BY id DESC LIMIT ?", (chat_id, KEEP_RECENT_TOOL_CALLS))]
        if keep:
            marks = ",".join("?" * len(keep))
            cur = database.execute(
                f"UPDATE tool_calls SET hidden = 1 WHERE chat_id = ?"
                f" AND hidden = 0 AND id NOT IN ({marks})", [chat_id] + keep)
        else:
            cur = database.execute(
                "UPDATE tool_calls SET hidden = 1 WHERE chat_id = ? AND hidden = 0",
                (chat_id,))
        tools_hidden = cur.rowcount

    if target in ("chat_messages", "both"):
        keep = [r["id"] for r in database.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND hidden = 0"
            " ORDER BY id DESC LIMIT ?", (chat_id, KEEP_RECENT_MESSAGES))]
        if keep:
            marks = ",".join("?" * len(keep))
            cur = database.execute(
                f"UPDATE messages SET hidden = 1 WHERE chat_id = ?"
                f" AND hidden = 0 AND id NOT IN ({marks})", [chat_id] + keep)
            msgs_hidden = cur.rowcount

    # The summary is stored as a hidden message: excluded from the UI, but
    # build_gemini_history reads hidden rows, so the model still sees it.
    database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content, hidden)"
        " VALUES (?, 'stellar', ?, 1)",
        (chat_id, f"{COMPRESSED_PREFIX}\n{state_document.strip()}"))
    database.commit()

    tokens, ratio = estimate_context_usage(database, chat_id)
    return (f"Archived {tools_hidden} tool call(s) and {msgs_hidden} message(s). "
            f"Your state document is preserved. Context now about "
            f"{ratio * 100:.0f}% full. Continue from the state document.")


# ---------------------------------------------------------------------
# Phase 10: generative UI and games
# ---------------------------------------------------------------------
# Every tool so far runs and returns in milliseconds. This one renders a
# widget into the chat and then STOPS, for as long as it takes a person to
# click something. That is architecturally different from anything else
# here, and it is what turns a transcript into an application.
#
# How the pause works: the tool appends its HTML to the same Redis list the
# stream is being read from, so the widget appears immediately, then blocks
# polling a second key for the answer. The browser POSTs whatever the user
# did to that key, and the poll wakes up and returns it as the tool result.
# The model then reasons about the answer and can render the next state.

# Long enough for someone to think about a chess move or fill in a form;
# short enough that an abandoned widget does not hold a worker thread all
# day. The wait also breaks early on cancellation, so Stop still works.
INTERACTION_TIMEOUT = 600
INTERACTION_POLL = 0.1


def _k_interaction(interaction_id: str) -> str:
    return f"interaction:{interaction_id}"


class _WidgetUnavailable(Exception):
    pass


def _show_widget(html: str, goal: str, replace_id: str | None = None,
                 update: dict | None = None) -> str:
    """Put a widget on the stream. Returns its interaction id.

    `update` is the position data for a widget that is already on screen.
    When the client still has that frame it hands the data to the running
    script instead of reloading the document - no flash, and the widget can
    animate the change. The html is sent as well so a client that lost the
    frame (a page reload mid-game) can rebuild it from scratch.
    """
    emit_fn = getattr(g, "stream_emit", None)
    if emit_fn is None or getattr(g, "stream_redis_url", None) is None:
        raise _WidgetUnavailable
    interaction_id = str(uuid.uuid4())
    emit_fn({"type": "interaction", "id": interaction_id, "html": html,
             "goal": goal, "replaces": replace_id or None,
             "update": update})
    return interaction_id


def _await_widget(interaction_id: str, timeout: int = INTERACTION_TIMEOUT,
                  close: bool = True):
    """Block until the widget answers. Returns the dict, or None on timeout.

    Returns the string "cancelled" if the user pressed Stop meanwhile, so a
    loop built on this can tell the two apart.

    close=False leaves the frame marked live on the client. A game waits
    dozens of times on the same widget; dimming it between every move would
    flicker.
    """
    r = _redis_client(g.stream_redis_url)
    key = _k_interaction(interaction_id)
    cancelled = getattr(g, "stream_cancelled", lambda: False)
    emit_fn = g.stream_emit

    def closed():
        if close:
            emit_fn({"type": "interaction_closed", "id": interaction_id})

    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancelled():
            emit_fn({"type": "interaction_closed", "id": interaction_id})
            return "cancelled"
        try:
            raw = r.lpop(key)
        except Exception:
            return None
        if raw:
            closed()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": str(raw)}
        time.sleep(INTERACTION_POLL)

    emit_fn({"type": "interaction_closed", "id": interaction_id})
    return None


def request_user_interaction(html_ui: str, goal: str, status: str,
                             replace_id: str = "") -> str:
    """Render an interactive widget in the chat and wait for the user.

    Use this whenever the next step depends on a person: playing a game,
    choosing between options, filling in a form, or confirming a direction
    before you build something. It is far better than asking in prose and
    hoping they answer in a parseable way.

    Your execution PAUSES here. The widget is shown, the user interacts, and
    whatever the widget passes to window.stellar.finish(data) comes back as
    this tool's return value. You then decide what happens next and may call
    this tool again with an updated widget to continue the loop.

    Args:
        html_ui: A complete, self-contained HTML fragment: markup, a <style>
            block, and a <script> that calls window.stellar.finish(data).
            No external scripts. It renders inside a dark chat bubble.
        goal: One line on what you are trying to learn from this interaction,
            for example 'get the user's chess move' or 'choose a colour
            scheme'. Shown to nobody; it keeps you honest about the purpose.
        status: A short present-tense line shown while the widget is open,
            for example 'Waiting for your move'.
        replace_id: Pass the interaction_id from the previous result to
            update that widget in place instead of adding a new one. Use this
            for anything with turns - a game, a multi-step form, a wizard.
            Without it every move leaves another board on screen and the
            conversation becomes a column of dead boards.

    Returns:
        A JSON string of whatever the widget sent, including the
        interaction_id to pass as replace_id next time.
    """
    if not html_ui or "<" not in html_ui:
        return "html_ui must be an HTML fragment."
    if "stellar.finish" not in html_ui:
        # Without this call the widget can never return, and the tool would
        # block for the full timeout while the user clicks a dead button.
        return ("That widget never calls window.stellar.finish(data), so it "
                "cannot return anything. Add a click handler that calls it.")

    emit_fn = getattr(g, "stream_emit", None)
    redis_url = getattr(g, "stream_redis_url", None)
    if emit_fn is None or redis_url is None:
        return "Interactive widgets are not available in this context."

    interaction_id = str(uuid.uuid4())

    # Append straight onto the stream the client is already reading, so the
    # widget appears before the wait begins rather than after it ends.
    emit_fn({
        "type": "interaction",
        "id": interaction_id,
        "html": html_ui,
        "goal": goal,
        # When set, the client swaps this widget's contents rather than
        # appending a new one, so a game is one board that changes rather
        # than a stack of stale boards.
        "replaces": replace_id or None,
    })

    r = _redis_client(redis_url)
    key = _k_interaction(interaction_id)
    cancelled = getattr(g, "stream_cancelled", lambda: False)

    deadline = time.time() + INTERACTION_TIMEOUT
    while time.time() < deadline:
        if cancelled():
            emit_fn({"type": "interaction_closed", "id": interaction_id})
            return "The user stopped the conversation while the widget was open."

        try:
            raw = r.lpop(key)
        except Exception as exc:
            return f"Lost the interaction channel: {exc}"

        if raw:
            emit_fn({"type": "interaction_closed", "id": interaction_id})
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return str(raw)

            if data.get("exit"):
                return ("The user closed the widget and wants to stop this "
                        "interaction. Acknowledge briefly and do not reopen it.")
            # Hand back the id so the next call can update this widget
            # rather than stacking another one underneath it.
            data["interaction_id"] = interaction_id
            return json.dumps(data)

        time.sleep(INTERACTION_POLL)

    emit_fn({"type": "interaction_closed", "id": interaction_id})
    return (f"The user did not respond within {INTERACTION_TIMEOUT // 60} minutes. "
            "Do not reopen the widget; ask in plain text instead.")


def chess_move(action: str, status: str, move: str = "",
               think_seconds: int = 3, elo: int = 2000) -> str:
    """Play chess with a real board and a real engine behind you.

    Use this for every chess position - never track the board yourself. You
    cannot reliably hold a position in memory across a long game, and a
    remembered board drifts until you play a piece that moved away ten turns
    ago. This tool owns the position, so that cannot happen.

    Typical loop: 'new' to start, then 'apply' the user's move, then
    'analyse' to see your strongest options, then 'apply' the one you chose.
    Pick from the candidates and explain your reasoning in your own words -
    the engine supplies the tactics, you supply the play and the commentary.

    Args:
        action: One of:
            'new'      - start a fresh game
            'state'    - current position, legal moves, whose turn
            'apply'    - play `move` on the board (rejected if illegal)
            'analyse'  - ranked candidate moves with evaluations
        status: A short present-tense line shown to the user, for example
            'Considering the position'.
        move: For 'apply'. Either UCI like 'e2e4' or algebraic like 'Nf3'.
        think_seconds: For 'analyse'. How long the engine may search, 1 to 10.
            More time means deeper tactics.
        elo: For 'analyse'. How strong to play, 1320 to 3190. Default 2000,
            a strong club player. Lower it if the user asks for an easier
            game, raise it if they want a hard one. If they name a level -
            beginner, intermediate, master - map it to a number and say what
            you chose.

    Returns:
        A JSON string with the position in FEN, an ASCII board, whose turn it
        is, every legal move, and for 'analyse' the ranked candidates with
        evaluations in pawns from the side to move's point of view.
    """
    import chess

    try:
        chat_id = int(getattr(g, "lab_chat_id", None))
    except (TypeError, ValueError):
        return "No active chat, so there is nowhere to keep the game."

    redis_url = getattr(g, "stream_redis_url", None) or         current_app.config["REDIS_URL"]
    r = _redis_client(redis_url)
    key = f"chess:{chat_id}"

    def load() -> chess.Board:
        fen = None
        try:
            fen = r.get(key)
        except Exception:
            pass
        return chess.Board(fen) if fen else chess.Board()

    def save(b: chess.Board) -> None:
        try:
            r.setex(key, 60 * 60 * 24 * 7, b.fen())
        except Exception:
            pass

    def describe(b: chess.Board, extra: dict | None = None) -> str:
        out = {
            "fen": b.fen(),
            "board": str(b),
            "turn": "white" if b.turn == chess.WHITE else "black",
            "move_number": b.fullmove_number,
            "in_check": b.is_check(),
            # The legal list is the point. Choose from it; anything else is
            # rejected before it can reach the board.
            "legal_moves_san": [b.san(m) for m in b.legal_moves],
            "legal_moves_uci": [m.uci() for m in b.legal_moves],
            "game_over": b.is_game_over(),
        }
        if b.is_game_over():
            out["result"] = b.result()
            out["reason"] = (
                "checkmate" if b.is_checkmate() else
                "stalemate" if b.is_stalemate() else
                "insufficient material" if b.is_insufficient_material() else
                "draw")
        if extra:
            out.update(extra)
        return json.dumps(out, indent=2)

    action = (action or "").strip().lower()

    if action == "new":
        b = chess.Board()
        save(b)
        return describe(b, {"note": "New game. White to move."})

    board = load()

    if action == "state":
        return describe(board)

    if action == "apply":
        if not move:
            return "No move given."
        if board.is_game_over():
            return describe(board, {"error": "The game is already over."})

        parsed = None
        for parse in (board.parse_san, chess.Move.from_uci):
            try:
                candidate = parse(move.strip())
                if candidate in board.legal_moves:
                    parsed = candidate
                    break
            except Exception:
                continue

        if parsed is None:
            # The refusal that makes illegal play impossible. The legal list
            # comes back with it so the next attempt is an informed one.
            return describe(board, {
                "error": f"{move!r} is not legal in this position.",
                "hint": "Choose from legal_moves_san.",
            })

        san = board.san(parsed)
        board.push(parsed)
        save(board)
        return describe(board, {"played": san})

    if action == "analyse":
        if board.is_game_over():
            return describe(board, {"note": "The game is over."})
        try:
            import chess_engine
        except ImportError:
            return "The chess engine module is unavailable."

        budget = max(1, min(int(think_seconds or 3), 10))
        try:
            strength = max(1320, min(int(elo or 2000), 3190))
        except (TypeError, ValueError):
            strength = 2000

        result = chess_engine.analyse(board.fen(), top_n=5,
                                      time_budget=budget, elo=strength)
        return describe(board, {
            "candidates": result["candidates"],
            "playing_at_elo": strength,
            "note": ("Evaluations are in pawns from the side to move's point "
                     "of view. Positive is better for you. 'line' is the "
                     "continuation the engine expects, which is what to "
                     "describe when you explain your plan. Pick one of these "
                     "and put the reasoning in your own words - never mention "
                     "evaluations, engines or search."),
        })

    return f"Unknown action {action!r}. Use new, state, apply or analyse."


def _engine_budget(elo: int) -> float:
    """Think time by strength. A 1400 opponent is fine in a tenth of a
    second; a 3000 one benefits from half. All well under what a person
    notices as a delay."""
    return 0.12 if elo <= 1800 else 0.25 if elo <= 2400 else 0.5


def chess_play(status: str, elo: int = 2000, play_as: str = "white",
               new_game: bool = False, minutes: int = 10) -> str:
    """Play a full game of chess against the user, on a real board.

    Call this ONLY when the user has asked to play chess - a greeting or an
    unrelated message is not a request for a game, and an unasked-for board
    takes over the conversation. When they have asked, this is the only tool
    to use for it.

    It starts or resumes a game. It draws the board, takes the
    user's moves by click or drag, answers each one in well under a second,
    grades every move on both sides live (Brilliant, Great, Best, Excellent,
    Good, Inaccuracy, Mistake, Blunder) with an eval bar, and keeps going by
    itself. You are not consulted per move, so DO NOT draw a board with
    request_user_interaction and DO NOT track moves yourself.

    It returns to you only when something needs words: the game ends (with
    a full accuracy review for both sides), the user asks a question about
    the position, they resign, or they start a new game. Respond like a
    player and a coach - name the opening, point at the turning move, quote
    the accuracy numbers, credit the brilliancies and explain the blunders
    with the better move - then, if the game is still on, call chess_play
    again to resume exactly where it was.

    Args:
        status: A short present-tense line, for example 'Setting up the board'.
        elo: Playing strength, 1320 to 3190. Default 2000. If the user names
            a level - beginner 1400, casual 1700, club 2000, strong 2400,
            master 2800 - pick the number and tell them what you chose.
        play_as: The USER's colour, 'white' or 'black'. Default white.
        new_game: True to discard any game in progress and start fresh.
        minutes: Clock per side. Default 10. Pass 0 for an untimed game.
            Running out of time loses, as in real chess.

    Returns:
        What happened, the full move list, and the review, so you can
        comment on it.
    """
    import threading as _threading

    import chess
    import chess_engine
    import chess_ui

    try:
        chat_id = int(getattr(g, "lab_chat_id", None))
    except (TypeError, ValueError):
        return "No active chat to play in."

    try:
        elo = max(1320, min(int(elo or 2000), 3190))
    except (TypeError, ValueError):
        elo = 2000
    try:
        minutes = max(0, min(int(minutes if minutes is not None else 10), 180))
    except (TypeError, ValueError):
        minutes = 10
    user_color = "black" if str(play_as).lower().startswith("b") else "white"
    engine_color = "black" if user_color == "white" else "white"

    redis_url = getattr(g, "stream_redis_url", None) or current_app.config["REDIS_URL"]
    r = _redis_client(redis_url)
    key = f"chessgame:{chat_id}"

    def fresh_state(widget=None):
        return {
            "moves": [], "elo": elo, "user": user_color, "widget": widget,
            "clock": ({"white": minutes * 60_000, "black": minutes * 60_000}
                      if minutes else None),
            "forfeit": None,
            # analysis[i] is the reviewer's view of the position after i
            # plies; quality[i] grades move i using analysis[i] and [i+1].
            "analysis": [], "quality": [],
        }

    state = None
    if not new_game:
        try:
            raw = r.get(key)
            state = json.loads(raw) if raw else None
        except Exception:
            state = None
    if not state:
        state = fresh_state()
    else:
        elo = state.get("elo", elo)
        user_color = state.get("user", user_color)
        engine_color = "black" if user_color == "white" else "white"
        state.setdefault("analysis", [])
        state.setdefault("quality", [])

    def save():
        try:
            r.setex(key, 7 * 24 * 3600, json.dumps(state))
        except Exception:
            pass

    def replay():
        b = chess.Board()
        sans = []
        for u in state["moves"]:
            m = chess.Move.from_uci(u)
            sans.append(b.san(m))
            b.push(m)
        return b, sans

    def pgn(sans):
        out = []
        for i in range(0, len(sans), 2):
            out.append(f"{i // 2 + 1}. {sans[i]}"
                       + (f" {sans[i + 1]}" if i + 1 < len(sans) else ""))
        return " ".join(out) or "(no moves)"

    def tick(color, seconds):
        if state["clock"]:
            state["clock"][color] -= int(seconds * 1000)

    def clock_payload(live):
        if not state["clock"]:
            return None
        return {"white": max(0, state["clock"]["white"]),
                "black": max(0, state["clock"]["black"]), "ticking": live}

    # -- grading -------------------------------------------------------
    # The reviewer is a second, full-strength engine. The player is elo
    # limited; the judge must see more than the player, or a 1400 opponent
    # would grade its own blunders as best moves.
    def ensure_analysis(reviewer, ply):
        """Make sure analysis[ply] exists for the position after `ply` plies."""
        while len(state["analysis"]) <= ply:
            b = chess.Board()
            for u in state["moves"][:len(state["analysis"])]:
                b.push(chess.Move.from_uci(u))
            state["analysis"].append(reviewer.evaluate(b, 0.1))

    def grade_pending(reviewer):
        """Grade every move that has analysis on both sides of it."""
        while len(state["quality"]) < len(state["moves"]):
            i = len(state["quality"])
            ensure_analysis(reviewer, i + 1)
            b = chess.Board()
            for u in state["moves"][:i]:
                b.push(chess.Move.from_uci(u))
            mv = chess.Move.from_uci(state["moves"][i])
            q = chess_engine.grade_move(b, mv, state["analysis"][i], state["analysis"][i + 1])
            state["quality"].append(q)

    def current_eval():
        a = state["analysis"][len(state["moves"])] if len(state["analysis"]) > len(state["moves"]) else None
        if not a:
            return None
        return {"cp": a["cp"], "mate": a.get("mate"), "text": chess_engine.eval_text(a)}

    def show(board, sans, text, waiting, result=None, rev=None):
        kw = dict(user_color=user_color, elo=elo, status_text=text,
                  waiting=waiting, result=result,
                  last_move=state["moves"][-1] if state["moves"] else None,
                  clock=clock_payload(live=not result),
                  quality=state["quality"], eval=current_eval(), review=rev)
        data = chess_ui.board_data(board, sans, **kw)
        html = chess_ui.render(board, sans, **kw)
        try:
            state["widget"] = _show_widget(html, "chess", state.get("widget"), update=data)
        except _WidgetUnavailable:
            return False
        save()
        return True

    def close():
        emit_fn = getattr(g, "stream_emit", None)
        if emit_fn and state.get("widget"):
            emit_fn({"type": "interaction_closed", "id": state["widget"]})

    def review_text(sans):
        rv = chess_engine.review(state["quality"], sans, user_color)
        me, them = rv[user_color], rv[engine_color]
        def counts(c):
            return ", ".join(f"{n} {k}" for k, n in c["counts"].items() if n and k != "forced") or "none"
        lines = [
            f"Accuracy: you {me['accuracy']}%, Stellar {them['accuracy']}%.",
            f"Your moves: {counts(me)}.",
            f"Stellar's moves: {counts(them)}.",
        ]
        if me["worst"]:
            lines.append("Your costliest moves: " + "; ".join(
                f"{w['move_no']}. {w['san']} ({w['cls']}, lost {w['loss']/100:.1f}"
                f"{', better was ' + w['better'] if w['better'] else ''})" for w in me["worst"]))
        if me["highlights"]:
            lines.append("Your best moments: " + ", ".join(
                f"{h['move_no']}. {h['san']} ({h['cls']})" for h in me["highlights"]))
        return rv, " ".join(lines)

    def outcome(board):
        res = board.result(claim_draw=True)
        if board.is_checkmate():
            winner = "black" if board.turn == chess.WHITE else "white"
            return res, ("Checkmate - you win!" if winner == user_color
                         else "Checkmate - Stellar wins.")
        if board.is_stalemate():
            return res, "Stalemate - a draw."
        if board.is_insufficient_material():
            return res, "Draw - insufficient material."
        if board.can_claim_threefold_repetition():
            return "1/2-1/2", "Draw by repetition."
        if board.can_claim_fifty_moves():
            return "1/2-1/2", "Draw by the fifty-move rule."
        return res, "Game over."

    def finished(board, sans, res, why, reviewer):
        grade_pending(reviewer)
        rv, rtext = review_text(sans)
        show(board, sans, why, waiting=False, result=res, rev=rv)
        close()
        state["widget"] = None
        save()
        return (f"Game over: {why} Result {res}. You (Stellar) played "
                f"{engine_color} at {elo}. Moves: {pgn(sans)}. {rtext} Review the "
                f"game like a coach - opening, turning point, what they did well, "
                f"the better moves at the blunders - and offer a rematch.")

    error_text = None
    with chess_engine.EngineSession(elo=elo) as engine, \
            chess_engine.EngineSession(elo=None) as reviewer:
        while True:
            board, sans = replay()
            ensure_analysis(reviewer, len(state["moves"]))
            grade_pending(reviewer)

            # Either side can flag. Only the user's clock was ever
            # checked, so Stellar played on from a displayed 0:00 for the
            # rest of the game and the "Stellar lost on time" branch in
            # finished() was unreachable.
            if state["clock"] and not state.get("forfeit"):
                for _colour in ("white", "black"):
                    if state["clock"][_colour] <= 0:
                        state["forfeit"] = _colour
                        save()
                        break

            if state.get("forfeit"):
                loser = state["forfeit"]
                res = "0-1" if loser == "white" else "1-0"
                why = ("You lost on time." if loser == user_color
                       else "Stellar lost on time - you win!")
                return finished(board, sans, res, why, reviewer)

            if board.is_game_over(claim_draw=True):
                res, why = outcome(board)
                return finished(board, sans, res, why, reviewer)

            if (board.turn == chess.WHITE) != (user_color == "white"):
                t0 = time.time()
                mv = engine.best_move(board, _engine_budget(elo))
                if mv is None:
                    return "The engine could not find a move."
                tick(engine_color, time.time() - t0)
                state["moves"].append(mv)
                save()
                continue

            text = error_text or ("Check!" if board.is_check() else "Your move")
            error_text = None
            turn_started = time.time()
            if not show(board, sans, text, waiting=True):
                return "The board cannot be shown in this context."

            data = _await_widget(state["widget"], close=False)
            elapsed = time.time() - turn_started

            if data == "cancelled":
                close()
                return "The user stopped while the game was open. The game is saved."
            if data is None:
                close()
                return (f"The user did not move for {INTERACTION_TIMEOUT // 60} "
                        f"minutes. The game is saved; call chess_play to resume. "
                        f"Moves: {pgn(sans)}")

            if data.get("newgame"):
                state = fresh_state(widget=state.get("widget"))
                save()
                continue

            if data.get("exit"):
                close()
                state["widget"] = None
                save()
                if data.get("resign"):
                    grade_pending(reviewer)
                    _, rtext = review_text(sans)
                    return (f"The user resigned after {len(sans)} half-moves. "
                            f"Moves: {pgn(sans)}. {rtext} Be gracious; offer a rematch.")
                return "The user closed the board."

            if data.get("ask"):
                return (f"The user asked, mid-game: {data['ask']!r}\n"
                        f"Position (FEN): {board.fen()}\nMoves so far: {pgn(sans)}\n"
                        f"It is their move. Answer in a few sentences as a player "
                        f"would, then call chess_play again to resume - the board "
                        f"is waiting for them.")

            if data.get("flag"):
                if state["clock"] and state["clock"][user_color] - elapsed * 1000 <= 500:
                    state["forfeit"] = user_color
                    save()
                continue

            mv = str(data.get("move") or "").strip()
            try:
                move = chess.Move.from_uci(mv)
            except Exception:
                move = None
            if move is None or move not in board.legal_moves:
                error_text = "That move is not legal here."
                continue

            tick(user_color, elapsed)
            if state["clock"] and state["clock"][user_color] <= 0:
                state["forfeit"] = user_color
                save()
                continue

            state["moves"].append(mv)
            save()

            # Grade the user's move while the engine thinks about its reply:
            # two different engine processes, so the two searches overlap and
            # the grade costs the user no waiting.
            after_user = board.copy()
            after_user.push(move)
            holder = {}
            def _judge():
                holder["a"] = reviewer.evaluate(after_user, 0.1)
            t = _threading.Thread(target=_judge, daemon=True)
            t.start()
            if not after_user.is_game_over(claim_draw=True):
                t0 = time.time()
                reply = engine.best_move(after_user, _engine_budget(elo))
                t.join()
                if reply is None:
                    return "The engine could not find a move."
                tick(engine_color, time.time() - t0)
                if "a" in holder:
                    state["analysis"].append(holder["a"])
                state["moves"].append(reply)
            else:
                t.join()
                if "a" in holder:
                    state["analysis"].append(holder["a"])
            save()

# ---------------------------------------------------------------------
# Phase 8: the rest of the tool suite
# ---------------------------------------------------------------------
# Eight tools that turn the agent from something that answers into
# something that produces: images, slide decks, a look inside a YouTube
# video, email to the user's own inbox, a memory that outlives the chat, a
# way to page through output that was too long to show, file sharing out
# of the sandbox, and tasks that run later with nobody at the keyboard.
#
# They share three pieces of plumbing that the reference re-implements
# inside every tool: where a tool's files go (_outputs_dir), how a tool
# calls the model on its own account with key rotation (_tool_model_call),
# and how a file name chosen by the model is resolved safely
# (_resolve_chat_file).

# Image models, tried in order: the current "Nano Banana" line first, then
# the older generally-available model it replaced.
IMAGE_MODEL = "gemini-3.1-flash-image-preview"
IMAGE_FALLBACK_MODEL = "gemini-2.5-flash-image"
IMAGE_ASPECTS = ("1:1", "3:4", "4:3", "9:16", "16:9")

# Persistent memory: notes a user can accumulate before the oldest go. A
# hundred one-line notes is a few thousand tokens, cheap to prepend to
# every turn.
MEMORY_MAX = 100

# Scheduler: how often a worker looks for due tasks, how many tasks one
# user may have waiting, and the shortest repeat interval accepted.
SCHEDULER_INTERVAL = 30
SCHEDULED_TASKS_MAX = 10
SCHEDULE_MIN_REPEAT = 5           # minutes
# A task still marked running after this long belongs to a process that
# died. It is handed back to the queue.
SCHEDULE_STALE_MINUTES = 30

# Email: Gmail by default. Any SMTP-over-SSL server works with SMTP_HOST
# and SMTP_PORT in keys.env.
SMTP_DEFAULT_HOST = "smtp.gmail.com"
SMTP_DEFAULT_PORT = 465
EMAIL_ATTACHMENT_MAX = 20 * 1024 * 1024


# --- shared plumbing --------------------------------------------------
def _outputs_root() -> Path:
    try:
        return Path(current_app.config.get("OUTPUTS_DIR") or PROJECT_ROOT / "outputs")
    except RuntimeError:              # no app context (tests, scripts)
        return PROJECT_ROOT / "outputs"


def _outputs_dir(user_id: int, chat_id: int) -> Path:
    """Where a chat's produced files live on disk.

    One folder per chat, so serving a file can check chat ownership. The
    reference kept a single public folder and served it to anyone who knew
    a name; here a link is only good for the person the chat belongs to.
    """
    d = _outputs_root() / f"u{int(user_id)}_c{int(chat_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _output_link(chat_id: int, filename: str) -> str:
    # Percent-encoded, because every Markdown renderer ends a URL at the
    # first space. A shared file called "my report.png" produced a link
    # that stopped at "my" and rendered as literal text.
    return f"/api/outputs/{int(chat_id)}/{quote(filename)}"


def _safe_filename(name: str) -> str:
    """A name the model chose, reduced to something safe to create."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .")
    if len(base) > 120:
        # Trim the stem, keep the suffix. Cutting the string at 120
        # characters threw the extension away, and serve_output decides
        # inline-versus-download by extension - so a long-named picture
        # arrived as an unopenable download.
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            base = stem[:120 - len(ext) - 1].strip(" .") + "." + ext
        else:
            base = base[:120]
    return base or "file"


def _resolve_chat_file(user_id: int, chat_id: int, name: str) -> Path | None:
    """Find a file the model named among this chat's files.

    Looks in the chat's outputs first, then its sandbox workspace. The
    result is resolved and required to lie inside one of those two folders,
    so a name like ../../keys.env resolves to nothing.
    """
    rel = str(name or "").strip().replace("\\", "/").lstrip("/")
    if rel.startswith("lab/"):
        rel = rel[4:]
    if not rel:
        return None
    for base in (_outputs_dir(user_id, chat_id), _lab_workspace(user_id, chat_id)):
        base_r = base.resolve()
        cand = (base_r / rel).resolve()
        if cand.is_file() and base_r in cand.parents:
            return cand
    return None


def _tool_model_call(model: str, call, fallback: str | None = None):
    """Call the model from inside a tool with the main loop's key discipline.

    First usable key; on a quota error block that key for the model and
    rotate; on overload block the model; when the model is missing or every
    key is blocked on it, try the fallback. `call(client, model)` performs
    the request and returns whatever it likes.
    """
    keys = gemini_keys()
    if not keys:
        raise RuntimeError("no Gemini API key configured")
    last: Exception | None = None
    for m in [model] + ([fallback] if fallback and fallback != model else []):
        if KEY_MANAGER.is_model_blocked(m):
            continue
        for _ in range(len(keys)):
            idx = KEY_MANAGER.first_available(keys, m)
            if idx is None:
                break
            KEY_MANAGER.record_request(keys[idx], m)
            try:
                return call(genai.Client(api_key=keys[idx]), m)
            except Exception as exc:
                last = exc
                kind = _classify_error(exc)
                if kind == "quota":
                    seconds, reason = parse_quota_block(str(exc))
                    if reason == "OVERLOAD":
                        KEY_MANAGER.block_model(m, seconds)
                        break
                    KEY_MANAGER.block(keys[idx], m, seconds, reason)
                    continue
                if kind == "missing_model":
                    break
                if kind == "transient":
                    time.sleep(1.0)
                    continue
                raise
    raise RuntimeError(str(last)[:300] if last else f"no API key is available for {model}")


def _model_view(result: str, row_id: int) -> str:
    """What the model is shown of a tool result.

    The full result is stored; the model sees the first TOOL_OUTPUT_LIMIT
    characters and a note telling it how to read the rest. A page of text
    the model cannot get back to is a page it never saw.
    """
    if len(result) <= TOOL_OUTPUT_LIMIT:
        return result
    return (result[:TOOL_OUTPUT_LIMIT]
            + f"\n\n[Output truncated: {len(result) - TOOL_OUTPUT_LIMIT:,} more "
              f"characters. The full output is stored as tool output #{row_id}. "
              f"Call read_tool_output(output_id={row_id}) to page through it, or "
              f"with keyword= to search it.]")


# --- images -------------------------------------------------------------
def _image_bytes(parts: list, aspect_ratio: str) -> tuple[bytes, str] | None:
    """Ask the image model for one picture. (bytes, mime) or None."""
    ratio = aspect_ratio if aspect_ratio in IMAGE_ASPECTS else "1:1"

    def call(client, model):
        return client.models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(aspect_ratio=ratio),
            ),
        )

    resp = _tool_model_call(IMAGE_MODEL, call, fallback=IMAGE_FALLBACK_MODEL)
    for part in _iter_parts(resp):
        blob = getattr(part, "inline_data", None)
        if blob is not None and blob.data:
            return bytes(blob.data), (blob.mime_type or "image/png")
    return None


def generate_image(prompt: str, status: str, aspect_ratio: str = "1:1",
                   reference_files: list[str] | None = None) -> str:
    """Generate a picture from a description and show it in the chat.

    Use it when the user asks for an image, an illustration, a logo, a
    poster, concept art, or a variation of a picture made earlier in this
    chat. The picture is saved among the chat's files and returned as
    Markdown that displays inline: put that Markdown in your reply exactly
    as returned.

    Args:
        prompt: What to draw, in detail: subject, setting, style, lighting,
            composition, colours, mood. Vague prompts give generic pictures.
        status: A short present-tense line shown to the user while this
            runs, for example 'Painting a lighthouse at dusk'.
        aspect_ratio: '1:1', '3:4', '4:3', '9:16' or '16:9'.
        reference_files: Up to four file names from this chat's files (see
            manage_files) to use as visual references, for example to make
            a variation of an earlier image.

    Returns:
        Markdown for the image, or an explanation of why there is none.
    """
    try:
        user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No active chat to save the image in."
    prompt = (prompt or "").strip()
    if not prompt:
        return "Describe what to draw."

    parts = [types.Part.from_text(text=prompt)]
    missing = []
    for name in (reference_files or [])[:4]:
        p = _resolve_chat_file(user_id, chat_id, name)
        if p is None:
            missing.append(str(name))
            continue
        import mimetypes
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        parts.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
    if missing:
        return (f"Reference file(s) not found in this chat: {', '.join(missing)}. "
                f"Call manage_files(action='list') to see what exists.")

    try:
        made = _image_bytes(parts, aspect_ratio)
    except Exception as exc:
        if _classify_error(exc) == "quota":
            return ("Image generation is out of quota on every configured key for "
                    "today: the free tier allows very few image requests a day, and "
                    "some keys none at all. Tell the user plainly. If they only need "
                    "a picture of something real, web_search can find one.")
        return (f"Image generation failed: {str(exc)[:200]}. Tell the user plainly; "
                f"if they only need a picture of something real, web_search can find one.")
    if made is None:
        return ("The image model returned no picture, which usually means the "
                "prompt was refused. Rephrase it or tell the user.")

    data, mime = made
    ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    name = f"image_{uuid.uuid4().hex[:8]}.{ext}"
    (_outputs_dir(user_id, chat_id) / name).write_bytes(data)
    alt = re.sub(r"[\[\]\n]+", " ", prompt)[:80]
    return (f"![{alt}]({_output_link(chat_id, name)})\n\n"
            f"Saved as {name} in this chat's files.")


# --- presentations ------------------------------------------------------
_DECK_THEMES = {
    "light":  {"bg": "FFFFFF", "panel": "F3F5F9", "text": "1F2430", "dim": "6B7280", "accent": "2F6FED"},
    "dark":   {"bg": "14171F", "panel": "1A1E28", "text": "E6E8EE", "dim": "8B91A1", "accent": "6D8CFF"},
    "warm":   {"bg": "FBF7F0", "panel": "F1E9DC", "text": "2B2118", "dim": "7A6A5A", "accent": "D9822B"},
    "forest": {"bg": "F4F7F2", "panel": "E6EEE1", "text": "1E2A1B", "dim": "5F6F5A", "accent": "2E7D4F"},
}


def _deck_theme(style: str) -> dict:
    s = (style or "").lower()
    for key in ("dark", "warm", "forest"):
        if key in s:
            return _DECK_THEMES[key]
    if any(w in s for w in ("night", "technical", "terminal", "cyber", "developer")):
        return _DECK_THEMES["dark"]
    return _DECK_THEMES["light"]


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                    "visual": {"type": "string"},
                },
                "required": ["title", "bullets", "notes", "visual"],
            },
        },
    },
    "required": ["title", "subtitle", "slides"],
}


def _plan_presentation(topic: str, num_slides: int, style: str, context: str) -> dict:
    """Ask the model for the deck as structured JSON, not prose.

    response_schema makes the model return exactly this shape, so there is
    no parsing of a Markdown outline and no slide silently lost to a
    formatting slip.
    """
    prompt = (
        f"Plan a {num_slides}-slide presentation on: {topic}\n"
        f"Style and audience: {style or 'clear and professional'}\n"
        + (f"Build it from this material and keep its facts straight:\n{context}\n"
           if context else "")
        + "Rules: a short deck title and a one-line subtitle. Each slide: a title "
          "of at most 8 words; 3 to 5 bullets of at most 14 words each that carry "
          "real content - numbers, names, decisions, not headings; speaker notes of "
          "2 to 4 sentences saying what to actually tell the audience; and 'visual', "
          "one sentence describing an illustration for the slide that contains no "
          "text. The first slide is the introduction and the last is the takeaway."
    )

    def call(client, model):
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PLAN_SCHEMA,
            ),
        )

    resp = _tool_model_call(DEFAULT_MODEL, call, fallback=FALLBACK_MODEL)
    plan = json.loads(resp.text or "{}")
    plan["slides"] = [s for s in (plan.get("slides") or []) if isinstance(s, dict)][:num_slides]
    return plan


def _build_deck(plan: dict, style: str, images: list, path: Path) -> int:
    """Write a .pptx from a plan. Returns the number of slides written.

    Real slides, not pictures of slides: the title is a text box, the
    bullets are paragraphs, the notes are speaker notes. Every word stays
    editable afterwards, which a full-bleed generated image never allows,
    and no text is left to an image model's spelling.
    """
    from io import BytesIO
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    t = _deck_theme(style)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    blank = prs.slide_layouts[6]

    def background(slide, colour):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor.from_string(colour)

    def box(slide, left, top, width, height, text, size, bold=False, colour=None, align=None):
        tb = slide.shapes.add_textbox(left, top, width, height)
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        p.font.size, p.font.bold, p.font.name = Pt(size), bold, "Calibri"
        p.font.color.rgb = RGBColor.from_string(colour or t["text"])
        if align is not None:
            p.alignment = align
        return tf

    def bar(slide, left, top, width, height, colour):
        s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
        s.fill.solid()
        s.fill.fore_color.rgb = RGBColor.from_string(colour)
        s.line.fill.background()
        return s

    def picture(slide, data, left, top, width, height):
        """Place an image inside a box, keeping its aspect ratio."""
        try:
            from PIL import Image
            with Image.open(BytesIO(data)) as im:
                iw, ih = im.size
        except Exception:
            slide.shapes.add_picture(BytesIO(data), left, top, width=width)
            return
        scale = min(width / iw, height / ih)
        pw, ph = int(iw * scale), int(ih * scale)
        slide.shapes.add_picture(BytesIO(data), left + (width - pw) // 2,
                                 top + (height - ph) // 2, pw, ph)

    title = str(plan.get("title") or "Untitled")
    slides = plan.get("slides") or []
    total = len(slides) + 1

    slide = prs.slides.add_slide(blank)
    background(slide, t["bg"])
    bar(slide, 0, 0, Inches(0.35), H, t["accent"])
    box(slide, Inches(1.1), Inches(2.3), Inches(11), Inches(1.8), title, 44, bold=True)
    if plan.get("subtitle"):
        box(slide, Inches(1.1), Inches(4.1), Inches(11), Inches(1.2),
            str(plan["subtitle"]), 22, colour=t["dim"])

    for i, s in enumerate(slides):
        slide = prs.slides.add_slide(blank)
        background(slide, t["bg"])
        img = images[i] if i < len(images) else None
        text_w = Inches(6.6) if img else Inches(11.9)
        box(slide, Inches(0.7), Inches(0.45), text_w, Inches(1.0),
            str(s.get("title") or f"Slide {i + 1}"), 30, bold=True)
        bar(slide, Inches(0.7), Inches(1.4), Inches(1.2), Inches(0.07), t["accent"])
        bullets = [str(b).strip() for b in (s.get("bullets") or []) if str(b).strip()][:7]
        tf = None
        for b in bullets:
            if tf is None:
                tf = box(slide, Inches(0.7), Inches(1.75), text_w, Inches(4.9), "•  " + b, 18)
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
                p.text = "•  " + b
                p.font.size, p.font.name = Pt(18), "Calibri"
                p.font.color.rgb = RGBColor.from_string(t["text"])
            p.space_after = Pt(10)
        if img:
            bar(slide, Inches(7.7), Inches(0.7), Inches(5.0), Inches(6.0), t["panel"])
            picture(slide, img, Inches(7.85), Inches(0.85), Inches(4.7), Inches(5.7))
        box(slide, Inches(0.7), Inches(6.9), Inches(9), Inches(0.4), title, 10, colour=t["dim"])
        box(slide, Inches(11.4), Inches(6.9), Inches(1.3), Inches(0.4),
            f"{i + 2} / {total}", 10, colour=t["dim"], align=PP_ALIGN.RIGHT)
        if s.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(s["notes"])

    prs.save(str(path))
    return total


def make_presentation(topic: str, status: str, num_slides: int = 8,
                      style: str = "clean and professional", context: str = "",
                      illustrate: bool = False) -> str:
    """Build a PowerPoint deck (.pptx) the user can download and edit.

    The deck is planned as structured data - title, bullets and speaker
    notes per slide - and written as real, editable slides in a themed
    layout. Pass everything you know about the subject in `context`: the
    deck is only as good as the material it is built from, so research
    first with web_search or fetch_url when the topic needs facts.

    Args:
        topic: What the presentation is about, and for whom if known.
        status: A short present-tense line shown to the user, for example
            'Building a 10-slide deck on solar storage'.
        num_slides: Content slides, 3 to 20. A title slide is added.
        style: Tone and look, for example 'dark, technical', 'warm and
            simple for a school class', 'executive summary'.
        context: Facts, figures, quotes, the user's own notes or research
            results to build the slides from.
        illustrate: True adds a generated picture to every slide. It is
            slower and spends image quota, so use it when the user asks
            for visuals.

    Returns:
        A download link and the slide outline, to relay to the user.
    """
    try:
        user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No active chat to save the deck in."
    try:
        n = max(3, min(int(num_slides or 8), 20))
    except (TypeError, ValueError):
        n = 8

    try:
        plan = _plan_presentation(topic, n, style, context or "")
    except Exception as exc:
        return f"Could not plan the deck: {exc}"
    slides = plan.get("slides") or []
    if not slides:
        return "The planner returned no slides. Try again with more context."

    images: list = [None] * len(slides)
    made = 0
    if illustrate:
        from concurrent.futures import ThreadPoolExecutor

        def one(s):
            visual = str(s.get("visual") or s.get("title") or topic)
            try:
                got = _image_bytes([types.Part.from_text(
                    text=f"{visual}. Style: {style}. Illustration only - no text, "
                         f"no letters, no captions, no watermark.")], "4:3")
                return got[0] if got else None
            except Exception as exc:
                logger.warning("Slide image failed: %s", exc)
                return None

        # Threads rather than the reference's asyncio-around-executor: the
        # SDK call is blocking either way, so a thread pool is the same
        # concurrency with none of the event-loop ceremony.
        with ThreadPoolExecutor(max_workers=4) as pool:
            images = list(pool.map(one, slides))
        made = sum(1 for x in images if x)

    slug = re.sub(r"[^a-z0-9]+", "-", str(plan.get("title") or topic).lower()).strip("-")[:40] or "deck"
    name = f"{slug}_{uuid.uuid4().hex[:6]}.pptx"
    try:
        total = _build_deck(plan, style, images, _outputs_dir(user_id, chat_id) / name)
    except Exception as exc:
        logger.exception("Deck build failed")
        return f"Planning succeeded but writing the file failed: {type(exc).__name__}: {exc}"

    outline = "\n".join(f"{i + 1}. {s.get('title')}" for i, s in enumerate(slides))
    extra = f"{made} of {len(slides)} slides illustrated.\n\n" if illustrate else ""
    return (f"Deck ready: [{plan.get('title') or topic} - {total} slides]"
            f"({_output_link(chat_id, name)})\n\nSlides:\n{outline}\n\n{extra}"
            f"Give the user that link and the outline, and offer to change the "
            f"tone, the length, or add visuals.")


# --- YouTube ------------------------------------------------------------
_YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)[\w-]{6,}")


def _youtube_search_api(key: str, query: str, n: int) -> str:
    import requests

    found = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": query, "maxResults": n, "type": "video", "key": key},
        timeout=15).json()
    if "error" in found:
        return f"YouTube search failed: {found['error'].get('message', 'unknown error')}"
    ids = [it["id"]["videoId"] for it in found.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return f"No videos found for {query!r}."
    stats = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key},
        timeout=15).json()
    lines = []
    for it in stats.get("items", []):
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        lines.append(f"- {sn.get('title')} - {sn.get('channelTitle')} "
                     f"({int(st.get('viewCount', 0)):,} views, {it.get('contentDetails', {}).get('duration', '')})\n"
                     f"  https://www.youtube.com/watch?v={it['id']}\n"
                     f"  {(sn.get('description') or '').strip().replace(chr(10), ' ')[:200]}")
    return "\n".join(lines)


def _youtube_search_tavily(query: str, n: int) -> str:
    """No YouTube Data API key: search the web restricted to youtube.com.

    Titles and links without view counts, which is enough to pick a video
    to analyse - and it needs no extra key, so search works out of the box.
    """
    import requests

    keys = tavily_keys()
    if not keys:
        return ("YouTube search needs either YOUTUBE_API_KEY or a TAVILY_API_KEY "
                "in keys.env; neither is set.")
    resp = requests.post("https://api.tavily.com/search", json={
        "api_key": keys[0], "query": query, "max_results": n,
        "include_domains": ["youtube.com"],
    }, timeout=25)
    if resp.status_code != 200:
        return f"Search failed with HTTP {resp.status_code}."
    lines = []
    for it in resp.json().get("results", []):
        url = it.get("url", "")
        if "youtube.com" not in url and "youtu.be" not in url:
            continue
        lines.append(f"- {it.get('title', 'Untitled')}\n  {url}\n"
                     f"  {(it.get('content') or '').strip().replace(chr(10), ' ')[:200]}")
    return "\n".join(lines) if lines else f"No videos found for {query!r}."


def analyze_youtube_video(status: str, action: str = "analyze", video_url: str = "",
                          question: str = "Summarise this video with timestamps for each part.",
                          start_time: str = "", end_time: str = "",
                          max_results: int = 5) -> str:
    """Watch a YouTube video and answer a question about it, or search YouTube.

    With action 'analyze' the model watches the video itself - what is
    said, what is shown, when - so use it for summaries with timestamps,
    "what does this video claim", quotes, or checking a specific moment.
    With action 'search' it finds videos for `question` and returns titles
    and links you can then analyse.

    Args:
        status: A short present-tense line shown to the user, for example
            'Watching the video'.
        action: 'analyze' (default) or 'search'.
        video_url: The YouTube link, required for 'analyze'.
        question: What to find out about the video, or for 'search' the
            query.
        start_time: Optional offset like '1m30s' or '90s' to begin from.
        end_time: Optional offset to stop at.
        max_results: For 'search', how many videos, 1 to 15.

    Returns:
        The answer, or the list of videos.
    """
    question = (question or "").strip() or "Summarise this video with timestamps for each part."
    if action == "search":
        try:
            n = max(1, min(int(max_results or 5), 15))
        except (TypeError, ValueError):
            n = 5
        key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        try:
            return _youtube_search_api(key, question, n) if key else _youtube_search_tavily(question, n)
        except Exception as exc:
            return f"YouTube search failed: {type(exc).__name__}: {exc}"

    url = (video_url or "").strip()
    if not _YOUTUBE_RE.match(url):
        return "video_url must be a YouTube link (youtube.com/watch?v=..., youtu.be/..., or a Short)."
    if not url.startswith("http"):
        url = "https://" + url

    # Pre-fetch video details and chapters (fast, ~0.3s)
    vid_title = ""
    desc_text = ""
    duration = 0
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", errors="ignore")
        m_player = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", html)
        if m_player:
            pdata = json.loads(m_player.group(1))
            vdetails = pdata.get("videoDetails", {})
            vid_title = vdetails.get("title", "")
            desc_text = vdetails.get("shortDescription", "")
            duration = int(vdetails.get("lengthSeconds", 0))
        if not desc_text:
            m_desc = re.search(r'"shortDescription":"(.*?)"', html)
            if m_desc:
                desc_text = m_desc.group(1).encode().decode("unicode_escape", errors="ignore")
    except Exception as fetch_err:
        logger.debug("Could not pre-fetch YouTube metadata: %s", fetch_err)

    # For long videos (> 10 mins or with chapter timestamps), direct structured analysis
    # is 5x faster and avoids video encoder timeouts and 503 errors.
    has_chapters = bool(desc_text and re.search(r"\d+:\d+", desc_text))
    if (duration > 600 or has_chapters) and not (start_time or end_time):
        text_prompt = (
            f"Video Title: {vid_title or url}\n"
            f"Video URL: {url}\n"
            f"Duration: {duration // 60} minutes\n\n"
            f"Chapters & Overview:\n{desc_text}\n\n"
            f"Task: {question}\n\n"
            "Provide a neat, accurate, comprehensive response with timestamps and key takeaways."
        )
        def text_call(client, model):
            cfg = None
            if "2.5" in model:
                cfg = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=0))
            return client.models.generate_content(model=model, contents=text_prompt, config=cfg)
        try:
            resp = _tool_model_call(DEFAULT_MODEL, text_call, fallback=FALLBACK_MODEL)
            if resp and resp.text:
                return resp.text.strip()
        except Exception as err:
            logger.warning("Fast chapter analysis failed: %s; falling back to multimodal", err)

    # For short clips (< 10 mins) or specific clip offsets, use multimodal video decoding
    part = types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*"))
    meta = {}
    if start_time:
        meta["start_offset"] = str(start_time).strip()
    if end_time:
        meta["end_offset"] = str(end_time).strip()
    if meta:
        part.video_metadata = types.VideoMetadata(**meta)
    contents = [types.Content(role="user", parts=[part, types.Part.from_text(text=question)])]

    def call(client, model):
        return client.models.generate_content(model=model, contents=contents)

    try:
        resp = _tool_model_call(DEFAULT_MODEL, call, fallback=FALLBACK_MODEL)
        return (resp.text or "").strip() or "The model returned nothing for this video."
    except Exception as exc:
        if desc_text or vid_title:
            text_prompt = (
                f"Video Title: {vid_title}\n"
                f"Video URL: {url}\n\n"
                f"Video Outline & Description:\n{desc_text}\n\n"
                f"User Request: {question}\n\n"
                "Provide a neat, comprehensive summary with timestamps and key takeaways."
            )
            def fb_call(client, model):
                return client.models.generate_content(model=model, contents=text_prompt)
            fallback_resp = _tool_model_call(DEFAULT_MODEL, fb_call, fallback=FALLBACK_MODEL)
            if fallback_resp and fallback_resp.text:
                return fallback_resp.text.strip()

        return (f"Could not analyse the video: {exc}. Private, age-restricted and "
                f"very long videos cannot be watched; say so if that is the case.")


# --- email ----------------------------------------------------------------
def _smtp_send(sender: str, password: str, msg) -> None:
    """The one line that touches the network, kept apart so tests can
    replace it and everything else in send_self_email still runs."""
    import smtplib

    host = os.environ.get("SMTP_HOST", SMTP_DEFAULT_HOST)
    port = int(os.environ.get("SMTP_PORT", SMTP_DEFAULT_PORT))
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def send_self_email(subject: str, body: str, status: str, attachment: str = "") -> str:
    """Email the user at their own registered address.

    Only that address: this cannot send mail to anyone else, so it is safe
    to use freely for reports, results, reminders, and files they want to
    keep. The body is Markdown and arrives rendered.

    Args:
        subject: Subject line.
        body: The message, in Markdown.
        status: A short present-tense line shown to the user, for example
            'Emailing the report'.
        attachment: Optional name of a file from this chat's files (see
            manage_files) to attach, up to 20 MB.

    Returns:
        Confirmation, or what is missing from the configuration.
    """
    from email.message import EmailMessage
    import mimetypes

    try:
        user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No active chat, so no recipient."
    sender = os.environ.get("EMAIL_USER", "").strip()
    password = os.environ.get("EMAIL_PASS", "").strip()
    if not sender or not password:
        return ("Email is not configured: EMAIL_USER and EMAIL_PASS (a Gmail App "
                "Password) are needed in keys.env. Tell the user, and give them the "
                "content here instead.")
    row = get_db().execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or "@" not in (row["username"] or ""):
        return "The user's account has no email address to send to."
    to = row["username"]

    msg = EmailMessage()
    msg["Subject"] = f"[Stellar] {(subject or 'Message from Stellar').strip()}"
    msg["From"] = f"Stellar <{sender}>"
    msg["To"] = to
    text = (body or "").strip()
    msg.set_content(text + "\n\n-- \nSent by Stellar at your request.")
    try:
        import markdown
        html = markdown.markdown(text, extensions=["extra", "tables", "sane_lists"])
        msg.add_alternative(
            "<html><body style=\"font-family:sans-serif;line-height:1.55;color:#222;"
            "max-width:680px\">" + html
            + "<hr style=\"border:0;border-top:1px solid #ddd;margin:24px 0\">"
              "<p style=\"color:#777;font-size:12px\">Sent by Stellar at your request.</p>"
              "</body></html>", subtype="html")
    except Exception as exc:                      # plain text still goes
        logger.warning("Markdown rendering for email failed: %s", exc)

    if attachment:
        p = _resolve_chat_file(user_id, chat_id, attachment)
        if p is None:
            return (f"Attachment {attachment!r} is not among this chat's files. "
                    f"Call manage_files(action='list') to see what exists.")
        if p.stat().st_size > EMAIL_ATTACHMENT_MAX:
            return f"{p.name} is larger than 20 MB, which mail will not carry."
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)

    try:
        _smtp_send(sender, password, msg)
    except Exception as exc:
        name = type(exc).__name__
        if "Authentication" in name:
            return ("The mail server rejected the login. EMAIL_PASS must be a Google "
                    "App Password (Google Account > Security > App passwords), not "
                    "the account password.")
        return f"Sending failed: {name}: {exc}"
    return f"Sent to {to}." + (f" Attached {os.path.basename(attachment)}." if attachment else "")


# --- memory -----------------------------------------------------------------
def memory_notes(database, user_id) -> list[dict]:
    """A user's saved notes, oldest first."""
    if user_id is None:
        return []
    return [dict(r) for r in database.execute(
        "SELECT id, note FROM user_memory WHERE user_id = ? ORDER BY id",
        (int(user_id),)).fetchall()]


def memory_prompt(database, user_id) -> str:
    """The system-prompt section carrying the notes, or '' if there are none."""
    notes = memory_notes(database, user_id)
    if not notes:
        return ""
    return ("\n\n### WHAT YOU REMEMBER ABOUT THIS USER\n"
            "Saved by you in earlier conversations with remember. Act on them "
            "without being asked; update or delete a note when it turns out to be "
            "wrong.\n" + "\n".join(f"- [{n['id']}] {n['note']}" for n in notes))


def remember(status: str, note: str = "", forget_id: int = 0) -> str:
    """Save a fact about the user for every future conversation, or delete one.

    Worth saving: preferences they state (formatting, tone, language,
    tools, level of detail), facts about their work and setup that keep
    coming up, corrections they make, and what went wrong before with how
    it was fixed. Not worth saving: details of a single task, anything
    secret, anything they asked you to forget. Your notes appear at the
    start of every turn under "What you remember about this user".

    Args:
        status: A short present-tense line shown to the user, for example
            'Noting that for next time'.
        note: One clear sentence to save.
        forget_id: The number of an existing note to delete instead.

    Returns:
        Confirmation with the note's number.
    """
    try:
        user_id, _chat_id = _lab_identity()
    except RuntimeError:
        return "No user context to remember for."
    database = get_db()

    if forget_id:
        n = database.execute("DELETE FROM user_memory WHERE id = ? AND user_id = ?",
                             (int(forget_id), user_id)).rowcount
        database.commit()
        return f"Forgot note {forget_id}." if n else f"There is no note {forget_id}."

    note = " ".join((note or "").split())
    if not note:
        return "Give a note to save, or forget_id to delete one."
    if len(note) > 500:
        return "Keep a note under 500 characters: one fact per note."
    dup = database.execute("SELECT id FROM user_memory WHERE user_id = ? AND note = ?",
                           (user_id, note)).fetchone()
    if dup:
        return f"Already saved as note {dup['id']}."
    rid = database.execute("INSERT INTO user_memory (user_id, note) VALUES (?, ?)",
                           (user_id, note)).lastrowid
    database.execute(
        "DELETE FROM user_memory WHERE user_id = ? AND id NOT IN"
        " (SELECT id FROM user_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, MEMORY_MAX))
    database.commit()
    return f"Saved as note {rid}. It will be in your context from the next turn on."


# --- long outputs -----------------------------------------------------------
def read_tool_output(output_id: int, status: str, keyword: str = "",
                     start_line: int = 0, max_lines: int = 120) -> str:
    """Read part of an earlier tool result that was cut short in your context.

    When a result is truncated, the note names its output id. Page through
    the stored result with start_line, or search it with keyword to get
    only the matching lines with their numbers.

    Args:
        output_id: The number from the truncation note.
        status: A short present-tense line shown to the user, for example
            'Reading the rest of the page'.
        keyword: Return only lines containing this text, case-insensitive.
        start_line: Line (or match) number to start from, 0-based.
        max_lines: How many lines to return, 1 to 400.

    Returns:
        The requested lines with a header saying where they sit.
    """
    try:
        _user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No chat context."
    try:
        oid = int(output_id)
        start = max(0, int(start_line or 0))
        limit = max(1, min(int(max_lines or 120), 400))
    except (TypeError, ValueError):
        return "output_id, start_line and max_lines must be numbers."

    row = get_db().execute(
        "SELECT result FROM tool_calls WHERE id = ? AND chat_id = ?", (oid, chat_id)).fetchone()
    if row is None:
        return f"There is no tool output #{oid} in this chat."
    lines = (row["result"] or "").split("\n")

    if keyword:
        k = keyword.lower()
        hits = [f"{i}: {ln}" for i, ln in enumerate(lines) if k in ln.lower()]
        if not hits:
            return f"No line of output #{oid} contains {keyword!r}."
        if start >= len(hits):
            return (f"Output #{oid} has {len(hits)} match(es) for {keyword!r}; "
                    f"start_line {start} is past the end.")
        page = hits[start:start + limit]
        head = (f"--- output #{oid}: matches {start}-{start + len(page) - 1} of "
                f"{len(hits)} for {keyword!r} ---\n")
        tail = (f"\n--- {len(hits) - start - len(page)} more matches; continue from "
                f"start_line={start + len(page)} ---") if start + len(page) < len(hits) else ""
        return head + "\n".join(page) + tail

    if start >= len(lines):
        return f"Output #{oid} has {len(lines)} lines; start_line {start} is past the end."
    page = lines[start:start + limit]
    head = f"--- output #{oid}: lines {start}-{start + len(page) - 1} of {len(lines)} ---\n"
    tail = (f"\n--- {len(lines) - start - len(page)} lines remain; continue from "
            f"start_line={start + len(page)} ---") if start + len(page) < len(lines) else ""
    return head + "\n".join(page) + tail


# --- files ------------------------------------------------------------------
def _list_dir(base: Path, link=None, limit: int = 200) -> list[str]:
    out = []
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if len(out) >= limit:
            out.append(f"... more than {limit} entries")
            break
        rel = p.relative_to(base).as_posix()
        if any(seg.startswith(".") or seg in ("__pycache__", "node_modules", ".venv", "venv")
               for seg in rel.split("/")):
            continue
        if p.is_file():
            size = p.stat().st_size
            shown = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            out.append(f"- {rel} ({shown})" + (f" -> {link(rel)}" if link else ""))
    return out


def manage_files(action: str, status: str, path: str = "") -> str:
    """List this chat's files, or share one from the sandbox with the user.

    Two places hold files. The chat's outputs are what tools produced -
    images, decks - and are already linkable. The sandbox workspace is /lab
    in lab_execute, where code you run writes its results; nothing there is
    visible to the user until it is shared.

    Args:
        action: 'list' shows both places. 'share' copies a file from the
            sandbox workspace into the outputs and returns a link for it.
        status: A short present-tense line shown to the user, for example
            'Sharing the chart'.
        path: For 'share', the file's path relative to /lab, for example
            'plots/sales.png'.

    Returns:
        The listing, or Markdown linking the shared file.
    """
    try:
        user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No chat context."
    outputs = _outputs_dir(user_id, chat_id)
    lab = _lab_workspace(user_id, chat_id)

    if action == "list":
        a = _list_dir(outputs, link=lambda rel: _output_link(chat_id, rel))
        b = _list_dir(lab)
        return ("Outputs (linkable):\n" + ("\n".join(a) if a else "- (none)")
                + "\n\nSandbox workspace (/lab):\n" + ("\n".join(b) if b else "- (empty)"))

    if action == "share":
        rel = str(path or "").strip().replace("\\", "/").lstrip("/")
        if rel.startswith("lab/"):
            rel = rel[4:]
        if not rel:
            return "Give the file's path relative to /lab."
        src = (lab.resolve() / rel).resolve()
        if not (src.is_file() and lab.resolve() in src.parents):
            return f"No file at /lab/{rel}. Call manage_files(action='list') to see the workspace."
        if src.stat().st_size > 200 * 1024 * 1024:
            return "That file is over 200 MB; share something smaller."
        import shutil
        name = _safe_filename(src.name)
        dest = outputs / name
        # Compared by content, not by size. Two different 4-byte files
        # collided as "the same file" and the second share silently
        # replaced the first, breaking a link the user already had.
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            stem, ext = os.path.splitext(name)
            name = f"{stem}_{uuid.uuid4().hex[:4]}{ext}"
            dest = outputs / name
        shutil.copyfile(src, dest)
        link = _output_link(chat_id, name)
        if dest.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            return f"![{name}]({link})\n\nShared /lab/{rel} as {name}."
        return f"[Download {name}]({link})\n\nShared /lab/{rel} as {name}."

    return "action must be 'list' or 'share'."


# --- scheduled tasks ----------------------------------------------------------
def _parse_when(run_at: str, delay_minutes: int):
    """(aware UTC datetime, note) from the model's run_at or delay_minutes."""
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        delay = int(delay_minutes or 0)
    except (TypeError, ValueError):
        delay = 0
    if delay > 0:
        return now + _dt.timedelta(minutes=delay), ""
    s = (run_at or "").strip().replace("Z", "+00:00")
    if not s:
        raise ValueError("give run_at (ISO 8601 with an offset, for example "
                         "2026-09-20T08:00:00+05:30) or delay_minutes")
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"could not read {run_at!r} as ISO 8601")
    note = ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
        note = (" No offset was given, so that was read as UTC; pass one like "
                "+05:30 to be exact.")
    return when.astimezone(_dt.timezone.utc), note


def _utc_str(when) -> str:
    return when.strftime("%Y-%m-%d %H:%M:%S")


def schedule_task(action: str, status: str, task_prompt: str = "", run_at: str = "",
                  delay_minutes: int = 0, every_minutes: int = 0, task_id: int = 0) -> str:
    """Run a task later, on its own, without the user present; or list and cancel.

    When the time comes, the task prompt is sent to you in this same chat as
    a new turn and you carry it out with your tools; the result appears in
    the chat for the user to read. Write task_prompt as complete
    instructions to your future self - it will have this chat's history but
    not your current train of thought. Call get_current_time first so the
    time is right, and confirm the scheduled time back to the user in their
    timezone.

    Args:
        action: 'schedule', 'list' or 'cancel'.
        status: A short present-tense line shown to the user, for example
            'Scheduling the daily digest'.
        task_prompt: What to do when the task fires (for 'schedule').
        run_at: When, as ISO 8601 with a timezone offset, for example
            '2026-09-20T08:00:00+05:30'.
        delay_minutes: Alternative to run_at: minutes from now.
        every_minutes: Repeat interval in minutes, 0 for once. At least 5.
        task_id: For 'cancel', the task's number.

    Returns:
        Confirmation with the task number and its next run time in UTC.
    """
    try:
        user_id, chat_id = _lab_identity()
    except RuntimeError:
        return "No chat context to schedule in."
    database = get_db()

    if action == "list":
        rows = database.execute(
            "SELECT id, task_prompt, run_at, every_minutes, status, runs FROM scheduled_tasks"
            " WHERE user_id = ? AND status IN ('pending', 'running') ORDER BY run_at",
            (user_id,)).fetchall()
        if not rows:
            return "No scheduled tasks."
        return "Scheduled tasks (times in UTC):\n" + "\n".join(
            f"- #{r['id']} at {r['run_at']}"
            + (f", every {r['every_minutes']} min" if r["every_minutes"] else ", once")
            + (f", ran {r['runs']}x" if r["runs"] else "")
            + f" [{r['status']}]: {r['task_prompt'][:90]}" for r in rows)

    if action == "cancel":
        n = database.execute(
            "UPDATE scheduled_tasks SET status = 'cancelled' WHERE id = ? AND user_id = ?"
            " AND status IN ('pending', 'running')", (int(task_id or 0), user_id)).rowcount
        database.commit()
        return f"Cancelled task #{task_id}." if n else f"There is no active task #{task_id}."

    if action != "schedule":
        return "action must be 'schedule', 'list' or 'cancel'."

    prompt = (task_prompt or "").strip()
    if not prompt:
        return "task_prompt is required: what should happen when the task fires?"
    try:
        when, note = _parse_when(run_at, delay_minutes)
    except ValueError as exc:
        return f"Cannot schedule: {exc}."
    import datetime as _dt
    if when < _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30):
        return "That time is in the past (or under a minute away). Pick a later one."
    try:
        every = int(every_minutes or 0)
    except (TypeError, ValueError):
        every = 0
    if every and every < SCHEDULE_MIN_REPEAT:
        return f"every_minutes must be at least {SCHEDULE_MIN_REPEAT}."
    active = database.execute(
        "SELECT COUNT(*) FROM scheduled_tasks WHERE user_id = ? AND status IN ('pending', 'running')",
        (user_id,)).fetchone()[0]
    if active >= SCHEDULED_TASKS_MAX:
        return f"You already have {active} tasks waiting; cancel one before adding more."

    rid = database.execute(
        "INSERT INTO scheduled_tasks (user_id, chat_id, task_prompt, run_at, every_minutes)"
        " VALUES (?, ?, ?, ?, ?)", (user_id, chat_id, prompt, _utc_str(when), every)).lastrowid
    database.commit()
    return (f"Scheduled as task #{rid} for {_utc_str(when)} UTC"
            + (f", repeating every {every} minutes" if every else "") + f".{note}")


def _scheduled_producer(r, args: dict):
    """gemini_producer for a scheduled task, plus bookkeeping when it ends."""
    task_id = args["_scheduled_task"]
    ok = False
    try:
        yield from gemini_producer(r, args)
        ok = True
    finally:
        _finish_task(task_id, ok)


def _finish_task(task_id: int, ok: bool) -> None:
    database = get_db()
    row = database.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None or row["status"] != "running":
        return                           # cancelled while it ran; leave it
    if row["every_minutes"]:
        # Re-armed whether or not this run succeeded. Writing a terminal
        # status on failure killed the schedule outright: run_due_tasks
        # only ever claims 'pending', so one transient error - a Redis
        # blip, a locked database - silently ended a daily digest for
        # good, with nothing said to anyone.
        database.execute(
            "UPDATE scheduled_tasks SET status = 'pending', lock_id = NULL, runs = runs + 1,"
            " last_run = datetime('now'), run_at = datetime('now', '+' || ? || ' minutes')"
            " WHERE id = ?", (row["every_minutes"], task_id))
        if not ok:
            logger.warning("Scheduled task %s failed; it will run again on schedule",
                           task_id)
    else:
        database.execute(
            "UPDATE scheduled_tasks SET status = ?, lock_id = NULL, runs = runs + 1,"
            " last_run = datetime('now') WHERE id = ?", ("done" if ok else "failed", task_id))
    database.commit()


def _launch_task(app, task: dict) -> str:
    """Start a claimed task as a normal streamed turn in its chat.

    It goes through the same producer as a typed message, so it gets the
    tool loop, key rotation, persistence and cancellation for free. The
    stream has no reader; the reply is simply in the chat when the user
    next opens it.
    """
    redis_url = app.config["REDIS_URL"]
    message = (f"[Scheduled task #{task['id']}] {task['task_prompt']}\n\n"
               f"(This task is running on its schedule; the user is not at the "
               f"keyboard. Carry it out now with your tools and leave the result "
               f"here. Do not ask questions.)")
    qid = register_query(redis_url, {
        "chat_id": task["chat_id"], "user_id": task["user_id"],
        "message": message, "_scheduled_task": task["id"],
    })
    claim_stream(redis_url, qid)
    run_worker(app, qid, _scheduled_producer)
    return qid


def _chat_busy(chat_id: int, redis_url: str | None = None) -> bool:
    return chat_is_generating(redis_url, chat_id)


def run_due_tasks(app) -> int:
    """Claim and start every due task. Returns how many were started.

    The claim is one UPDATE that picks the earliest due row and stamps it
    with this call's lock id, so several workers polling the same database
    cannot start the same task twice. A task whose chat is mid-generation
    is pushed back a minute rather than cancelling the user's turn.
    """
    started = 0
    with app.app_context():
        try:
            database = get_db()
            database.execute(
                "UPDATE scheduled_tasks SET status = 'pending', lock_id = NULL"
                " WHERE status = 'running' AND claimed_at < datetime('now', ?)",
                (f"-{SCHEDULE_STALE_MINUTES} minutes",))
            database.commit()
            for _ in range(20):
                lock = uuid.uuid4().hex
                database.execute(
                    "UPDATE scheduled_tasks SET status = 'running', lock_id = ?,"
                    " claimed_at = datetime('now')"
                    " WHERE id = (SELECT id FROM scheduled_tasks WHERE status = 'pending'"
                    "             AND run_at <= datetime('now') ORDER BY run_at LIMIT 1)",
                    (lock,))
                database.commit()
                task = database.execute(
                    "SELECT * FROM scheduled_tasks WHERE lock_id = ? AND status = 'running'",
                    (lock,)).fetchone()
                if task is None:
                    break
                if _chat_busy(task["chat_id"], app.config.get("REDIS_URL")):
                    database.execute(
                        "UPDATE scheduled_tasks SET status = 'pending', lock_id = NULL,"
                        " run_at = datetime('now', '+1 minute') WHERE id = ?", (task["id"],))
                    database.commit()
                    continue
                try:
                    _launch_task(app, dict(task))
                except Exception:
                    # Hand the claim straight back rather than leaving the
                    # row 'running' until the 30-minute stale reclaim, and
                    # keep going: one unlaunchable task used to abort the
                    # whole tick, so everything behind it waited too.
                    logger.exception("Could not launch scheduled task %s", task["id"])
                    database.execute(
                        "UPDATE scheduled_tasks SET status = 'pending', lock_id = NULL,"
                        " run_at = datetime('now', '+1 minute') WHERE id = ?",
                        (task["id"],))
                    database.commit()
                    continue
                started += 1
        except sqlite3.OperationalError as exc:
            # WARNING, not DEBUG. At the app's default INFO level a
            # scheduler that could not see its own table said nothing at
            # all and simply never ran anything.
            logger.warning("Scheduler skipped a tick: %s", exc)
            return 0
    return started


def start_scheduler(app) -> threading.Thread:
    """A daemon thread that runs due tasks every SCHEDULER_INTERVAL seconds."""
    def loop():
        while True:
            try:
                run_due_tasks(app)
            except Exception:
                logger.exception("Scheduler tick failed")
            time.sleep(SCHEDULER_INTERVAL)

    t = threading.Thread(target=loop, name="scheduler", daemon=True)
    t.start()
    return t


# --- Phase 9: repo_control and production app hosting ------------------------
active_apps: dict[str, dict] = {}
active_apps_lock = threading.Lock()


def _redis_repo_key(process_id: str) -> str:
    """Redis hash key for caching deployed app routing metadata."""
    return f"repo:{process_id}"


def generate_unique_subdomain(project_name: str, database=None) -> str:
    """Generate a clean, URL-friendly subdomain slug with collision avoidance."""
    slug = re.sub(r"[^a-z0-9]+", "-", (project_name or "app").lower()).strip("-")
    if not slug:
        slug = "app"
    # Reserved subdomains
    if slug in ("www", "api", "admin", "mail", "app", "status", "stellar", "test"):
        slug = f"{slug}-app"

    db = database or get_db()
    base_slug = slug
    counter = 1
    while True:
        row = db.execute("SELECT 1 FROM repo_history WHERE subdomain = ?", (slug,)).fetchone()
        if not row:
            return slug
        counter += 1
        slug = f"{base_slug}-{counter}"


def _perform_snapshot(project_dir: Path, p_id: str, db) -> int:
    """Scan the deployment workspace and save non-binary source files into SQLite."""
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
    snapshot: dict[str, str] = {}
    count = 0
    if project_dir.exists():
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for f in files:
                if f.startswith(".") or f.endswith((".pyc", ".png", ".jpg", ".jpeg", ".ico", ".tar", ".gz", ".zip", ".bin")):
                    continue
                file_path = Path(root) / f
                try:
                    # The model has a shell in this directory, so a file
                    # here may be a symlink it made. read_text() would
                    # resolve it on the host: "ln -s ../../keys.env note.txt"
                    # would snapshot every credential into the database and
                    # hand it back on the next deploy.
                    if file_path.is_symlink():
                        continue
                    if not file_path.resolve().is_relative_to(project_dir.resolve()):
                        continue
                    if file_path.stat().st_size > 500_000:
                        continue
                    rel_path = file_path.relative_to(project_dir).as_posix()
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    snapshot[rel_path] = content
                    count += 1
                except Exception:
                    pass

    row = db.execute("SELECT files_snapshot FROM repo_history WHERE process_id = ?", (p_id,)).fetchone()
    old_snap = {}
    if row and row["files_snapshot"]:
        try:
            old_snap = json.loads(row["files_snapshot"])
        except Exception:
            pass
    if "port" in old_snap:
        snapshot["port"] = old_snap["port"]
    if "repo" in old_snap:
        snapshot["repo"] = old_snap["repo"]

    db.execute(
        "UPDATE repo_history SET files_snapshot = ?, last_updated = datetime('now') WHERE process_id = ?",
        (json.dumps(snapshot), p_id),
    )
    db.commit()
    return count


def repo_control(
    action: str,
    status: str,
    timeout: int = 60,
    app_id: str = "",
    project_name: str = "",
    files: list[str] | None = None,
    repo_url: str = "",
    port: int = 5000,
    command: str = "",
    env_type: str = "web",
) -> str:
    """Control and manage repository-based or custom-stack web deployments.

    Deploy, run, manage, snapshot, and control long-running web applications
    (Node.js, React, Flask, FastAPI, Go, static HTML, etc.) running in isolated
    containers with their own public subdomains.

    Args:
        action: One of 'deploy', 'execute', 'list_history', 'rename', 'stop',
            'restart', or 'snapshot'.
        status: A short present-tense line shown to the user while this runs,
            for example 'Deploying web application' or 'Starting web server'.
        timeout: Execution timeout in seconds (default 60, up to 600).
        app_id: The Deployment ID (process_id), project name, or subdomain
            (required for 'execute', 'rename', 'stop', 'restart', 'snapshot').
        project_name: Custom name for the project (used for subdomain in 'deploy'
            and 'rename').
        files: Optional list of file paths to explicitly snapshot (for 'snapshot').
        repo_url: Git repository URL to clone on deploy (optional for 'deploy').
        port: Internal port the web app listens on inside the container (default 5000).
        command: Shell command to run inside the container (required for 'execute').
        env_type: 'web' (standard web stack) or 'mobile'.

    Returns:
        Confirmation message, live subdomain URL, deployment list, or command output.
    """
    try:
        user_id, _cid = _lab_identity()
    except RuntimeError:
        user_id = session.get("user_id") if has_request_context() else None
        if not user_id:
            return "Authentication or chat context required to manage deployments."

    action = (action or "").strip().lower()
    valid_actions = {"deploy", "execute", "list_history", "rename", "stop", "restart", "snapshot"}
    if action not in valid_actions:
        return (f"Unknown action {action!r}. Supported actions: "
                "'deploy', 'execute', 'list_history', 'rename', 'stop', 'restart', 'snapshot'.")

    try:
        timeout = max(1, min(int(timeout or 60), 600))
    except (TypeError, ValueError):
        timeout = 60

    try:
        port = int(port or 5000)
    except (TypeError, ValueError):
        port = 5000

    db = get_db()

    if action == "list_history":
        rows = db.execute(
            "SELECT project_name, process_id, subdomain, status, host_port, deployment_url, created_at "
            "FROM repo_history WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        if not rows:
            return "You have no deployed applications."
        lines = ["### Your Deployments\n"]
        for r in rows:
            status_icon = "🟢" if r["status"] == "running" else "⚪"
            url = r["deployment_url"] or ""
            url_link = f" - [Open App]({url})" if r["status"] == "running" and url else ""
            lines.append(
                f"- {status_icon} **{r['project_name']}** (ID: `{r['process_id']}`, Subdomain: `{r['subdomain']}`) "
                f"— Status: *{r['status']}*{url_link}"
            )
        return "\n".join(lines)

    if action == "rename":
        if not app_id or not project_name:
            return "Both app_id (current project) and project_name (new name) are required for rename."
        row = db.execute(
            "SELECT id, process_id, project_name FROM repo_history "
            "WHERE (process_id = ? OR subdomain = ? OR project_name = ?) AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id, app_id, app_id, user_id),
        ).fetchone()
        if not row:
            return f"Deployment {app_id!r} not found."
        p_id = row["process_id"]
        domain = current_app.config.get("STELLAR_DOMAIN") or os.environ.get("STELLAR_DOMAIN", "stellarai.site")
        new_subdomain = generate_unique_subdomain(project_name, db)
        new_url = f"https://{new_subdomain}.{domain}/"
        db.execute(
            "UPDATE repo_history SET project_name = ?, subdomain = ?, deployment_url = ?, last_updated = datetime('now') WHERE process_id = ?",
            (project_name, new_subdomain, new_url, p_id),
        )
        db.commit()
        with active_apps_lock:
            if p_id in active_apps:
                active_apps[p_id]["subdomain"] = new_subdomain
        return f"Deployment renamed to '{project_name}'! New URL: {new_url}"

    if action == "snapshot":
        if not app_id:
            return "app_id is required for snapshot."
        row = db.execute(
            "SELECT id, process_id, project_name FROM repo_history "
            "WHERE (process_id = ? OR subdomain = ? OR project_name = ?) AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id, app_id, app_id, user_id),
        ).fetchone()
        if not row:
            return f"Deployment {app_id!r} not found."
        p_id = row["process_id"]
        project_dir = PROJECT_ROOT / "deployments" / f"u{user_id}_{p_id}"
        n = _perform_snapshot(project_dir, p_id, db)
        return f"Snapshotted {n} files from '{row['project_name']}' into database."

    if action == "stop":
        if not app_id:
            return "app_id is required to stop a deployment."
        row = db.execute(
            "SELECT id, process_id, project_name, container_id, status FROM repo_history "
            "WHERE (process_id = ? OR subdomain = ? OR project_name = ?) AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id, app_id, app_id, user_id),
        ).fetchone()
        if not row:
            return f"Deployment {app_id!r} not found."
        p_id = row["process_id"]
        project_dir = PROJECT_ROOT / "deployments" / f"u{user_id}_{p_id}"
        _perform_snapshot(project_dir, p_id, db)
        try:
            client = _docker()
            c = client.containers.get(f"stellar-repo-{p_id}")
            c.stop(timeout=5)
        except Exception as exc:
            logger.warning("Could not stop container stellar-repo-%s: %s", p_id, exc)
        db.execute(
            "UPDATE repo_history SET status = 'stopped', last_updated = datetime('now') WHERE process_id = ?",
            (p_id,),
        )
        db.commit()
        with active_apps_lock:
            active_apps.pop(p_id, None)
        return f"Deployment '{row['project_name']}' (`{p_id}`) stopped. Files snapshotted to database."

    if action == "restart":
        if not app_id:
            return "app_id is required to restart a deployment."
        row = db.execute(
            "SELECT id, process_id, project_name, container_id, subdomain, status, files_snapshot FROM repo_history "
            "WHERE (process_id = ? OR subdomain = ? OR project_name = ?) AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id, app_id, app_id, user_id),
        ).fetchone()
        if not row:
            return f"Deployment {app_id!r} not found."
        p_id = row["process_id"]
        domain = current_app.config.get("STELLAR_DOMAIN") or os.environ.get("STELLAR_DOMAIN", "stellarai.site")
        subdomain = row["subdomain"]
        snapshot = {}
        if row["files_snapshot"]:
            try:
                snapshot = json.loads(row["files_snapshot"])
            except Exception:
                pass
        target_port = snapshot.get("port", 5000)
        project_dir = PROJECT_ROOT / "deployments" / f"u{user_id}_{p_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        for rel_path, content in snapshot.items():
            if rel_path in ("port", "repo") or not isinstance(content, str):
                continue
            fp = project_dir / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            if not fp.exists():
                fp.write_text(content, encoding="utf-8")

        try:
            client = _docker()
        except Exception as d_err:
            return f"Docker is not available: {d_err}"

        container_name = f"stellar-repo-{p_id}"
        try:
            c = client.containers.get(container_name)
            if c.status != "running":
                c.start()
        except Exception:
            network = _user_network(client, user_id)
            c = client.containers.run(
                LAB_IMAGE,
                name=container_name,
                command=["tail", "-f", "/dev/null"],
                ports={f"{target_port}/tcp": ("127.0.0.1", 0)},
                volumes={str(project_dir): {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                network=network,
                detach=True,
                mem_limit=LAB_MEMORY,
                nano_cpus=int(LAB_CPUS * 1_000_000_000),
                # Same cap the lab container gets. Without it a
                # fork bomb in a deployed app exhausts the host's
                # PID table and nothing on the box can fork.
                pids_limit=LAB_PIDS,
                labels={"stellar": "repo", "user": str(user_id), "process_id": p_id, "subdomain": subdomain},
            )
        c.reload()
        host_port = int(c.attrs["NetworkSettings"]["Ports"][f"{target_port}/tcp"][0]["HostPort"])
        db.execute(
            "UPDATE repo_history SET status = 'running', host_port = ?, container_id = ?, last_updated = datetime('now') WHERE process_id = ?",
            (host_port, c.id, p_id),
        )
        db.commit()
        with active_apps_lock:
            active_apps[p_id] = {"container_id": c.id, "port": host_port, "status": "running", "subdomain": subdomain}
        public_url = f"https://{subdomain}.{domain}/"
        return f"Deployment '{row['project_name']}' restarted and running! Live URL: {public_url} (Port {target_port} -> host port {host_port})."

    if action == "execute":
        if not app_id or not command:
            return "Both app_id and command are required for execute."
        row = db.execute(
            "SELECT id, process_id, project_name, status, files_snapshot FROM repo_history "
            "WHERE (process_id = ? OR subdomain = ? OR project_name = ?) AND user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id, app_id, app_id, user_id),
        ).fetchone()
        if not row:
            return f"Deployment {app_id!r} not found."
        p_id = row["process_id"]
        snapshot = {}
        if row["files_snapshot"]:
            try:
                snapshot = json.loads(row["files_snapshot"])
            except Exception:
                pass
        target_port = snapshot.get("port", 5000)

        try:
            client = _docker()
        except Exception as d_err:
            return f"Docker is not available: {d_err}"

        container_name = f"stellar-repo-{p_id}"
        try:
            container = client.containers.get(container_name)
            if container.status != "running":
                container.start()
        except Exception as exc:
            return f"Container {container_name} is not available: {exc}. Try repo_control(action='restart', app_id='{p_id}')."

        try:
            exec_res = container.exec_run(
                cmd=["timeout", "--signal=KILL", str(timeout), "bash", "-lc", command],
                workdir="/app",
                demux=False,
            )
            output = (exec_res.output or b"").decode("utf-8", errors="replace")
        except Exception as exc:
            return f"Execution error in {container_name}: {exc}"

        project_dir = PROJECT_ROOT / "deployments" / f"u{user_id}_{p_id}"
        _perform_snapshot(project_dir, p_id, db)

        start_keywords = ["npm start", "python", "node", "serve", "go run", "npm run dev", "uvicorn", "gunicorn", "flask run"]
        if any(kw in command.lower() for kw in start_keywords):
            time.sleep(2)
            container.reload()
            if container.status != "running":
                return f"Command executed, but container stopped. Output:\n{output}"
            try:
                check_res = container.exec_run(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{target_port}/")
                status_code = check_res.output.decode("utf-8", errors="replace").strip()
                if status_code.isdigit():
                    code = int(status_code)
                    if 200 <= code < 500:
                        output += f"\n\nServer is READY (HTTP {code}) and listening on 0.0.0.0:{target_port}!"
                    elif code >= 500:
                        output += f"\n\nServer responded with HTTP ERROR {code} on port {target_port}."
                    else:
                        output += f"\n\nServer returned HTTP {code} on port {target_port}."
            except Exception:
                pass

        if exec_res.exit_code != 0:
            return f"Command exited with code {exec_res.exit_code}.\nOutput:\n{output}"
        return output or "Command executed successfully (no output)."

    if action == "deploy":
        project_title = (project_name or "").strip() or (
            repo_url.split("/")[-1].replace(".git", "") if repo_url else "Custom Web App"
        )
        domain = current_app.config.get("STELLAR_DOMAIN") or os.environ.get("STELLAR_DOMAIN", "stellarai.site")

        existing_snapshot = None
        lookup = app_id or project_name
        if lookup:
            old_row = db.execute(
                "SELECT files_snapshot FROM repo_history "
                "WHERE (project_name = ? OR process_id = ? OR subdomain = ?) AND user_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (lookup, lookup, lookup, user_id),
            ).fetchone()
            if old_row and old_row["files_snapshot"]:
                try:
                    existing_snapshot = json.loads(old_row["files_snapshot"])
                except Exception:
                    pass

        process_id = uuid.uuid4().hex[:12]
        subdomain = generate_unique_subdomain(project_title, db)
        initial_files = existing_snapshot or {"port": port}
        if repo_url:
            initial_files["repo"] = repo_url
        if port:
            initial_files["port"] = port

        public_url = f"https://{subdomain}.{domain}/"

        project_dir = PROJECT_ROOT / "deployments" / f"u{user_id}_{process_id}"
        project_dir.mkdir(parents=True, exist_ok=True)

        if existing_snapshot:
            for fname, fcontent in existing_snapshot.items():
                if fname in ("repo", "port") or not isinstance(fcontent, str):
                    continue
                fpath = project_dir / fname
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(fcontent, encoding="utf-8")

        db.execute(
            "INSERT INTO repo_history (user_id, project_name, process_id, status, files_snapshot, subdomain, host_port, deployment_url) "
            "VALUES (?, ?, ?, 'deploying', ?, ?, 0, ?)",
            (user_id, project_title, process_id, json.dumps(initial_files), subdomain, public_url),
        )
        db.commit()

        try:
            client = _docker()
            network = _user_network(client, user_id)
            container_name = f"stellar-repo-{process_id}"

            try:
                old = client.containers.get(container_name)
                old.remove(force=True)
            except Exception:
                pass

            container = client.containers.run(
                LAB_IMAGE,
                name=container_name,
                command=["tail", "-f", "/dev/null"],
                ports={f"{port}/tcp": ("127.0.0.1", 0)},
                volumes={str(project_dir): {"bind": "/app", "mode": "rw"}},
                working_dir="/app",
                network=network,
                detach=True,
                mem_limit=LAB_MEMORY,
                nano_cpus=int(LAB_CPUS * 1_000_000_000),
                # Same cap the lab container gets. Without it a
                # fork bomb in a deployed app exhausts the host's
                # PID table and nothing on the box can fork.
                pids_limit=LAB_PIDS,
                labels={
                    "stellar": "repo",
                    "user": str(user_id),
                    "process_id": process_id,
                    "subdomain": subdomain,
                },
            )
            container.reload()
            host_port = int(container.attrs["NetworkSettings"]["Ports"][f"{port}/tcp"][0]["HostPort"])

            db.execute(
                "UPDATE repo_history SET status = 'running', host_port = ?, container_id = ? WHERE process_id = ?",
                (host_port, container.id, process_id),
            )
            db.commit()

            with active_apps_lock:
                active_apps[process_id] = {
                    "container_id": container.id,
                    "port": host_port,
                    "status": "running",
                    "subdomain": subdomain,
                }

            try:
                redis_url = current_app.config.get("REDIS_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                rclient = _redis_client(redis_url)
                rclient.hset(
                    _redis_repo_key(process_id),
                    mapping={
                        "container_id": container.id,
                        "status": "running",
                        "process_id": process_id,
                        "host_port": str(host_port),
                        "subdomain": subdomain,
                    },
                )
            except Exception as redis_err:
                logger.warning("Could not cache repo info in Redis: %s", redis_err)

            if repo_url and not any(project_dir.iterdir()):
                container.exec_run(f"git clone {repo_url} .", workdir="/app")

            restored_note = f" (restored {len(existing_snapshot)} files from snapshot)" if existing_snapshot else ""
            return (
                f"Container provisioned for '{project_title}'{restored_note}!\n"
                f"- **Process ID**: `{process_id}`\n"
                f"- **Subdomain**: `{subdomain}`\n"
                f"- **Live URL**: {public_url}\n"
                f"- **Internal Port**: {port} (mapped to host {host_port})\n\n"
                f"Use `repo_control(action='execute', app_id='{process_id}', command='...')` to write files, "
                f"install dependencies, and launch your server.\n"
                f"**Important**: Make sure your application binds to `0.0.0.0:{port}`."
            )
        except Exception as exc:
            logger.exception("Failed to provision repo container: %s", exc)
            db.execute("UPDATE repo_history SET status = 'failed' WHERE process_id = ?", (process_id,))
            db.commit()
            return f"Error provisioning deployment container: {exc}"

    return "No action taken."


def handle_subdomain_proxy(app):
    """Intercept requests with a subdomain and reverse proxy to the target container."""
    import requests

    if request.path.startswith("/api/sentinel/"):
        return None

    host = request.headers.get("Host", "").split(":")[0].lower()
    if not host or host in ("localhost", "127.0.0.1", "testserver"):
        return None

    stellar_domain = (app.config.get("STELLAR_DOMAIN") or os.environ.get("STELLAR_DOMAIN", "stellarai.site")).lower()

    subdomain = None
    if host.endswith("." + stellar_domain):
        subdomain = host[:-len(stellar_domain) - 1]
    elif host.endswith(".localhost"):
        subdomain = host[:-len(".localhost")]
    elif host.endswith(".testserver"):
        subdomain = host[:-len(".testserver")]

    if not subdomain or subdomain in ("www", "api", "admin", "mail", "app", "status", "stellar"):
        return None

    db = get_db()
    cursor = db.execute(
        "SELECT r.id, r.user_id, r.project_name, r.process_id, r.status, r.host_port, "
        "u.is_approved FROM repo_history r JOIN users u ON r.user_id = u.id "
        "WHERE r.subdomain = ? OR r.process_id = ? ORDER BY r.id DESC LIMIT 1",
        (subdomain, subdomain),
    )
    row = cursor.fetchone()
    if not row:
        return f"Application '{subdomain}' not found. Verify the URL or deploy it with repo_control.", 404

    if not row["is_approved"]:
        return f"Access Denied. The owner of '{subdomain}' is not approved.", 403

    if row["status"] != "running" or not row["host_port"]:
        return f"Application '{subdomain}' is stopped or unavailable. Start it in Repo Control.", 503

    target_port = row["host_port"]
    path = request.full_path
    target_url = f"http://127.0.0.1:{target_port}{path}"

    try:
        proxy_cookies = {k: v for k, v in request.cookies.items() if k not in ("session", "stellar_session_main")}
        proxy_headers = {k: v for k, v in request.headers if k.lower() not in ("host", "cookie")}
        proxy_headers["X-Forwarded-For"] = request.remote_addr or "127.0.0.1"
        proxy_headers["X-Forwarded-Proto"] = request.scheme

        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=proxy_headers,
            data=request.get_data(),
            cookies=proxy_cookies,
            allow_redirects=False,
            stream=True,
            timeout=3600,
        )

        excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        headers = [(k, v) for (k, v) in resp.raw.headers.items() if k.lower() not in excluded_headers]
        headers.append(("Cache-Control", "no-cache, no-store, must-revalidate"))

        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return Response(stream_with_context(generate()), status=resp.status_code, headers=headers)
    except requests.exceptions.RequestException as exc:
        logger.error("Proxy error for subdomain %s (port %s): %s", subdomain, target_port, exc)
        return f"Application '{subdomain}' is currently unreachable on port {target_port}: {exc}", 502


# What the model is told about these tools beyond their docstrings: the
# conventions that span several of them.
TOOL_GUIDE = """

### FILES, IMAGES AND OUTPUTS

- generate_image returns Markdown for the picture. Put it in your reply
  exactly as returned and it displays inline.
- Files that tools produce live in this chat's files and are linked as
  /api/outputs/<chat>/<name>. Use the links the tools give you; never
  invent one.
- Code run with lab_execute writes to /lab, which the user cannot see.
  manage_files(action='share') turns a file there into a download link,
  and 'list' shows what exists in both places.
- A tool result longer than 12,000 characters is cut in your context; the
  note at the cut names an output id, and read_tool_output pages through
  or searches the rest. Use it rather than fetching the same page again.

### DEPLOYING WEB APPLICATIONS (repo_control)

- When the user asks to build, run, host, or deploy a website, web application,
  dashboard, or API, use repo_control. Do not just run it inside /lab.
- Use action='deploy' to provision an isolated container with its own live public
  subdomain.
- Use action='execute' to install dependencies (pip, npm), write files, and start
  the server on 0.0.0.0 and the specified port.
- Code changes and project files are automatically snapshotted into the database,
  so apps can be stopped and restarted cleanly without losing files.
- Use action='list_history' to see all active and past deployments.

### MEMORY AND TIME

- remember saves a durable fact about the user; it appears in every future
  conversation under "What you remember about this user". Save preferences
  and corrections without being asked, and never save secrets.
- schedule_task runs an instruction later or on a repeat, in this chat,
  with nobody present. Call get_current_time first so the time is right,
  and confirm the time back in the user's timezone.
- analyze_youtube_video watches a video itself: summaries with timestamps,
  what is claimed, a specific moment. It also searches YouTube.
"""


# The registry handed to the model. Adding a tool means writing the function
# and adding it here - there is no schema to maintain separately.
AVAILABLE_TOOLS = [get_current_time, fetch_url, web_search, lab_execute,
                   compress_memory, request_user_interaction, chess_move,
                   chess_play, generate_image, make_presentation,
                   analyze_youtube_video, send_self_email, remember,
                   read_tool_output, manage_files, schedule_task, repo_control]
TOOLS_BY_NAME = {fn.__name__: fn for fn in AVAILABLE_TOOLS}



def _execute_tool(name: str, arguments: dict) -> tuple[str, bool]:
    """Run one tool call. Returns (result_text, is_error).

    Never raises. A tool that blows up must come back to the model as text
    it can read and react to - an exception here would kill the whole turn
    over one bad argument, when the model could have simply tried again.
    """
    # The model is only offered tools from AVAILABLE_TOOLS, but it can still
    # emit a name that is not in it. Dispatching through the registry rather
    # than getattr() means a hallucinated name cannot reach anything else in
    # this module.
    fn = TOOLS_BY_NAME.get(name)
    if fn is None:
        return f"No such tool: {name!r}. Available: {', '.join(TOOLS_BY_NAME)}", True

    try:
        result = fn(**arguments)
        return str(result), False
    except TypeError as exc:
        # Wrong or missing arguments - the model can correct this itself.
        return f"Invalid arguments for {name}: {exc}", True
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return f"{name} failed: {type(exc).__name__}: {exc}", True


# ---------------------------------------------------------------------
# Phase 3: Gemini LLM Engine
# ---------------------------------------------------------------------
def collect_keys(*names: str) -> list[str]:
    """Gather a credential pool from keys.env, in priority order.

    Accepts exact names and numbered families, so all of these are found and
    treated as one pool:

        PRIMARY_API_KEY
        BACKUP_API_KEY_1, BACKUP_API_KEY_2, ...
        TAVILY_API_KEY, TAVILY_API_KEY_1, ...

    Reading a family rather than one fixed variable means adding a key is
    just adding a line - no code change, and no silent failure when the
    variable happens to be spelled with a suffix.

    Duplicates are dropped while preserving order: the same key listed twice
    is one credential sharing one quota, and keeping both would make the
    pool look larger than it is.
    """
    found: list[str] = []
    for name in names:
        exact = (os.environ.get(name) or "").strip()
        if exact:
            found.append(exact)
        # Numbered siblings. 1-based, stopping at the first gap so a typo'd
        # _7 does not silently extend the pool past a missing _6.
        i = 1
        while True:
            val = (os.environ.get(f"{name}_{i}") or "").strip()
            if not val:
                break
            found.append(val)
            i += 1

    return list(dict.fromkeys(found))


def gemini_keys() -> list[str]:
    """Every Gemini key available, primary first."""
    return collect_keys("PRIMARY_API_KEY", "BACKUP_API_KEY")


def tavily_keys() -> list[str]:
    """Every Tavily key available."""
    return collect_keys("TAVILY_API_KEY")


def get_gemini_client() -> genai.Client:
    """Return an authenticated Google GenAI client.

    Phase 4 uses the first key in the pool. Phase 7 replaces this with real
    rotation: per-(key, model) blocks in Redis, so an exhausted key is
    skipped rather than retried.
    """
    keys = gemini_keys()
    if not keys:
        raise RuntimeError(
            "No Gemini API key found. Set PRIMARY_API_KEY or BACKUP_API_KEY_1 "
            "in keys.env - get one free at aistudio.google.com/apikey"
        )
    return genai.Client(api_key=keys[0])


def build_gemini_history(database: sqlite3.Connection, chat_id: int, before_msg_id: int | None = None) -> list[types.Content]:
    """Retrieve previous conversation messages and map them into Gemini Content objects."""
    # Hidden rows ARE included here, and that is the whole point of the
    # column: hidden means "not in the transcript the user reads", not "not
    # in the model's memory". Two things depend on it.
    #
    # An interrupted reply is stored hidden so the model knows what it
    # already said and does not repeat it. And compress_memory hides old
    # messages while storing a state document as a hidden message - filter
    # those out and compression deletes the context *and* the summary meant
    # to replace it, which is worse than not compressing at all.
    #
    # get_messages() applies hidden = 0 separately. That is the UI's view.
    query = (
        "SELECT id, message_type, message_content FROM messages"
        " WHERE chat_id = ?"
    )
    params: list[object] = [chat_id]
    if before_msg_id is not None:
        query += " AND id < ?"
        params.append(before_msg_id)
    query += " ORDER BY timestamp ASC, id ASC"

    rows = database.execute(query, tuple(params)).fetchall()

    contents: list[types.Content] = []
    for row in rows:
        text = (row["message_content"] or "").strip()
        if not text:
            continue
        role = "user" if row["message_type"] == "user" else "model"

        # Gemini API requires alternation; merge consecutive messages with the same role
        if contents and contents[-1].role == role:
            contents[-1].parts.append(types.Part.from_text(text="\n\n" + text))
        else:
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=text)],
                )
            )

    # Gemini history must start with a user turn
    while contents and contents[0].role != "user":
        contents.pop(0)

    return contents


def _classify_error(exc: Exception) -> str:
    """Bucket an SDK exception into a recovery strategy.

    The SDK raises a wide variety of exception types, and the useful signal
    is almost always in the message text rather than the class. Matching on
    text is inelegant but it is what actually works across transports.
    """
    s = str(exc).lower()

    # An overloaded model must be checked BEFORE the transient list,
    # because Google's overload response also says "503" and
    # "unavailable". Classing it transient retried the same overloaded
    # model twice and then gave up, so block_model and the fallback-model
    # switch were unreachable from the main loop.
    # Google phrases a capacity refusal several ways and they all arrive as
    # a 503 that also says "unavailable", so matching only one word meant
    # the rest were retried against the same busy model and then given up
    # on. Observed in the wild: "The model is overloaded" and "This model
    # is currently experiencing high demand. Spikes in demand are usually
    # temporary."
    if any(x in s for x in ("overload", "high demand", "spikes in demand",
                            "at capacity", "model is busy")):
        return "quota"

    # Retrying the same model fixes these.
    if any(x in s for x in (
        "server disconnected", "connection", "timeout", "deadline",
        "503", "500", "unavailable", "internal error", "temporarily",
    )):
        return "transient"

    # A different model might work.
    if any(x in s for x in ("404", "not_found", "not found", "is not supported")):
        return "missing_model"

    # Nothing here will help; phase 7 adds key rotation for these.
    if any(x in s for x in ("429", "resource_exhausted", "quota", "rate limit")):
        return "quota"

    return "fatal"


class _Cancelled(Exception):
    """Raised inside the stream loop when the user pressed stop."""


def _drain_injections(redis_url: str, chat_id: int) -> list[dict]:
    """Take every follow-up queued for this chat, oldest first."""
    out = []
    try:
        r = _redis_client(redis_url)
        while True:
            raw = r.lpop(f"inject:{chat_id}")
            if not raw:
                break
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Belt and braces with the TTL above: an item queued for a turn
            # that has long since ended is dropped rather than delivered to
            # whatever turn happens to be running now.
            # A missing timestamp means "no idea", not "infinitely old", so
            # it is delivered rather than dropped; the TTL still bounds it.
            try:
                age = time.time() - float(item["at"])
            except (KeyError, TypeError, ValueError):
                age = 0.0
            if age > INJECT_TTL:
                logger.info("Dropping a follow-up queued %.0fs ago in chat %s",
                            age, chat_id)
                continue
            out.append(item)
    except Exception as exc:
        logger.error("Could not drain injections: %s", exc)
    return out


def _save_reply(database: sqlite3.Connection, chat_id: int, text: str,
                hidden: bool = False) -> int:
    """Commit a model reply and return its row id."""
    reply_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content, hidden)"
        " VALUES (?, 'stellar', ?, ?)",
        (chat_id, text, 1 if hidden else 0),
    ).lastrowid
    database.commit()
    return reply_id


def _record_tool_call(database, chat_id, name, arguments, result, ms, is_error) -> int:
    """Persist one tool invocation and return its row id."""
    row_id = database.execute(
        "INSERT INTO tool_calls"
        " (chat_id, tool_name, arguments, result, duration_ms, is_error)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, name, json.dumps(arguments, default=str), result, ms,
         1 if is_error else 0),
    ).lastrowid
    database.commit()
    return row_id


def _iter_parts(chunk):
    """Yield the content parts of a streamed chunk, tolerating empty ones.

    Chunks arrive with no candidates, or a candidate with no content, more
    often than the type hints suggest - usually the final chunk carrying only
    usage metadata. Indexing blindly raises mid-stream.
    """
    for cand in (chunk.candidates or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            yield part


def gemini_producer(r: redis.Redis, args: dict):
    """Run one turn, guaranteeing the chat's claim is always released.

    The claim and its release belong to the same function. They were split
    across the producer and its caller, which meant any path that did not go
    through run_worker leaked the claim - and a leaked claim makes the next
    generation in that chat cancel a thread that no longer exists, while the
    chat looks permanently busy to anything that checks.

    A finally in a generator runs when it is exhausted, closed or garbage
    collected, so this holds for a normal end, an error, and a consumer that
    simply stops reading.
    """
    chat_id = args["chat_id"]
    query_id = args.get("_query_id") or ""
    try:
        yield from _generate_turn(r, args)
    finally:
        release_generation(chat_id, query_id, args.get("_redis_url"))


def _generate_turn(r: redis.Redis, args: dict):
    """The manual tool-calling loop.

    The SDK will run this loop itself if asked. It is driven by hand because
    the loop has to do four things the automatic version does not expose:
    stream the model's own status line before each tool runs, persist every
    call, bound the number of iterations, and keep partial output when a
    turn dies halfway.

    Recovery rules, in priority order:
      1. Output already emitted commits the turn to its model. Retrying
         would replay text the client has appended, rendering it twice.
      2. Transient failures before any output retry on the same model.
      3. A missing model falls through to FALLBACK_MODEL.
      4. Anything else ends the turn, keeping whatever was produced.
    """
    chat_id = args["chat_id"]
    message = args["message"]
    database = get_db()

    # Tools receive only the arguments the model chose, so anything about
    # WHO is asking has to travel out of band. g is request-scoped and this
    # worker has its own app context, so there is no bleed between turns.
    g.lab_user_id = args.get("user_id")
    g.lab_chat_id = chat_id

    query_id = args.get("_query_id") or ""
    redis_url = args.get("_redis_url") or current_app.config["REDIS_URL"]

    # Claim this chat. Registering also cancels any generation this one
    # supersedes, so two replies can never interleave into one transcript.
    cancel_event = register_generation(chat_id, query_id, redis_url)

    # request_user_interaction has to put a widget on screen BEFORE it waits,
    # which means writing to the stream from inside a tool. Tools are plain
    # functions and cannot yield into this generator, so they are handed the
    # same append-to-Redis primitive the worker uses. consume_stream reads
    # that list by index, so anything appended simply appears.
    g.stream_redis_url = redis_url
    g.stream_emit = (lambda event: emit(r, query_id, event)) if r is not None         else (lambda event: None)

    g.stream_cancelled = lambda: cancelled()

    def cancelled() -> bool:
        """True once this turn should stop.

        Two sources, deliberately. The Event is instant and in-process; the
        Redis flag catches a stop published before this worker started
        listening, or by a process that had already moved on.
        """
        return cancel_event.is_set() or (
            bool(query_id) and is_stopped(redis_url, query_id))

    user_msg_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'user', ?)",
        (chat_id, message),
    ).lastrowid
    new_title = _touch_chat(database, chat_id, message)
    database.commit()

    yield {"type": "user_message", "id": user_msg_id}
    if new_title:
        yield {"type": "chat_title", "chat_id": chat_id, "name": new_title}
    yield {"type": "status", "text": "Thinking\u2026"}

    history = build_gemini_history(database, chat_id, before_msg_id=user_msg_id)

    # Tell the model how full its context is, so it can decide to compress.
    # It is given the numbers rather than compressed for it: the model is
    # the only party that knows which parts of the conversation still
    # matter to the task.
    system_instruction = SYSTEM_INSTRUCTION
    if any(t.__name__ == "request_user_interaction" for t in AVAILABLE_TOOLS):
        system_instruction += GENERATIVE_UI_GUIDE
    system_instruction += TOOL_GUIDE
    # What the model saved about this user in earlier chats. Prepended
    # every turn rather than retrieved on demand: a preference the model
    # has to think to look up is one it will forget to look up.
    system_instruction += memory_prompt(database, args.get("user_id"))

    est_tokens, ratio = estimate_context_usage(database, chat_id)
    if ratio >= CONTEXT_WARN_RATIO:
        system_instruction += (
            f"\n\n### CONTEXT NOTICE\n"
            f"This conversation is using roughly {ratio * 100:.0f}% of your "
            f"context window ({est_tokens:,} tokens). Before continuing, call "
            f"compress_memory with a thorough state_document. Archive "
            f"'tool_logs' first; tool output is usually the bulk of it."
        )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        # Passing the functions themselves: google-genai builds the schema
        # from each signature and docstring.
        tools=AVAILABLE_TOOLS,
        # The whole point. With AFC enabled the SDK runs tools internally and
        # returns only the final text - no status lines, no persistence, no
        # iteration cap, and no way to stream anything while a tool runs.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    keys = gemini_keys()
    if not keys:
        yield {"type": "error",
               "message": "No Gemini API key configured. Add PRIMARY_API_KEY "
                          "or BACKUP_API_KEY_1 to keys.env."}
        return

    model = DEFAULT_MODEL
    key_idx = KEY_MANAGER.first_available(keys, model)
    if key_idx is None:
        # Every key is blocked on the preferred model. The fallback meters
        # separately, so it is worth trying before giving up.
        model = FALLBACK_MODEL
        key_idx = KEY_MANAGER.first_available(keys, model)
    if key_idx is None:
        yield {"type": "error",
               "message": "All API keys are rate limited right now. "
                          "Daily quota resets at midnight US Pacific."}
        return

    client = genai.Client(api_key=keys[key_idx])
    chat_session = client.chats.create(model=model, history=history, config=config)

    def rebuild(new_model: str, new_key_idx: int):
        """Rebuild the chat on a different key or model, keeping history.

        A genai.Client is bound to its API key, so rotating means building a
        new client AND a new chat. The history has to be carried across by
        hand - the session's own accumulated history where available, so
        tool exchanges earlier in this turn survive the switch. Miss this
        and the model silently forgets the conversation mid-answer.
        """
        try:
            prior = chat_session.get_history()
        except Exception:
            prior = history
        c = genai.Client(api_key=keys[new_key_idx])
        return c, c.chats.create(model=new_model, history=prior, config=config)

    reply_parts: list[str] = []      # text across every iteration of this turn
    tool_row_ids: list[int] = []     # rows to attach to the reply once it exists
    next_message = message
    last_error: Exception | None = None
    hit_limit = True                 # cleared by the normal exit below

    for iteration in range(MAX_TOOL_ITERATIONS):
        calls: list = []
        emitted_this_call = False
        succeeded = False

        # Separate budgets, because these failures are unrelated. A network
        # blip should not consume a key rotation, and rotating through four
        # exhausted keys should not exhaust the transient-retry allowance.
        transient_left = MAX_LLM_ATTEMPTS
        rotations_left = len(keys) + 1

        # --- one model call, with key rotation then model fallback -------
        while True:
            if cancelled():
                break

            try:
                # Count before sending, not after. A request that fails still
                # consumed quota, and counting on success would let a run of
                # errors walk straight past the limit.
                KEY_MANAGER.record_request(keys[key_idx], model)

                for chunk in chat_session.send_message_stream(next_message):
                    for part in _iter_parts(chunk):
                        # Thinking blocks are internal reasoning; surfacing
                        # them would leak scratch work into the transcript.
                        if getattr(part, "thought", False):
                            continue
                        text = getattr(part, "text", None)
                        if text:
                            if cancelled():
                                # Stop mid-stream rather than draining the
                                # rest of the response first. The user asked
                                # for it to stop, not to finish quietly.
                                raise _Cancelled()
                            emitted_this_call = True
                            reply_parts.append(text)
                            yield {"type": "token", "text": text}
                        fc = getattr(part, "function_call", None)
                        if fc:
                            calls.append(fc)
                succeeded = True
                break

            except _Cancelled:
                # Caught here rather than allowed to propagate: breaking the
                # retry loop drops through to `if not succeeded: break`, out
                # of the iteration loop, and into the cancellation handling
                # at the end. Letting it escape the producer would surface as
                # a generic error event instead.
                break

            except Exception as exc:
                last_error = exc
                kind = _classify_error(exc)

                if emitted_this_call:
                    break        # rule 1: cannot replay what was sent

                # -- quota: rotate the key first, only then the model -----
                # A fresh key on the preferred model beats a stale key on a
                # worse one, so keys are exhausted before models are.
                if kind == "quota" and rotations_left > 0:
                    rotations_left -= 1
                    seconds, reason = parse_quota_block(str(exc))

                    if reason == "OVERLOAD":
                        # Google is busy, not this key. Rotating keys against
                        # an overloaded model burns the entire pool on the
                        # same failure, so mark the model instead.
                        KEY_MANAGER.block_model(model, seconds)
                        if model != FALLBACK_MODEL and not KEY_MANAGER.is_model_blocked(FALLBACK_MODEL):
                            yield {"type": "status", "text": "Switching model\u2026"}
                            model = FALLBACK_MODEL
                            client, chat_session = rebuild(model, key_idx)
                            continue
                        break

                    KEY_MANAGER.block(keys[key_idx], model, seconds, reason)

                    nxt = KEY_MANAGER.first_available(keys, model)
                    if nxt is not None:
                        logger.info("Rotating key %d -> %d on %s",
                                    key_idx, nxt, model)
                        yield {"type": "status",
                               "text": "Switching API key\u2026"}
                        key_idx = nxt
                        client, chat_session = rebuild(model, key_idx)
                        continue

                    # No key is usable on this model. Try the other model,
                    # which meters separately.
                    if model != FALLBACK_MODEL:
                        alt = KEY_MANAGER.first_available(keys, FALLBACK_MODEL)
                        if alt is not None:
                            logger.warning("All keys blocked on %s, moving to %s",
                                           model, FALLBACK_MODEL)
                            yield {"type": "status",
                                   "text": "Switching model\u2026"}
                            model = FALLBACK_MODEL
                            key_idx = alt
                            client, chat_session = rebuild(model, key_idx)
                            continue

                    logger.error("Every key is blocked on every model.")
                    break

                if kind == "transient" and transient_left > 1:
                    transient_left -= 1
                    delay = LLM_RETRY_BACKOFF ** (MAX_LLM_ATTEMPTS - transient_left - 1)
                    logger.warning("Model %s transient failure: %s", model, exc)
                    yield {"type": "status",
                           "text": "Connection issue, retrying\u2026"}
                    time.sleep(delay)
                    client, chat_session = rebuild(model, key_idx)
                    continue

                if kind == "missing_model" and model != FALLBACK_MODEL:
                    logger.warning("Model %s unavailable, falling back to %s",
                                   model, FALLBACK_MODEL)
                    model = FALLBACK_MODEL
                    client, chat_session = rebuild(model, key_idx)
                    continue

                # 429 bodies are several hundred characters of JSON; logging
                # them whole buries everything else in the file.
                logger.error("Model %s failed (%s): %s", model, kind,
                             str(exc)[:200])
                break

        if not succeeded:
            break

        if not calls:
            hit_limit = False

            # The natural seam in the turn: the model has stopped asking for
            # tools and is about to finish. If the user typed while it was
            # working, this is where that arrives.
            injected = _drain_injections(redis_url, chat_id)
            if injected and not cancelled():
                # Commit what was said so far as hidden: out of the
                # transcript, still in the model's context, so it knows what
                # it already told the user.
                partial = "".join(reply_parts).strip()
                if partial:
                    pid = _save_reply(
                        database, chat_id,
                        partial + "\n\n*[interrupted by follow-up]*",
                        hidden=True)
                    # Nudge it a second earlier so it sorts before the
                    # follow-up the user has already seen appear.
                    first_id = injected[0].get("message_id")
                    if first_id:
                        database.execute(
                            "UPDATE messages SET timestamp ="
                            " datetime((SELECT timestamp FROM messages WHERE id = ?),"
                            " '-1 second') WHERE id = ?", (first_id, pid))
                        database.commit()

                reply_parts.clear()
                if tool_row_ids:
                    database.executemany(
                        "UPDATE tool_calls SET message_id = ? WHERE id = ?",
                        [(pid if partial else None, rid) for rid in tool_row_ids])
                    database.commit()
                    tool_row_ids.clear()

                # Tell the browser to close the current bubble and start a
                # new one, so the follow-up answer is not appended to the
                # answer it replaced.
                yield {"type": "stream_reset"}

                follow = "\n".join(
                    f"[LIVE FOLLOW-UP] {m['message']}" for m in injected)
                next_message = (
                    "[SYSTEM] The user sent this while you were replying. "
                    "Your previous output has already been shown to them. "
                    "Address this now:\n" + follow)
                yield {"type": "status", "text": "Follow-up received\u2026"}
                hit_limit = True      # the turn is continuing, not ending
                continue

            break

        # --- execute the tools the model asked for ----------------------
        responses = []
        for fc in calls:
            if cancelled():
                break
            name = fc.name
            tool_args = dict(fc.args) if fc.args else {}

            # status is the model's own narration of what it is about to do.
            yield {
                "type": "tool_start",
                "name": name,
                "status": tool_args.get("status") or ("Running " + name),
            }

            t0 = time.time()
            result, is_error = _execute_tool(name, tool_args)
            ms = int((time.time() - t0) * 1000)

            row_id = _record_tool_call(
                database, chat_id, name, tool_args, result, ms, is_error)
            tool_row_ids.append(row_id)

            yield {
                "type": "tool_end",
                "id": row_id,
                "name": name,
                "ms": ms,
                "is_error": is_error,
                "preview": result[:300],
            }

            responses.append(types.Part.from_function_response(
                name=name, response={"result": _model_view(result, row_id)}))

        # Feeding the results back IS the next request. This is the loop.
        next_message = responses

    else:
        # The for-else fires only when the range was exhausted without break.
        logger.warning("Chat %s hit the %d iteration tool limit",
                       chat_id, MAX_TOOL_ITERATIONS)

    if cancelled():
        # Deliberately different from the reference, which discards
        # everything on a stop. Keeping the partial matches what the user
        # actually saw, and a reply that vanishes on refresh is worse than
        # one marked incomplete.
        partial = "".join(reply_parts).strip()
        if partial:
            rid = _save_reply(database, chat_id,
                              partial + "\n\n*[stopped]*")
            if tool_row_ids:
                database.executemany(
                    "UPDATE tool_calls SET message_id = ? WHERE id = ?",
                    [(rid, t) for t in tool_row_ids])
                database.commit()
            yield {"type": "message", "id": rid}
        yield {"type": "cancelled"}
        return

    reply = "".join(reply_parts).strip()

    if not reply and last_error is not None:
        friendly = ("The model is rate limited. Try again shortly."
                    if _classify_error(last_error) == "quota"
                    else str(type(last_error).__name__) + ": " + str(last_error))
        yield {"type": "error", "message": friendly}
        return

    if not reply:
        reply = ("I stopped after using tools without producing an answer."
                 if hit_limit else "(Empty response from model)")
    elif last_error is not None:
        reply += "\n\n*[Response interrupted: connection lost]*"

    reply_id = _save_reply(database, chat_id, reply)

    # Attach this turn's tool calls to the reply now that it has an id, so a
    # reloaded transcript can place them under the right message.
    if tool_row_ids:
        database.executemany(
            "UPDATE tool_calls SET message_id = ? WHERE id = ?",
            [(reply_id, rid) for rid in tool_row_ids],
        )
        database.commit()

    yield {"type": "message", "id": reply_id}


# ---------------------------------------------------------------------
# Chat API Routes
# ---------------------------------------------------------------------
chat_bp = Blueprint("chat", __name__, url_prefix="/api")


def _owned_chat(chat_id: int):
    """Fetch a chat owned by g.user or return 404."""
    row = get_db().execute(
        "SELECT * FROM chats WHERE id = ? AND user_id = ?",
        (chat_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404, description="Chat not found")
    return row


def _title_from(message: str) -> str:
    """Derive chat title from first line of first message."""
    first_line = message.strip().splitlines()[0] if message.strip() else "New chat"
    if len(first_line) <= _TITLE_MAX:
        return first_line
    return first_line[: _TITLE_MAX - 1].rstrip() + "…"


def _touch_chat(
    database: sqlite3.Connection, chat_id: int, first_message: str | None
) -> str | None:
    """Bump recency, and title the chat if it has no title yet.

    Returns the newly assigned title, or None if the chat already had one.
    The caller pushes that down the stream so the client can update its
    sidebar in place - otherwise every turn needs a follow-up GET /api/chats
    purely to discover a title the server already knew.
    """
    row = database.execute("SELECT name FROM chats WHERE id = ?", (chat_id,)).fetchone()

    if row and row["name"] is None and first_message:
        title = _title_from(first_message)
        database.execute(
            "UPDATE chats SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (title, chat_id),
        )
        return title

    database.execute(
        "UPDATE chats SET updated_at = datetime('now') WHERE id = ?", (chat_id,)
    )
    return None


@chat_bp.get("/chats")
@require_approval
def list_chats():
    rows = get_db().execute(
        "SELECT id, name, created_at, updated_at FROM chats"
        " WHERE user_id = ? AND is_temp = 0"
        " ORDER BY updated_at DESC",
        (g.user["id"],),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@chat_bp.post("/chats")
@require_approval
def create_chat():
    database = get_db()
    cur = database.execute(
        "INSERT INTO chats (user_id, name) VALUES (?, NULL)", (g.user["id"],)
    )
    database.commit()
    return jsonify({"id": cur.lastrowid, "name": None}), 201


@chat_bp.delete("/chats/<int:chat_id>")
@require_approval
def delete_chat(chat_id: int):
    _owned_chat(chat_id)
    database = get_db()
    database.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    database.commit()
    return "", 204


@chat_bp.post("/chats/<int:chat_id>/name")
@require_approval
def rename_chat(chat_id: int):
    _owned_chat(chat_id)
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    database = get_db()
    database.execute(
        "UPDATE chats SET name = ?, updated_at = datetime('now') WHERE id = ?",
        (name[:_TITLE_MAX], chat_id),
    )
    database.commit()
    return jsonify({"id": chat_id, "name": name[:_TITLE_MAX]})


@chat_bp.get("/chats/<int:chat_id>/messages")
@require_approval
def get_messages(chat_id: int):
    _owned_chat(chat_id)
    database = get_db()

    rows = database.execute(
        "SELECT id, message_type, message_content, timestamp"
        " FROM messages WHERE chat_id = ? AND hidden = 0"
        " ORDER BY timestamp, id",
        (chat_id,),
    ).fetchall()

    # Tool calls for the whole chat in one query, then grouped in Python.
    # The alternative - a query per message - is N+1, and a transcript with
    # forty replies would issue forty round trips to render one page.
    tools_by_message: dict[int, list] = {}
    for t in database.execute(
        "SELECT id, message_id, tool_name, arguments, duration_ms, is_error"
        " FROM tool_calls WHERE chat_id = ? AND hidden = 0 AND message_id IS NOT NULL"
        " ORDER BY id",
        (chat_id,),
    ).fetchall():
        tools_by_message.setdefault(t["message_id"], []).append({
            "id": t["id"],
            "name": t["tool_name"],
            "ms": t["duration_ms"],
            "is_error": bool(t["is_error"]),
        })

    out = []
    for r in rows:
        m = dict(r)
        tools = tools_by_message.get(r["id"])
        if tools:
            m["tools"] = tools
        out.append(m)

    return jsonify(out)


@chat_bp.post("/chats/<int:chat_id>/messages")
@require_approval
def post_message(chat_id: int):
    """Synchronous fallback endpoint."""
    chat = _owned_chat(chat_id)
    content = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not content:
        return jsonify({"error": "message is required"}), 400

    database = get_db()
    user_msg_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'user', ?)",
        (chat_id, content),
    ).lastrowid

    try:
        client = get_gemini_client()
        history = build_gemini_history(database, chat_id, before_msg_id=user_msg_id)
        chat_session = client.chats.create(
            model=DEFAULT_MODEL,
            history=history,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
        )
        resp = chat_session.send_message(content)
        reply_text = resp.text or "(Empty response)"
    except Exception as exc:
        # Roll back the user message rather than leaving it stranded with no
        # reply, and answer JSON - this endpoint's clients do not parse HTML
        # error pages.
        database.rollback()
        logger.error("Synchronous turn failed: %s", exc)
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502

    reply_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'stellar', ?)",
        (chat_id, reply_text),
    ).lastrowid

    _touch_chat(database, chat_id, content)
    database.commit()

    stored = database.execute(
        "SELECT id, message_type, message_content, timestamp"
        " FROM messages WHERE id IN (?, ?) ORDER BY id",
        (user_msg_id, reply_id),
    ).fetchall()
    return jsonify([dict(r) for r in stored]), 201


@chat_bp.post("/interaction/<interaction_id>/finish")
@require_approval
def finish_interaction(interaction_id: str):
    """Hand a widget's result back to the tool that is waiting for it.

    The waiting tool is polling a Redis list for exactly this. Pushing to it
    is what wakes the generation up, so this route is the bridge between a
    click in the browser and a blocked thread on the server.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "JSON body required"}), 400

    # Widgets are model-authored and run in the user's browser, so the
    # payload is untrusted twice over. It is only ever handed back to the
    # model as text - never evaluated - and a size cap keeps a runaway
    # script from filling Redis.
    payload = json.dumps(data)
    if len(payload) > 20_000:
        return jsonify({"error": "Response too large"}), 413

    try:
        r = _redis_client(current_app.config["REDIS_URL"])
        # A TTL, because nothing consumes this if the generation already
        # gave up waiting.
        r.rpush(_k_interaction(interaction_id), payload)
        r.expire(_k_interaction(interaction_id), INTERACTION_TIMEOUT)
    except Exception as exc:
        logger.error("Could not deliver interaction result: %s", exc)
        return jsonify({"error": "Could not deliver the response"}), 503

    return jsonify({"delivered": True})


@chat_bp.post("/stream/<query_id>/stop")
@require_approval
def stop_stream(query_id: str):
    """Stop a generation in progress.

    Ownership is re-checked here even though the query id is unguessable:
    a leaked id must not become a way to interrupt someone else's work.
    """
    redis_url = current_app.config["REDIS_URL"]

    args = get_query_args(redis_url, query_id)
    if args is None or args.get("user_id") != g.user["id"]:
        return jsonify({"error": "Unknown or expired query"}), 404

    signal_cancel(redis_url, args["chat_id"], query_id)
    logger.info("Stop requested for query %s by user %s", query_id, g.user["id"])
    return jsonify({"stopped": True})


@chat_bp.post("/chats/<int:chat_id>/inject")
@require_approval
def inject_message(chat_id: int):
    """Add a follow-up to a generation that is already running.

    Distinct from starting a new turn: the agent is mid-answer, and this
    steers it rather than queueing behind it. The message is stored
    immediately so it appears in the transcript at once, and queued in Redis
    for the loop to collect at its next safe point.
    """
    _owned_chat(chat_id)
    message = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    redis_url = current_app.config["REDIS_URL"]

    # Nothing running means there is nothing to steer, and the caller should
    # start a normal turn instead. 409 rather than 400: the request is
    # well-formed, it just conflicts with the current state.
    if not chat_is_generating(redis_url, chat_id):
        return jsonify({"error": "No generation is running in this chat"}), 409

    database = get_db()
    msg_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'user', ?)", (chat_id, message)).lastrowid
    database.commit()

    try:
        _rc = _redis_client(redis_url)
        _rc.rpush(f"inject:{chat_id}", json.dumps({
            "message": message, "message_id": msg_id, "at": time.time()}))
        # A lifetime, because this queue is only drained at one seam in the
        # turn loop: a turn that dies on a quota error leaves the item
        # behind, and an unrelated turn hours later would pick it up and
        # abruptly answer a stale question. It cannot outlive the longest
        # a turn can plausibly run.
        _rc.expire(f"inject:{chat_id}", INJECT_TTL)
    except Exception as exc:
        logger.error("Could not queue injection: %s", exc)
        return jsonify({"error": "Could not queue the follow-up"}), 503

    return jsonify({"id": msg_id, "queued": True}), 202


@chat_bp.get("/keys/status")
@require_approval
def key_status():
    """Which keys are usable right now, and why the rest are not.

    Admin only, and it returns fingerprints rather than keys - this is a
    diagnostic, not a way to read credentials back out of the server.
    """
    if not g.user["is_admin"]:
        return jsonify({"error": "Admin only"}), 403

    keys = gemini_keys()
    models = [DEFAULT_MODEL, FALLBACK_MODEL]
    rows = KEY_MANAGER.status(keys, models)

    # Attach how much of each daily budget is spent, so the pool's remaining
    # headroom is visible before it runs out rather than after.
    remaining = 0
    for row, key in zip(rows, keys):
        row["usage"] = {m: KEY_MANAGER.usage(key, m) for m in models}
        remaining += sum(max(0, u["rpd_limit"] - u["rpd_used"])
                         for u in row["usage"].values())

    return jsonify({
        "models": models,
        "keys": rows,
        "usable": sum(1 for r in rows if r["usable"]),
        "total": len(rows),
        "requests_remaining_today": remaining,
        "models_overloaded": [m for m in models if KEY_MANAGER.is_model_blocked(m)],
        # Redis means every worker agrees; local means they do not.
        "backend": "redis" if KEY_MANAGER._r() is not None else "process-local",
    })


@chat_bp.post("/chats/<int:chat_id>/query")
@require_approval
def register_chat_query(chat_id: int):
    """Step 1 of a turn: register arguments and return query_id."""
    _owned_chat(chat_id)
    message = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        qid = register_query(
            current_app.config["REDIS_URL"],
            {
                "chat_id": chat_id,
                "user_id": g.user["id"],
                "message": message,
            },
        )
        return jsonify({"query_id": qid}), 202
    except (redis.exceptions.ConnectionError, redis.exceptions.RedisError) as exc:
        logger.error("Redis connection failed in register_chat_query: %s", exc)
        return jsonify({
            "error": "Redis is not running. Please start Docker Desktop and run 'docker start stellar-redis' to chat."
        }), 503


@chat_bp.get("/stream/<query_id>")
@require_approval
def stream_chat(query_id: str):
    """Step 2 of a turn: attach to the SSE stream."""
    redis_url = current_app.config["REDIS_URL"]
    try:
        args = get_query_args(redis_url, query_id)
    except (redis.exceptions.ConnectionError, redis.exceptions.RedisError) as exc:
        logger.error("Redis connection failed in stream_chat: %s", exc)
        return jsonify({"error": "Redis is not running"}), 503
    if args is None or args.get("user_id") != g.user["id"]:
        return jsonify({"error": "Unknown or expired query"}), 404

    if claim_stream(redis_url, query_id):
        run_worker(current_app._get_current_object(), query_id, gemini_producer)

    # Last-Event-ID wins over ?from=. EventSource reconnects to the URL
    # it was given, which always carries ?from=0, and sets the header to
    # where it actually got to. Letting the query string win meant every
    # dropped connection replayed the whole turn and printed the reply
    # twice into the same bubble.
    start = None
    last_id = request.headers.get("Last-Event-ID")
    if last_id is not None:
        try:
            start = int(last_id) + 1
        except ValueError:
            start = None

    if start is None:
        start = 0
        if "from" in request.args:
            try:
                start = int(request.args["from"])
            except ValueError:
                pass

    return Response(
        consume_stream(redis_url, query_id, start),
        headers=sse_headers(),
    )


# Types a browser can show by itself. Anything else is offered as a
# download, so a .pptx or .csv does not open as a page of garbage.
#
# .html, .htm and .svg are deliberately NOT here. These files are written
# by the model, which is steerable by any page it reads, and they are
# served from the app's own origin - so an inline one is a script running
# as the logged-in user, with their cookies, against their own API. That
# is precisely the hole the sandboxed widget iframe exists to close. They
# download instead.
_INLINE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                 ".txt", ".md", ".json"}


@chat_bp.get("/outputs/<int:chat_id>/<path:filename>")
@require_approval
def serve_output(chat_id: int, filename: str):
    """A file a tool produced for this chat.

    Ownership first: the folder is derived from the logged-in user and the
    chat they must own, so a link pasted to someone else 404s for them.
    send_from_directory refuses paths that escape the folder.
    """
    _owned_chat(chat_id)
    folder = _outputs_dir(g.user["id"], chat_id)
    ext = os.path.splitext(filename)[1].lower()
    resp = send_from_directory(
        folder, filename, as_attachment=ext not in _INLINE_TYPES,
        max_age=3600)
    # Belt and braces around the same hole. nosniff stops a browser
    # deciding a .txt is really HTML, and the policy denies scripting to
    # anything that does render here, so a file that slips into the
    # inline set still cannot execute against this origin.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox")
    return resp


# ---------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------
def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=str(PROJECT_ROOT / os.environ.get("DATABASE_NAME", "stellar_local.db")),
        REDIS_URL=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        SESSION_COOKIE_NAME="stellar_session_main",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Off by default so local http development keeps working; the
        # deploy guide sets SESSION_COOKIE_SECURE=1, which is what stops
        # the session cookie travelling over the plain-http :80 vhost.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        OUTPUTS_DIR=str(PROJECT_ROOT / "outputs"),
    )

    if test_config:
        app.config.update(test_config)

    # Without an explicit handler, logging falls back to lastResort: WARNING
    # and above, bare message, no timestamp. Since the retry and fallback
    # paths report themselves through this logger, that output needs to be
    # readable - it is the only view into why a turn misbehaved.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    # The SDK logs every HTTP request at INFO, which drowns everything else.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Copy keys.env.example to keys.env "
            "and generate one:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"'
        )

    # Wire database lifecycle
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

    # Point the key manager at Redis so rate-limit state is shared across
    # Gunicorn workers. Without this each worker keeps its own view and
    # three of the four go on hammering a key the fourth knows is dead.
    KEY_MANAGER._redis_url = app.config["REDIS_URL"]

    # Listen for stops published by the other workers. Without this a stop
    # only works when it happens to land on the worker that is generating -
    # which under four workers is one time in four.
    if not app.config.get("TESTING"):
        with app.app_context():
            init_db()
        start_cancel_listener(app.config["REDIS_URL"])
        # Scheduled tasks. Every worker runs one; the atomic claim in
        # run_due_tasks keeps them from starting the same task twice.
        start_scheduler(app)

    # Intercept wildcard subdomains (phase 9)
    @app.before_request
    def intercept_subdomains():
        return handle_subdomain_proxy(app)

    # Wire user loader
    @app.before_request
    def load_logged_in_user() -> None:
        user_id = session.get("user_id")
        if user_id is None:
            g.user = None
            return

        g.user = get_db().execute(
            "SELECT id, username, display_name, is_approved, is_admin"
            " FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if g.user is None:
            session.clear()

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)

    @app.route("/")
    def index():
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return render_template("index.html", display_name=session.get("display_name"))

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/sw.js")
    def service_worker():
        """A service worker whose only job is to remove itself.

        Stellar does not use one. But a service worker is registered
        against an ORIGIN, and every local project on 127.0.0.1:5000
        shares that origin - so one left behind by something else you ran
        on this port goes on intercepting Stellar's requests and failing
        them ("The FetchEvent resulted in a network error"). A 404 does
        not help: the browser keeps the old worker when the update fetch
        fails. Serving a valid worker that unregisters itself is the
        documented way out, and it costs nothing when no worker exists.
        """
        script = chr(10).join([
            "self.addEventListener('install', () => self.skipWaiting());",
            "self.addEventListener('activate', (e) => e.waitUntil(",
            "  self.registration.unregister()",
            "    .then(() => self.clients.matchAll())",
            "    .then((cs) => cs.forEach((c) => c.navigate(c.url)))));",
            "",
        ])
        return Response(
            script,
            mimetype="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            PROJECT_ROOT / "static",
            "favicon.ico",
            mimetype="image/vnd.microsoft.icon",
        )

    return app


def _check_interpreter() -> None:
    """Refuse to start with an interpreter that is missing dependencies.

    This exists because of a real failure. The server was started with a
    system-wide python rather than .venv, which happened to have most
    packages installed and so ran fine - until phase 5, when lab_execute
    needed `docker`, which only the venv had. Every sandbox call failed in
    one millisecond with a ModuleNotFoundError, and the agent apologised to
    the user about an unstable environment.

    Nothing about that pointed at the real cause. Failing loudly at startup
    costs one second and removes the whole class of problem.
    """
    import importlib.util

    required = {
        "flask": "Flask", "google.genai": "google-genai", "redis": "redis",
        "dotenv": "python-dotenv", "docker": "docker", "bs4": "beautifulsoup4",
        "requests": "requests",
    }
    missing = [pkg for mod, pkg in required.items()
               if importlib.util.find_spec(mod) is None]

    if not missing:
        return

    venv = PROJECT_ROOT / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")

    print("\n  Cannot start: missing packages\n")
    print(f"    interpreter : {sys.executable}")
    print(f"    missing     : {', '.join(missing)}\n")
    if venv.exists() and Path(sys.executable) != venv:
        print("  This is the wrong Python. Start the server with the project")
        print("  virtual environment instead:\n")
        print(f"      {venv} app.py\n")
    else:
        print("  Install them with:\n")
        print("      uv pip install -r requirements.txt\n")
    sys.exit(1)


if __name__ == "__main__":
    _check_interpreter()
    application = create_app()

    with application.app_context():
        existed = Path(application.config["DATABASE"]).exists()
        if existed:
            # Report what is about to be added, so a schema change arriving
            # with a new phase is visible rather than silent.
            pending = schema_drift(get_db())
            if pending:
                print(f"  Applying schema additions: {', '.join(pending)}")
        init_db()
        if not existed:
            print(f"  Created database: {application.config['DATABASE']}")

    application.run(host="127.0.0.1", port=5000, debug=True)
