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
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

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
    jsonify,
    redirect,
    render_template,
    request,
    session,
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
QUERY_ARGS_TTL = 60 * 60 * 24  # 24 hours for query registration arguments
POLL_INTERVAL = 0.05          # 50ms Redis polling interval
IDLE_TIMEOUT = 120            # give up on a silent stream after 2 minutes
# How often to write a comment frame while a stream is silent. This doubles
# as dead-client detection, so it wants to be short: an abandoned stream
# holds its worker thread and socket until the next write fails.
KEEPALIVE_INTERVAL = 10

DEFAULT_MODEL = "gemini-3-flash-preview"
FALLBACK_MODEL = "gemini-3.6-flash"

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

SYSTEM_INSTRUCTION = (
    "You are Stellar, a capable, sharp, and concise AI assistant. "
    "Respond helpfully and accurately using clean, well-structured Markdown. "
    "Avoid unnecessary conversational filler and focus on direct, high-quality answers."
)


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


def schema_drift(conn: sqlite3.Connection) -> list[str]:
    """Names in schema.sql that do not exist in this database yet."""
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
        conn.executescript(sql)
        conn.commit()
        conn.close()
    else:
        db = get_db()
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
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
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
            yield sse_frame(
                {"type": "error", "message": "Stream timed out."}, index
            )
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

# chat_id -> (threading.Event, query_id). Process-local by nature: the Event
# only means anything to the thread holding it.
ACTIVE_GENERATIONS: dict[int, tuple[threading.Event, str]] = {}
_ACTIVE_LOCK = threading.Lock()


def register_generation(chat_id: int, query_id: str) -> threading.Event:
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
    return event


def release_generation(chat_id: int, query_id: str) -> None:
    with _ACTIVE_LOCK:
        current = ACTIVE_GENERATIONS.get(chat_id)
        # Only clear our own claim - a newer generation may already own it.
        if current and current[1] == query_id:
            ACTIVE_GENERATIONS.pop(chat_id, None)


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
            # The model can be steered into following a chain; a redirect to
            # a private address would bypass the check above.
            allow_redirects=True,
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

    if len(text) > TOOL_OUTPUT_LIMIT:
        text = text[:TOOL_OUTPUT_LIMIT] + f"\n\n[truncated at {TOOL_OUTPUT_LIMIT} characters]"

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
    """A Docker client, or a clear error explaining what to start."""
    import docker
    try:
        c = docker.from_env()
        c.ping()
        return c
    except Exception as exc:
        raise RuntimeError(
            f"Docker is not reachable ({type(exc).__name__}). "
            "Start Docker Desktop, then run docker_setup.py."
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


# The registry handed to the model. Adding a tool means writing the function
# and adding it here - there is no schema to maintain separately.
AVAILABLE_TOOLS = [get_current_time, fetch_url, web_search, lab_execute,
                   compress_memory]
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
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
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
        release_generation(chat_id, query_id)


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
    cancel_event = register_generation(chat_id, query_id)

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
                            yield {"type": "status", "text": "Switching model\\u2026"}
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
                name=name, response={"result": result}))

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
    with _ACTIVE_LOCK:
        running = chat_id in ACTIVE_GENERATIONS or str(chat_id) in ACTIVE_GENERATIONS
    if not running:
        return jsonify({"error": "No generation is running in this chat"}), 409

    database = get_db()
    msg_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'user', ?)", (chat_id, message)).lastrowid
    database.commit()

    try:
        _redis_client(redis_url).rpush(f"inject:{chat_id}", json.dumps({
            "message": message, "message_id": msg_id, "at": time.time()}))
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

    qid = register_query(
        current_app.config["REDIS_URL"],
        {
            "chat_id": chat_id,
            "user_id": g.user["id"],
            "message": message,
        },
    )
    return jsonify({"query_id": qid}), 202


@chat_bp.get("/stream/<query_id>")
@require_approval
def stream_chat(query_id: str):
    """Step 2 of a turn: attach to the SSE stream."""
    redis_url = current_app.config["REDIS_URL"]
    args = get_query_args(redis_url, query_id)
    if args is None or args.get("user_id") != g.user["id"]:
        return jsonify({"error": "Unknown or expired query"}), 404

    if claim_stream(redis_url, query_id):
        run_worker(current_app._get_current_object(), query_id, gemini_producer)

    last_id = request.headers.get("Last-Event-ID")
    try:
        start = int(last_id) + 1 if last_id is not None else 0
    except ValueError:
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
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
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
        start_cancel_listener(app.config["REDIS_URL"])

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

    return app


if __name__ == "__main__":
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
