"""Chat API: sessions and messages.

Phase 1 has no model. ``POST /api/chats/<id>/messages`` stores the user's
message and replies with a deterministic echo, so the storage, retrieval and
rendering path can be proven correct before any AI is involved.

Phase 2 replaces the echo with an SSE stream; phase 3 puts Gemini behind it.
The request and response shapes here are chosen so those swaps do not change
the frontend's data model.
"""

from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, request

from auth import require_approval
from db import get_db

bp = Blueprint("chat", __name__, url_prefix="/api")

# Longest chat title auto-generated from a first message.
_TITLE_MAX = 48


def _owned_chat(chat_id: int):
    """Fetch a chat, or abort, unless it belongs to the current user.

    chat_id arrives from the URL, so it is attacker-controlled: nothing stops
    a logged-in user requesting /api/chats/99/messages. Every route that
    touches a chat goes through this function.

    404 rather than 403 on a foreign chat, deliberately - a 403 would confirm
    that the id exists and belongs to someone.
    """
    row = get_db().execute(
        "SELECT * FROM chats WHERE id = ? AND user_id = ?",
        (chat_id, g.user["id"]),
    ).fetchone()

    if row is None:
        abort(404, description="Chat not found")
    return row


def _title_from(message: str) -> str:
    """Derive a chat title from its first message.

    Phase 3 replaces this with a model-generated title. A truncated first
    line is a decent stand-in and costs no tokens.
    """
    first_line = message.strip().splitlines()[0] if message.strip() else "New chat"
    if len(first_line) <= _TITLE_MAX:
        return first_line
    return first_line[: _TITLE_MAX - 1].rstrip() + "…"


# ---------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------
@bp.get("/chats")
@require_approval
def list_chats():
    rows = get_db().execute(
        "SELECT id, name, created_at, updated_at FROM chats"
        " WHERE user_id = ? AND is_temp = 0"
        " ORDER BY updated_at DESC",
        (g.user["id"],),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/chats")
@require_approval
def create_chat():
    database = get_db()
    cur = database.execute(
        "INSERT INTO chats (user_id, name) VALUES (?, NULL)", (g.user["id"],)
    )
    database.commit()
    return jsonify({"id": cur.lastrowid, "name": None}), 201


@bp.delete("/chats/<int:chat_id>")
@require_approval
def delete_chat(chat_id: int):
    _owned_chat(chat_id)
    database = get_db()
    # Messages go too, via ON DELETE CASCADE - which only works because
    # db.py sets PRAGMA foreign_keys = ON. Without that pragma this would
    # silently orphan every message in the chat.
    database.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    database.commit()
    return "", 204


@bp.post("/chats/<int:chat_id>/name")
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


# ---------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------
@bp.get("/chats/<int:chat_id>/messages")
@require_approval
def get_messages(chat_id: int):
    _owned_chat(chat_id)

    # hidden = 0 is the whole point of the column: this is the UI's view of
    # the conversation. Phase 3 adds a separate reader for the model's view,
    # which includes hidden rows.
    rows = get_db().execute(
        "SELECT id, message_type, message_content, timestamp"
        " FROM messages WHERE chat_id = ? AND hidden = 0"
        " ORDER BY timestamp, id",
        (chat_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/chats/<int:chat_id>/messages")
@require_approval
def post_message(chat_id: int):
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

    # --- phase 2 replaces this block with a streamed model response -----
    reply = f"you said: {content}"
    # -------------------------------------------------------------------

    reply_id = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'stellar', ?)",
        (chat_id, reply),
    ).lastrowid

    # Name the chat from its first message, and bump recency for the sidebar.
    if chat["name"] is None:
        database.execute(
            "UPDATE chats SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (_title_from(content), chat_id),
        )
    else:
        database.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?", (chat_id,)
        )

    # One commit for all four statements. If the process dies midway the
    # whole turn rolls back, rather than leaving a user message with no
    # reply or a chat whose title points at a message that was never stored.
    database.commit()

    stored = database.execute(
        "SELECT id, message_type, message_content, timestamp"
        " FROM messages WHERE id IN (?, ?) ORDER BY id",
        (user_msg_id, reply_id),
    ).fetchall()

    return jsonify([dict(r) for r in stored]), 201
