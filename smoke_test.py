"""End-to-end smoke test for phases 1-3.

    .venv/Scripts/python.exe smoke_test.py           # offline, no API calls
    .venv/Scripts/python.exe smoke_test.py --live    # also does one real turn

Runs against a throwaway database and Redis db 15, so it never touches
stellar_local.db or the app's Redis keyspace.

The default run makes no network calls at all: the LLM producer is swapped
for a stub, so the transport can be verified without spending quota or
failing because Google had a bad minute. --live adds one real turn.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import app as A  # noqa: E402

REDIS_TEST_URL = "redis://localhost:6379/15"
LIVE = "--live" in sys.argv

failures: list[str] = []


def check(label: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def parse_sse(raw: str) -> list[tuple[int | None, dict]]:
    out = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        m_id = re.search(r"^id: (\d+)$", block, re.M)
        m_data = re.search(r"^data: (.*)$", block, re.M)
        if m_data:
            out.append((int(m_id.group(1)) if m_id else None,
                        json.loads(m_data.group(1))))
    return out


def stub_producer(r, args):
    """Stand-in for the model, so transport tests need no network."""
    database = A.get_db()
    uid = database.execute(
        "INSERT INTO messages (chat_id, message_type, message_content)"
        " VALUES (?, 'user', ?)",
        (args["chat_id"], args["message"]),
    ).lastrowid
    title = A._touch_chat(database, args["chat_id"], args["message"])
    database.commit()

    yield {"type": "user_message", "id": uid}
    if title:
        yield {"type": "chat_title", "chat_id": args["chat_id"], "name": title}
    yield {"type": "status", "text": "Thinking…"}
    for tok in ("**stub** ", "reply ", "for ", args["message"]):
        yield {"type": "token", "text": tok}

    rid = A._save_reply(database, args["chat_id"],
                        f"**stub** reply for {args['message']}")
    yield {"type": "message", "id": rid}


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "smoke.db"
    app = A.create_app({
        "DATABASE": str(tmp),
        "TESTING": True,
        "REDIS_URL": REDIS_TEST_URL,
    })
    with app.app_context():
        A.init_db()

    import redis as redis_lib
    redis_lib.from_url(REDIS_TEST_URL).flushdb()

    print("\n  Stellar smoke test" + ("  (live)" if LIVE else "  (offline)") + "\n")

    # --- schema ------------------------------------------------------
    conn = sqlite3.connect(tmp)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("schema: users/chats/messages exist",
          {"users", "chats", "messages"} <= tables)
    check("schema: WAL enabled",
          conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal")
    conn.close()

    # --- auth --------------------------------------------------------
    c = app.test_client()
    check("anonymous API access refused",
          c.get("/api/chats").status_code in (302, 401))

    c.post("/auth/register", data={"username": "a@b.com",
                                   "password": "hunter2hunter2"},
           follow_redirects=True)
    check("login works",
          c.post("/auth/login", data={"username": "a@b.com",
                                      "password": "hunter2hunter2"}
                 ).status_code == 302)
    check("first user is auto-approved admin",
          c.get("/api/chats").status_code == 200)

    bad = app.test_client()
    bad.post("/auth/login", data={"username": "a@b.com", "password": "wrong"})
    check("wrong password rejected",
          bad.get("/api/chats").status_code in (302, 401))

    # --- second user is gated ----------------------------------------
    c2 = app.test_client()
    c2.post("/auth/register", data={"username": "m@b.com",
                                    "password": "hunter2hunter2"},
            follow_redirects=True)
    c2.post("/auth/login", data={"username": "m@b.com",
                                 "password": "hunter2hunter2"})
    check("second user needs approval", c2.get("/api/chats").status_code == 403)

    conn = sqlite3.connect(tmp)
    conn.execute("UPDATE users SET is_approved=1 WHERE username='m@b.com'")
    conn.commit()
    conn.close()

    # --- chats -------------------------------------------------------
    chat = c.post("/api/chats").get_json()
    check("create chat", isinstance(chat.get("id"), int))
    check("cannot read another user's chat",
          c2.get(f"/api/chats/{chat['id']}/messages").status_code == 404)

    # --- streaming transport (stubbed producer) ----------------------
    r = c.post(f"/api/chats/{chat['id']}/query", json={"message": "hello"})
    check("register returns 202 + query_id",
          r.status_code == 202 and r.get_json().get("query_id"))
    qid = r.get_json()["query_id"]

    check("registering writes nothing to the database",
          len(c.get(f"/api/chats/{chat['id']}/messages").get_json()) == 0)
    check("empty message rejected",
          c.post(f"/api/chats/{chat['id']}/query",
                 json={"message": "  "}).status_code == 400)
    check("another user cannot attach to the stream",
          c2.get(f"/api/stream/{qid}").status_code == 404)
    check("unknown query id is 404",
          c.get("/api/stream/nope").status_code == 404)

    real_producer = A.gemini_producer
    A.gemini_producer = stub_producer          # transport test, no network
    try:
        resp = c.get(f"/api/stream/{qid}")
        check("content-type is text/event-stream",
              resp.headers.get("Content-Type", "").startswith("text/event-stream"))
        check("X-Accel-Buffering disabled for nginx",
              resp.headers.get("X-Accel-Buffering") == "no")

        events = parse_sse(resp.get_data(as_text=True))
        kinds = [e["type"] for _, e in events]
        check("stream terminates with done", kinds[-1] == "done")
        check("stream carries tokens", kinds.count("token") >= 3)
        check("stream reports the committed message id", "message" in kinds)
        check("event ids are sequential from 0",
              [i for i, _ in events] == list(range(len(events))))

        stored = c.get(f"/api/chats/{chat['id']}/messages").get_json()
        check("user message + reply both persisted", len(stored) == 2)
        check("chat auto-titled from first message",
              c.get("/api/chats").get_json()[0]["name"] == "hello")
        # The title travels in the stream, so the client needs no follow-up
        # GET /api/chats to discover it.
        titles = [e for _, e in events if e["type"] == "chat_title"]
        check("title pushed down the stream",
              len(titles) == 1 and titles[0]["name"] == "hello")

        # resumability: the property the whole design exists for
        replay = parse_sse(c.get(f"/api/stream/{qid}?from=0").get_data(as_text=True))
        check("reattaching from 0 replays the whole stream",
              [e for _, e in replay] == [e for _, e in events])

        mid = len(events) // 2
        resumed = parse_sse(
            c.get(f"/api/stream/{qid}?from={mid}").get_data(as_text=True))
        check("resuming from an index skips earlier events",
              len(resumed) == len(events) - mid and resumed[0][0] == mid)

        before = sqlite3.connect(tmp).execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0]
        c.get(f"/api/stream/{qid}?from=0")
        after = sqlite3.connect(tmp).execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0]
        check("reattaching does not re-run the worker", before == after)
    finally:
        A.gemini_producer = real_producer

    # --- error classification ----------------------------------------
    check("transient errors classified",
          A._classify_error(Exception("Server disconnected without sending a response")) == "transient")
    check("missing model classified",
          A._classify_error(Exception("404 NOT_FOUND")) == "missing_model")
    check("quota classified",
          A._classify_error(Exception("429 RESOURCE_EXHAUSTED")) == "quota")
    check("unknown errors are fatal",
          A._classify_error(Exception("something odd")) == "fatal")

    # --- history mapping ---------------------------------------------
    with app.app_context():
        db = A.get_db()
        hist = A.build_gemini_history(db, chat["id"])
        check("history maps to alternating gemini roles",
              [h.role for h in hist] == ["user", "model"])
        check("history starts with a user turn",
              not hist or hist[0].role == "user")

    # --- live turn ----------------------------------------------------
    if LIVE:
        chat2 = c.post("/api/chats").get_json()
        qid2 = c.post(f"/api/chats/{chat2['id']}/query",
                      json={"message": "Reply with exactly: PONG"}
                      ).get_json()["query_id"]
        ev = parse_sse(c.get(f"/api/stream/{qid2}").get_data(as_text=True))
        kinds = [e["type"] for _, e in ev]
        text = "".join(e["text"] for _, e in ev if e["type"] == "token")
        errs = [e["message"] for _, e in ev if e["type"] == "error"]
        check("live turn produced tokens", kinds.count("token") >= 1)
        check("live turn committed a reply", "message" in kinds)
        check(f"live reply looks sane (got {text.strip()[:40]!r})",
              "pong" in text.lower())
        if errs:
            print(f"        live errors: {errs}")

    redis_lib.from_url(REDIS_TEST_URL).flushdb()

    print()
    if failures:
        print(f"  {len(failures)} FAILED: {', '.join(failures)}\n")
        return 1
    print(f"  All checks passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
