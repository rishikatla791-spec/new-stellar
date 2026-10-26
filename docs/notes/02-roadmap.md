# Stellar Rebuild Roadmap

**Rule: every phase ends with something that runs.** No phase is "write 3 files and
hope." If you cannot demo it, the phase is not done.

Work the phases in order. The ordering is deliberate — each one teaches a concept the
next one depends on.

---

## Stage A — Foundations (no AI yet)

### Phase 0: Environment
Python venv, Redis, Docker, a `keys.env`, one Gemini API key.

**Done when:** `redis-cli ping` returns PONG, `docker run hello-world` works, and a
3-line Python script prints a Gemini response.

**Why first:** 90% of "the AI doesn't work" bugs are actually environment bugs. Prove
the environment before you write the app.

---

### Phase 1: Skeleton — Flask + SQLite + a chat UI
Routes: login (fake/local for now), `/`, `POST /message`, `GET /get_history`.
Tables: `users`, `chats`, `messages`.
Frontend: one HTML page, a textarea, a message list. No streaming. No AI — the server
replies with `"you said: " + text`.

**Done when:** you can send messages, refresh the page, and your history is still there.

**Concepts:** Flask app factory, `get_db()` with `g`, SQLite WAL mode, Jinja
templates, session cookies.

> Reference: `initialize_database()` at `app.py:902`, `insert_message()` at
> `app.py:1352`, `get_conversation_history()` at `app.py:1501`.

---

### Phase 2: The streaming pipeline (still no AI)
Implement `register_query` + `refine_stream` + the background thread + the Redis
relay. Have the background thread push 20 fake tokens with a 200ms sleep.

**Done when:** tokens appear one-by-one in the browser, AND you can hit F5 mid-stream,
reconnect with the same `query_id`, and keep receiving the rest.

**Concepts:** SSE format (`data: {...}\n\n`), `Response(stream_with_context(...),
mimetype='text/event-stream')`, `EventSource` in JS, threads + app context, Redis
lists as a queue.

**This is the single hardest phase.** Doing it with fake tokens instead of a live LLM
is the whole trick — when it breaks, you know it is the plumbing, not the model.

> Reference: `app.py:3790-4360`.

---

## Stage B — The agent

### Phase 3: Real LLM streaming
Swap fake tokens for `google-genai`. Build the system prompt. Feed conversation
history. Save the completed reply to `messages`.

**Done when:** it is a working ChatGPT clone with persistent history.

**Concepts:** `genai.Client`, `client.chats.create`, history serialization, system
instructions, token counting.

---

### Phase 4: The tool loop — *this is the "it's an agent now" moment*
Disable automatic function calling. Write the manual `while True` loop. Ship exactly
**two trivial tools** to start:
- `get_current_time(status, timeout)` — proves dispatch works
- `web_search(query, status, timeout)` — proves real I/O works (Tavily, free tier)

Then add `status` yielding so the UI shows "Searching the web..." before the call runs.

**Done when:** you ask "what's the news today?" and watch it call the tool, show a
status line, and answer from the result.

**Concepts:** function-calling schemas from docstrings, the request/response part
protocol, multi-turn tool loops, dispatch via `getattr`.

> Reference: `app.py:2360-2620`, `agent_tools.py:2337`.

---

### Phase 5: The Docker sandbox — `lab_execute`
Build a lab image. Create a per-user network with ICC disabled. Start one container
per chat with `/lab` bind-mounted. Run commands via the Docker SDK and return
stdout+stderr.

**Done when:** you say *"install pandas and plot me a sine wave"* and get a PNG back.

**Concepts:** `docker` Python SDK, `container.exec_run`, bind mounts, container
lifecycle, output truncation (and the `read_tool_output` tool that pages past it),
the exit-128 recreate-and-retry recovery.

**Security reality check:** the agent has root in that container. Before you expose
this to anyone but yourself: ICC off, no host mounts outside the workspace, CPU/memory
limits, and a hard container lifespan cap.

---

### Phase 6: Control — stop, inject, compress
Three separate features that all touch the loop:
1. **Stop** — `threading.Event` + a Redis pubsub listener so any worker can cancel a
   generation running on any other worker.
2. **Live injection** — `POST /api/inject_message`, the `inject_messages:{chat_id}`
   Redis list, `hidden=1` segmentation, the `stream_reset` SSE event.
3. **Memory compression** — the `hidden` column, the `compress_memory` tool, the
   context-usage warning injected into the system prompt.

**Done when:** you can interrupt the agent mid-tool-call and it stops cleanly with no
orphaned DB rows; and you can type a follow-up mid-generation and it pivots.

**Concepts:** cooperative cancellation, cross-process signalling, why you must never
save a partial response.

---

### Phase 7: Multi-key rotation
Build `GlobalKeyManager`. Per-(key, model) blocks in Redis. Classify `RPM` / `RPD` /
`INVALID` / `OVERLOAD`. Handle the client rebuild on key switch. Add the
Pacific-midnight reset thread.

**Done when:** with 2 keys configured, you can exhaust key 1 and the conversation
continues on key 2 without losing history.

**Concepts:** rate-limit error parsing, thread-safe singletons, Redis as shared
mutable state, why the client rebuild is mandatory.

> Reference: `key_manager.py` (526 lines — read it whole, it is the most
> self-contained good file in the repo).

---

## Stage C — Capability and production

### Phase 8: The rest of the tool suite
Pick by what you actually want: `generate_image`, `make_presentation` (pptx from AI
images), `analyze_youtube_video`, `send_self_email`, `manage_files`,
`logs_and_preferences` (persistent memory), `schedule_task` + the background scheduler
thread.

**Concepts:** structured JSON output, `asyncio.gather` for concurrent generation,
SMTP, a daemon thread polling SQLite for due tasks.

---

### Phase 9: Production deploy
Gunicorn (`gthread`, long timeout), Nginx with `proxy_buffering off`, systemd units,
Let's Encrypt. Then `repo_control`: deploy containers reachable at
`https://<name>.yourdomain/` via a wildcard cert, with file snapshots into SQLite.

**Concepts:** WSGI vs dev server, reverse proxying SSE, wildcard DNS + certs,
subdomain-to-container routing, why dependency install and server start must be
separate calls (OOM).

---

### Phase 10: Generative UI — `request_user_interaction`
The agent writes a self-contained HTML/CSS/JS widget, it renders in the chat feed,
execution **pauses**, the widget calls `window.stellar.finish(data)`, the answer goes
back into the loop.

**Done when:** you play tic-tac-toe against it. No game engine — the model *is* the
engine.

**Concepts:** blocking a tool call on a user event, sandboxed HTML injection, DOM
scoping between widgets.

This is the highest fun-per-line feature in the entire repo. Do not skip it.

---

## Stage D — Advanced (optional; each is a project on its own)

### Phase 11: Terminals
Web terminal first (PTY over SSE + xterm.js, with a Redis pubsub relay so it works
across Gunicorn workers), then the Paramiko SSH gateway with the device-code auth flow.

### Phase 12: The orchestrator
The 7-agent pipeline, `memory.db`, the context/outbox protocol, the watchdog, PR
automation. Only attempt this once Phases 1-9 are yours.

### Phase 13: Polish
`sentinel_healer.py` self-healing, PWA + Web Push (VAPID), the pytest suite, the
admin panel.

---

## Realistic effort

| Stage | Phases | Learning at a steady pace |
|-------|--------|---------------------------|
| A | 0-2 | ~1 week |
| B | 3-7 | ~3 weeks |
| C | 8-10 | ~3 weeks |
| D | 11-13 | open-ended |

**Stop-and-be-proud point: end of Phase 6.** At that point you own a
tool-calling AI agent with a sandboxed shell, resumable streaming, and clean
interrupts. That is genuinely more than most "AI app" projects ever ship. Everything
after is breadth, not depth.

---

## How to use the reference repo

Do **not** read `app.py` top to bottom. It is 8,561 lines and was written by seven AI
agents over hundreds of commits; it has repetition and dead ends.

Instead, per phase: build your own version first, get stuck, *then* open the reference
at the line numbers above and compare. You will learn ten times more from
"why did they do it that way instead of mine?" than from transcription.
