# Stellar (N1kky) vs new-stellar: a deep comparison, and the plan to pass it

Written 2026-10-07. Compared from a fresh read-only clone of `github.com/N1kky-wed/Stellar`
(HEAD `8472ce3`, last push 2026-07-31) and this repo at `832b3b5`. File and line references:
theirs are in the reference repo, ours in this one.

## 1. At a glance

|                     | Stellar (N1kky)                                   | new-stellar (ours)                                   |
|---------------------|---------------------------------------------------|------------------------------------------------------|
| Size                | ~29k lines Python; `app.py` 8,561; `main.js` 7,733; `main.css` 6,333 | `app.py` 10,807; `main.js` 2,572; smoke suite 3,947 |
| History             | 774 commits, Jan–Jul 2026, most of June written by its own agents | 83 commits, Sep–Oct 2026, built phase by phase      |
| Shape               | `app.py` + `agent_tools.py` + `key_manager.py` + orchestrator + 3 daemons | everything in `app.py`; chess, SSH, setup split out |
| Tests               | 30 pytest files, 411 tests, mocked Docker/Redis   | one script, ~547 checks, real Redis + Docker, CI    |
| Users               | Google sign-in only, waitlist                      | password + Google, approval, admin                   |

**One-line verdict.** Stellar has *more features* (about 15 that we lack). new-stellar has the
*better foundation*: security, correctness, resilience and isolation are clearly ahead. Getting
to "Stellar or better" means adding its features onto our base, not copying its base.

## 2. Tech stack

| Layer          | Stellar                                                         | new-stellar                                               | Better |
|----------------|-----------------------------------------------------------------|-----------------------------------------------------------|--------|
| Web            | Flask 3.1, Flask-Session (sessions in Redis db1)                | Flask 3.1, signed-cookie sessions with epoch revoke       | tie |
| Server         | Gunicorn gthread 4×25, unix socket, nginx                       | same                                                      | tie |
| SDK            | google-genai **and** the deprecated google-generativeai         | google-genai only                                         | ours |
| DB             | SQLite WAL + 2 more SQLite DBs for the orchestrator             | SQLite WAL, versioned schema, column-by-column migration  | ours |
| Cache/queue    | Redis: pub/sub stream fan-out, sessions, key blocks, push subs  | Redis: list-based replayable stream, claims, blocks, limits | tie |
| Sandboxes      | 9 images: lab (with node + gemini-cli), py with ML libs, C/C++/Go/Java/Node/PHP/Ruby/Rust, repo host, react-native | 1 image (`stellar-lab`) for chats and apps | **theirs** |
| Front end      | plain JS, marked.js, turndown, highlight.js, Tailwind CDN, PWA  | plain JS, own markdown→DOM renderer, xterm.js, self-hosted fonts, no CDN | ours (safer), theirs (richer) |
| CI             | py_compile + node --check + prettier + pytest; auto-merge bots  | smoke suite with real Redis + Docker                       | theirs (lint), ours (realism) |
| Deploy         | systemd: app, SSH (as root), orchestrator, "angel" tracer; wildcard TLS | systemd: app, SSH, backups (daily), sandbox egress firewall; four-worker test | ours |
| Extras         | Twilio, Playwright installed but unused                         | lock file for the whole tree                              | ours |

## 3. Architecture

**Both** use the same two-step reply: register a query, then open an SSE stream that a
background thread feeds through Redis, so a dropped connection resumes.

| Concern               | Stellar                                                     | new-stellar |
|-----------------------|-------------------------------------------------------------|-------------|
| Stream transport      | Redis list history + pub/sub push (low latency)             | Redis list, polled every 50 ms (simpler; costs a thread per stream) |
| Resume                | replay history then subscribe                               | `Last-Event-ID` resume, orphan salvage after 75 s, one-time finalise |
| Agent loop            | manual loop, unlimited rounds, MAX_TOKENS continuation check | manual loop, 8 rounds (+4 for follow-ups), per-error recovery budgets |
| Tool calls            | thread pool, tool-specific timeouts                          | one interruptible thread per tool; Stop never waits |
| Context               | 1M budget, compression at 95%, warning at 75%                | 1M estimate, notice at 70%, compress_memory, 12k-char model view |
| Cross-worker          | per-worker cancel dicts + pub/sub; monitors run per worker  | Redis claims with Lua, heartbeat, supersede, one-shell-per-chat |
| Multi-agent           | **Orchestrator**: 7 autonomous coding agents that clone Stellar, edit it, open PRs, auto-merge and restart the live server | none |
| Self-healing          | Sentinel (Gemini patches deployed apps) – **disabled**      | none |

## 4. Models and keys

| | Stellar | new-stellar |
|---|---|---|
| Provider | Gemini only (orchestrator also drives an external CLI) | Gemini only |
| Personas | 4 (Obsidian, Crimson, Lunarity, Emerald) mapped to models, tools gated by model | one Stellar, model picker in Settings and the composer |
| Rotation | Redis blocks per key/model, RPM + RPD, monkeypatched SDK counts every call, mid-conversation key switch re-uploads files | Redis blocks per key/model, RPM + RPD, earliest-available key, error classifier with 7 kinds |
| Limits | per model | **hard-coded free-tier limits** (4 RPM / 15 RPD on flash): safe for "no paid Gemini", but a paid key would be capped |
| When all fail | a diagnostic report from the smallest model | friendly error + waits 5/15/30 s |
| Safety settings | `BLOCK_NONE` on every category | defaults |

## 5. Tools, side by side

| Capability | Stellar tool | new-stellar tool | Gap |
|---|---|---|---|
| Web search | `web_search` (Tavily search + extract + crawl + map, image URL checks) | `web_search` (Tavily search) + `fetch_url` (SSRF-safe reader) | theirs has crawl/map/extract |
| Run code | `lab_execute` (persistent root container, no limits) | `lab_execute` (persistent, capped, dropped caps, quota) | ours safer |
| Multi-language run | `/api/run_code`, 10 languages with their own images | through `lab_execute` only (Python image) | **theirs** |
| Deploy apps | `repo_control` incl. mobile (react-native) env, resume from history, auto-relaunch on restart | `repo_control` (no mobile, apps stay down after a restart) | **theirs** |
| Files | `manage_files` read/move/project between uploads, lab, repo | `manage_files` list + share | theirs moves between envs |
| Images | `generate_image` up to 4K, 14 references | `generate_image` 2K, 4 references, style presets | tie (both need image quota) |
| Slides | `make_presentation` (every slide an AI image) + `regenerate_presentation_slide` | `make_presentation` (editable themes, optional images) | theirs regenerates slides |
| YouTube | search + analyse | search + analyse | tie |
| Email | `send_self_email` | `send_self_email` (verified address, 20/day) | tie |
| Schedule | `schedule_task` incl. **edit** | `schedule_task` (no edit), timezone-aware | theirs can edit |
| Memory | `logs_and_preferences` (100 entries) | `remember` (40 notes, blocked after untrusted content) | ours safer |
| Long outputs | `read_tool_output` | `read_tool_output` | tie |
| Compression | `compress_memory` (archives to /lab) | `compress_memory` | tie |
| Widgets | `request_user_interaction` (rendered in page) | same, in a no-network sandboxed iframe | ours safer |
| Skills library | `obtain_talent` (loads expert playbooks: frontend, games, mobile, generative AI…) | – | **theirs** |
| Bug reports | `report_process_issue` (emails the developer) | – | theirs |
| 3D explainers | `/api/visualize` (interactive HTML explainer) | – | **theirs** |
| Time | – | `get_current_time` | ours |
| Chess | – | `chess_play`, `chess_move` (engine, clock, grading) | ours |

Count: Stellar 16 model tools + run_code + visualize; new-stellar 17 model tools.

## 6. Features outside the chat

| Feature | Stellar | new-stellar |
|---|---|---|
| Sign-in | Google (Firebase) only; first user becomes admin | password + Google; admin only by config or CLI |
| Admin | approve/revoke, key dashboard (live SSE), **impersonate any user**, orchestrator hub | approve/revoke/verify/delete, end sessions, key health, audit log |
| Bring your own API key | yes, Fernet-encrypted per user | no |
| PWA + push notifications | yes (manifest, service worker, VAPID) | no (a stale worker is removed) |
| Multi-device live sync | yes (user events over SSE) | no |
| Chat tools | edit, delete, delete-after, search, temporary chats, token count, live app previews, presentation carousel | copy only |
| Terminal | web terminal + Rich SSH TUI with a deployments menu; gateway runs as root, shared secret has a hard-coded default | web terminal (xterm, one shell per chat, uploads, full screen) + SSH with password or device code |
| Deployments UI | listed in the SSH TUI; previews in chat | none yet (sidebar shortcuts only) |
| Telegram bot, issue resolver | yes (admin alerts, auto-fix with a CLI) | no |
| Analytics | 8x tracking script on every page | none |
| Landing pages | – | `/welcome` (WebGL ribbon), `/cosmos` (black hole), Vercel exports |
| Backups | – | daily SQLite online backup, 14 kept |

## 7. Security and reliability (where we are clearly ahead)

| | Stellar | new-stellar |
|---|---|---|
| CSRF | **none** on any POST, including admin and impersonate | Origin + Sec-Fetch-Site + token on every non-GET |
| CSP | none site-wide | strict `script-src 'self'` on the app; sandbox CSP on files and widgets |
| System prompt | tells the model to suspend safety rules and act as a red-team "Angel"; `BLOCK_NONE` | short, normal instructions; memory framed as data |
| Generated files | `/download` and `/view` are **public by filename** | owner-only, HTML/SVG never rendered |
| Sandboxes | root, no CPU/memory limits, `unless-stopped`, host dirs bind-mounted | 2 GB / 2 CPU / 512 procs, dropped caps, no-new-privileges, 3 per user, idle stop, egress firewall |
| Self-modification | orchestrator agents edit and redeploy the live app with permission checks turned off | none |
| Known bugs | `kwargs` NameError in repo recovery, invalid "system" role, unbound `status_code`, orphan grace of 150 h | the audit fixed ~80 silent defects, each with a regression check |
| Rate limits | API keys and SSH only | sign-up, login, Google, email, streams, widgets, SSH (gaps: query, inject, uploads) |

These are the parts **not to copy**: the jailbreak prompt and `BLOCK_NONE`, missing CSRF/CSP,
public output files, unlimited root containers, the self-modifying orchestrator, the hard-coded
SSH secret, and third-party tracking.

## 8. Our own weak spots (from the inventory, to fix along the way)

1. Rotate the Gemini key left in the untracked `testapi.py`.
2. Prompt injection: after reading a web page the model can still deploy, schedule or run code
   with no confirmation. Only `remember` is blocked.
3. 8 tool rounds is tight for building a website.
4. Deployed apps stay down after a restart (no restart policy, no relaunch).
5. No rate limit on `/query`, `/inject`, uploads, terminal input.
6. Network pool runs out after user 1024; Docker's own pools after ~30 networks.
7. One 10.8k-line `app.py`; tests are one script; CI has no lint.
8. Free-tier limits hard-coded (fine while we use only free keys).

## 9. The plan: reach Stellar, then pass it

Ordered so each phase ends in something usable on the live site. No paid Gemini anywhere.

**Phase A – the base (safety first, 1–2 days)**
- Live server: finish `docker_access.sh` so the terminal opens `/lab`, not Stellar's `/app`.
- Rotate the `testapi.py` key. Rate-limit `/query`, `/inject`, uploads, terminal input.
- Confirmation step for risky tools after untrusted content (deploy, schedule, email).
- Split `app.py` into modules (tools, agent loop, proxy, terminal, auth) with no behaviour change;
  add ruff + `node --check` to CI. Stellar paid for this split late; we do it before it grows.

**Phase B – the pages the sidebar promises (Stellar has these in other forms)**
- Deploy page: list apps, open, stop/restart, redeploy, delete, snapshots, resume from history.
- Files page: browse uploads and `/lab`, preview, download, delete, move between chat and app.
- Tools page: what each tool does, on/off per user.
- Apps survive restarts: restart policy plus relaunch of the saved start command.

**Phase C – chat features Stellar has**
- Edit and resend a message, regenerate, delete-after, chat search, temporary chats, export.
- AI-written chat titles (one cheap flash-lite call, counted against the free quota).
- Token/context meter. Live app preview card in the chat when Stellar deploys.

**Phase D – more power in the agent**
- More rounds (24–40) with a step budget the user can see; a live plan card.
- Skills library like `obtain_talent`, written fresh: playbooks for websites, games, data
  analysis, slides, loaded on demand so the base prompt stays short.
- Tavily extract/crawl/map; schedule edit; regenerate one slide.
- Multi-language runs: add Node and Go/Java toolchains to the lab image or a second image,
  instead of nine images.
- Interactive explainers (Stellar's `/api/visualize`) through the existing widget sandbox.

**Phase E – reach and notifications**
- PWA install + push notifications (turn finished, widget waiting, scheduled task ran).
- Multi-device live sync of chats.
- Bring-your-own Gemini key per user, encrypted: users add their own free keys, which removes
  our quota ceiling without paid Gemini.

**Phase F – beyond Stellar (the "more optimal and advanced" part)**
- A second free provider as automatic fallback (e.g. Groq or OpenRouter free models behind one
  small provider interface), so Gemini overloads or quota never stop Stellar.
- Background tasks that keep running after the tab closes, with a notification when done.
- Parallel sub-agents for big jobs (research + build at once), each with its own sandbox, run
  through our existing claim and stream system. Stellar's orchestrator only worked on its own
  code; ours would work for users, with permission checks kept on.
- Documents: read PDF, Word and spreadsheets and answer from them; projects that group chats,
  files and memory.
- Push-based streams (pub/sub instead of 50 ms polling) when concurrency grows; Postgres only if
  SQLite's single writer becomes the limit.
- A separate domain for deployed apps, which closes the last cookie-sharing risk.

**Not planned:** the self-modifying orchestrator, the Sentinel auto-patcher in its old form,
the Telegram bot, 8x tracking, the red-team prompt.
