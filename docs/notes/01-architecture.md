# Stellar — How It Actually Works

Reverse-engineered from `github.com/N1kky-wed/Stellar` (commit 8472ce3).
Real size: ~15,000 lines of hand-written Python + ~9,000 lines of frontend JS/HTML.

---

## The one-sentence version

Stellar is a Flask app that gives an LLM a **bash shell inside a Docker container**,
streams the resulting conversation to the browser over SSE, and persists every
message, tool call, and container filesystem to SQLite so the whole thing survives
restarts.

Everything else is scaffolding around that idea.

---

## The 8 layers

| # | Layer | Files | What it does |
|---|-------|-------|--------------|
| 1 | Edge | `deploy/nginx_stellar.conf` | TLS, wildcard `*.domain` routing, `proxy_buffering off` (mandatory for SSE) |
| 2 | App server | `deploy/gunicorn_stellar.service` | Gunicorn, `gthread` worker class, 4 workers x 25 threads, 3600s timeout |
| 3 | Web / API | `app.py` (8.5k lines, ~70 routes) | Auth, chats, SSE streams, admin, SSH device-codes, PTY terminal |
| 4 | Agent loop | `gemini_generate()` at `app.py:2147` | The brain. Manual function-calling loop. ~800 lines |
| 5 | Tools | `agent_tools.py` (16 tools, 2.4k lines) | What the agent can *do* |
| 6 | Sandbox | `dockerfiles/`, `dockersetup.py` | Per-user Docker networks, ICC disabled, one container per chat |
| 7 | Terminal | `ssh_gateway.py` (2.4k lines) | Paramiko SSH server + Rich TUI + docker exec PTY |
| 8 | Autonomy | `orchestrator/` (3.2k lines), `sentinel_healer.py` | 7 AI engineers that write PRs against the repo itself |

**Two datastores, two jobs:**

- **SQLite** (WAL mode, `busy_timeout=5000`) — durable truth: users, chats, messages,
  tool_calls, repo snapshots, scheduled tasks, agent memory.
- **Redis** — everything ephemeral and cross-process: query args, stream buffers,
  cancellation pubsub, key-block state, injection queues, SSH codes, push subscriptions.

The Redis/SQLite split exists because Gunicorn runs **4 separate OS processes**.
In-memory Python state is invisible across workers. Anything two workers must agree
on has to live in Redis.

---

## Mechanism 1: The two-phase streaming pipeline

This is the part everyone gets wrong first. A naive design does
`POST /chat` then streams the response. That breaks the moment the user refreshes,
loses signal, or switches devices.

Stellar splits it into register-then-attach:

```
Browser                      Flask                         Redis
   |                           |                             |
   |-- POST /register_query -->|                             |
   |                           |-- SETEX query_args:{id} --->|  (24h TTL)
   |<-- { query_id } ----------|                             |
   |                           |                             |
   |-- GET /refine_stream?id ->|                             |
   |                           |-- EXISTS stream_started:{id}|
   |                           |     not started?            |
   |                           |     -> spawn worker thread  |
   |                           |        running gemini_generate
   |                           |        which LPUSHes chunks |
   |                           |                             |
   |<== data: {...}\n\n =======|<-- consume chunks ----------|
   |<== data: {...}\n\n =======|                             |
```

Why this is good:

- **Reconnect-safe.** Refresh the page, re-GET the same `query_id`, attach to the
  same Redis buffer. The LLM never knew you left.
- **Worker-agnostic.** The SSE connection can land on worker #3 while the generation
  runs on worker #1. Redis is the meeting point.
- **Multi-device.** `redis.publish("user_events:{user_id}", ...)` tells your other
  tabs that a stream started elsewhere.

Read these in order: `register_query` (`app.py:3790`), `refine_stream` (`app.py:3863`),
`stream_consumer` (`app.py:4296`), `background_thread_runner` (`app.py:4335`).

---

## Mechanism 2: The agent loop

`gemini_generate()`, `app.py:2147`. This is the actual AI agent. Critically, it
**disables** the SDK's automatic function calling and drives the loop by hand:

```python
chat_config = types.GenerateContentConfig(
    tools=tools_config,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    ...
)
chat = client.chats.create(model=model_id, config=chat_config)

while True:
    r = chat.send_message(message_to_send)
    parts = r.candidates[0].content.parts

    for p in parts:
        if p.text:
            yield {'result': p.text}                 # stream text to user

    function_calls = [p.function_call for p in parts if p.function_call]

    if not function_calls:
        break                                        # turn is over
    else:
        for fc in function_calls:
            yield {'status': fc.args.get('status')}  # live progress line
            fn = getattr(agent_tools, fc.name)
            result = fn(**dict(fc.args))
            function_responses.append(result)
        message_to_send = function_responses          # feed back, loop again
```

**Why manual instead of automatic?** Because the loop needs four things the SDK's
auto mode will not give you:

1. **Streaming status lines.** Every tool takes a `status` parameter that the *model*
   fills in ("Installing pandas..."), yielded to the UI *before* the tool runs.
2. **Cancellation.** `cancel_event.is_set()` is checked before every LLM call and
   before every tool call. On stop, nothing partial is written to the DB.
3. **Live injection.** At the `not function_calls` break point it drains
   `inject_messages:{chat_id}` from Redis. If you typed a follow-up mid-generation,
   the partial reply is saved as `hidden=1`, a `stream_reset` event is sent, and the
   loop `continue`s with your new message instead of breaking.
4. **Key rotation mid-conversation.** See Mechanism 4.

**Tool schemas are free.** `google-genai` reads the Python function signature and
docstring to build the function-calling schema. `available_tools` is literally just
a list of function objects (`agent_tools.py:2337`). Write the function, write a good
docstring, and the model can call it. This is why the tool layer is only 2.4k lines
for 16 capable tools.

---

## Mechanism 3: The sandbox

The agent runs as `root` — but inside a container it cannot escape.

- One container per **user per chat**: `stellar-lab-u<uid>-c<cid>`
- Host dir `sandbox_runs/lab_workspace_u<uid>_c<cid>/` bind-mounted at `/lab`,
  so the workspace survives container death
- One Docker network per user, `stellar_net_<user_id>`, created with **ICC disabled**
  so containers cannot talk to each other
- `repo_control` deployments get a public subdomain via the Nginx wildcard cert, and
  their entire file tree is JSON-snapshotted into `repo_history.files_snapshot`
  before any stop — so `restart` can restore a dead container's code

Failure handling worth stealing: if a container's mount namespace breaks (exit code
128), `lab_execute` recreates the container and **transparently retries** the command.

---

## Mechanism 4: Key rotation (the operational reality)

Free-tier Gemini keys rate-limit constantly. `GlobalKeyManager` (`key_manager.py`)
tracks blocks per **(key, model)** pair in Redis, classified by reason:

| Reason | Recovers | Loop behavior |
|--------|----------|---------------|
| `RPM` | ~60s | Stay in pool; skip now, come back later |
| `RPD` | Pacific midnight | Drop from pool for this whole call |
| `INVALID` / `403` | never | Drop globally, across all models |
| `OVERLOAD` | ~10min | Skip; trigger a model fallback instead |

Strategy is "earliest-available": always rescan from index 0, so K1 is used
exclusively until it blocks, then K2, and the instant K1's 60s window expires you
are back on K1.

The subtle part: a `genai.Client` is **bound to its API key**. Switching keys
mid-conversation means rebuilding the entire chat object with the accumulated history
(`app.py:2455-2465`) — and re-uploading any attached files, because file URIs are
also key-scoped.

---

## Mechanism 5: Context management

A 1M-token window still fills up. Two systems handle it:

- **The `hidden` column** on `messages` and `tool_calls`. Hidden rows are excluded
  from the UI (`get_conversation_history(for_ui=True)` filters `hidden = 0`) but are
  still fed to the LLM. Used for both compression *and* interrupted partial replies.
- **The `compress_memory` tool.** When `get_refinement_prompt()` detects high context
  use, it injects a warning into the system prompt. The model then calls
  `compress_memory` itself, which sets `hidden=1` on old rows and writes a structured
  `[COMPRESSED MEMORY STATE]` document so it does not forget what it was doing.

The agent manages its own memory. That is an unusually good design and worth copying.

---

## Mechanism 6: The self-improving orchestrator

`orchestrator/` is a separate daemon, not part of the web app. Seven agent personas
(`agents/*.md`) run **sequentially** — each triggers the next on completion:

`bolt` (perf) -> `sentinel` (security) -> `palette` (UI) -> `newton` (tests)
-> `lucios` (logging) -> `proton` (docs), plus `mercury` (CI repair, event-triggered)

Each run: restart a clean container -> clone repo -> checkout `agent/<id>/<ts>` ->
inject `AGENTS.md` + `memory_context.md` -> run the `agy` CLI -> 45-minute watchdog ->
read `memory_outbox.json` back into `memory.db` -> open a PR -> GitHub Actions
auto-merges -> orchestrator pulls and restarts affected systemd services.

Agents collaborate through `memory.db`: **memories** (observation / decision /
outcome / warning), **messages** (group + DMs), **tasks** (open / fix_submitted /
resolved), and **facts** (constraint / convention / architecture / bug_pattern,
with supersession).

This is the most ambitious part of the repo and the **last** thing you should rebuild.
