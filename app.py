"""Stellar - Complete Application (Single-File Architecture).

Contains all subsystems in one cohesive module:
- Database Layer: SQLite with WAL mode, foreign keys, connection lifecycle
- Authentication Layer: Registration, login, session auth, approval gate
- Resumable Streaming: Redis list event queue, two-phase query/stream, SSE
- Phase 3 LLM Engine: Live streaming via Google GenAI SDK (gemini-3-flash-preview)
- Chat Management: Chat sessions, messages, title generation, and REST API
"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
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


def init_db(database_path: str | Path | None = None) -> None:
    """Apply schema.sql idempotently to initialize tables."""
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


def run_worker(app: Flask, qid: str, produce_fn) -> None:
    """Run producer in a daemon thread, piping everything to Redis."""
    redis_url = app.config["REDIS_URL"]

    def target() -> None:
        with app.app_context():
            r = _redis_client(redis_url)
            args = get_query_args(redis_url, qid) or {}
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
                emit(r, qid, {"type": "done"})

    threading.Thread(target=target, name=f"stream-{qid[:8]}", daemon=True).start()


# ---------------------------------------------------------------------
# Phase 3: Gemini LLM Engine
# ---------------------------------------------------------------------
def get_gemini_client() -> genai.Client:
    """Return an authenticated Google GenAI client."""
    api_key = os.environ.get("PRIMARY_API_KEY")
    if not api_key:
        raise RuntimeError("PRIMARY_API_KEY is not set in keys.env")
    return genai.Client(api_key=api_key)


def build_gemini_history(database: sqlite3.Connection, chat_id: int, before_msg_id: int | None = None) -> list[types.Content]:
    """Retrieve previous conversation messages and map them into Gemini Content objects."""
    query = (
        "SELECT id, message_type, message_content FROM messages"
        " WHERE chat_id = ? AND hidden = 0"
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


def _save_reply(database: sqlite3.Connection, chat_id: int, text: str) -> int:
    """Commit a model reply and return its row id."""
    reply_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'stellar', ?)",
        (chat_id, text),
    ).lastrowid
    database.commit()
    return reply_id


def gemini_producer(r: redis.Redis, args: dict):
    """Phase 3 producer: stream a Gemini reply into the event queue.

    Recovery rules, in priority order:

      1. Once any token has been emitted the turn is committed to its model.
         Retrying would replay the reply from the beginning and the client,
         which simply appends tokens, would render it twice. So a failure
         after first output saves what was produced rather than retrying.
      2. Transient failures before any output are retried on the same model,
         with backoff. These are common: a bare
         "Server disconnected without sending a response" kills roughly one
         call in ten and succeeds immediately on retry.
      3. A missing or unsupported model falls through to FALLBACK_MODEL.
      4. Anything else fails the turn.
    """
    chat_id = args["chat_id"]
    message = args["message"]
    database = get_db()

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
    yield {"type": "status", "text": "Thinking…"}

    history = build_gemini_history(database, chat_id, before_msg_id=user_msg_id)
    client = get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.LOW
        ),
    )

    accumulated: list[str] = []
    last_error: Exception | None = None

    for model in (DEFAULT_MODEL, FALLBACK_MODEL):
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            try:
                chat_session = client.chats.create(
                    model=model, history=history, config=config
                )
                for chunk in chat_session.send_message_stream(message):
                    if chunk.text:
                        accumulated.append(chunk.text)
                        yield {"type": "token", "text": chunk.text}

                # Success. Commit and end the turn.
                reply = "".join(accumulated).strip() or "(Empty response from model)"
                yield {"type": "message", "id": _save_reply(database, chat_id, reply)}
                return

            except Exception as exc:
                last_error = exc
                kind = _classify_error(exc)

                # Rule 1: already streamed output - keep it, do not replay.
                if accumulated:
                    logger.warning(
                        "Model %s failed after %d chars of output (%s): %s",
                        model, sum(len(a) for a in accumulated), kind, exc,
                    )
                    partial = "".join(accumulated).strip()
                    partial += "\n\n*[Response interrupted: connection lost]*"
                    yield {
                        "type": "message",
                        "id": _save_reply(database, chat_id, partial),
                    }
                    return

                if kind == "transient" and attempt < MAX_LLM_ATTEMPTS:
                    delay = LLM_RETRY_BACKOFF ** (attempt - 1)
                    logger.warning(
                        "Model %s attempt %d/%d failed (transient): %s. "
                        "Retrying in %.1fs.",
                        model, attempt, MAX_LLM_ATTEMPTS, exc, delay,
                    )
                    yield {
                        "type": "status",
                        "text": f"Connection issue, retrying ({attempt}/{MAX_LLM_ATTEMPTS})…",
                    }
                    time.sleep(delay)
                    continue

                if kind == "missing_model":
                    logger.warning(
                        "Model %s unavailable, falling back to %s: %s",
                        model, FALLBACK_MODEL, exc,
                    )
                    break  # try the next model

                # quota or fatal, or transient attempts exhausted
                logger.error("Model %s failed (%s): %s", model, kind, exc)
                break

    # Every model and attempt exhausted.
    friendly = (
        "The model is rate limited. Try again shortly."
        if _classify_error(last_error) == "quota"
        else f"{type(last_error).__name__}: {last_error}"
    )
    yield {"type": "error", "message": friendly}

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
    rows = get_db().execute(
        "SELECT id, message_type, message_content, timestamp"
        " FROM messages WHERE chat_id = ? AND hidden = 0"
        " ORDER BY timestamp, id",
        (chat_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


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
        if not Path(application.config["DATABASE"]).exists():
            init_db()
            print(f"Created database: {application.config['DATABASE']}")

    application.run(host="127.0.0.1", port=5000, debug=True)
