# Phase 1: Infrastructure hardening

Built 2026-10-08, after N1kky's roadmap card "Infrastructure Hardening" (repo_control,
docker-persistence, model-routing). Rishi's decisions: apps live 90 hours after their
last visit and then sleep and wake; git history; four routing tiers; a Projects panel;
automated tests here and browser checks on the live site only.

## What was researched first

| Source | Idea taken |
|---|---|
| Fly.io autostop/autostart | stop idle apps, wake them from the proxy on the next request |
| E2B persistence | a paused sandbox keeps its files indefinitely; resume is fast |
| GitHub Codespaces | "stopped" and "deleted" are different; the workspace survives rebuilds |
| RouteLLM (LMSYS) | send each query to the cheapest model that can do it; calibrate on real traffic |
| OpenRouter auto router | classify by task type, stick to a model within a session, always fall back |
| Gemini thinking docs | HIGH thinking for advanced coding, maths and multi-step planning |

Free-tier check (6 tiny calls, 2026-10-08): gemini-3.8-flash (tools + HIGH thinking),
3.5-flash, 3.5-flash-lite and gemma-4-31b-it (tools) all answer; 3.1-pro-preview has no
free quota and is never routed to.

## 1. Projects that live on

**Before:** a deployment was a container running `tail -f /dev/null`. Its server ran only
while someone had started it with `execute`, the command was never stored, and the
container had no restart policy, so a reboot left every app down. "Snapshots" copied text
files under 500 KB into SQLite: no binaries, no history, no rollback.

**Now:**

- **Saved start command.** `repo_control(action='serve', command=...)` stores the command
  in `repo_history.start_command` and writes `/app/.stellar/start.sh`. Every project
  container's PID 1 is a small boot script (`PROJECT_BOOT`) that runs `start.sh` in its own
  session, then idles. So starting the container is all it takes to bring the server back.
- **Restart policy `unless-stopped`.** Docker restarts running apps after a daemon or host
  restart; an app Stellar stopped on purpose (sleep, or the user) stays stopped.
- **The 90-hour lifecycle.** Visits through the proxy are recorded in Redis (at most every
  30 s per app per worker, so a page with forty assets is not forty writes). The reaper
  copies the time into `last_active_at` and puts a project to sleep after 90 hours with
  no visit or edit: checkpoint, then stop; files kept. `stopped_by` says why: `idle` and
  `cap` wake on the next visit, `user` stays stopped. (SQLite cannot change the old CHECK
  constraint on `status` without rebuilding the table, so "sleeping" is `stopped` plus a
  reason, which keeps the live database's upgrade a simple ALTER TABLE.)
- **Wake on visit.** A request to a sleeping app starts a background waker (one per
  project across all workers, by a Redis lock) and shows a page that reloads every 3 s,
  sent as 503 with `Retry-After` so crawlers and API clients come back too. A server that
  died while its container runs is relaunched once per two minutes (self-healing).
- **The cap without refusals.** Starting a sixth app puts the user's least recently used
  one to sleep instead of refusing.
- **Reconcile.** The reaper also brings back a project marked running whose container is
  gone or stopped (Docker Desktop restarted, a container removed by hand).
- **Git history, inside the container.** Every create, execute, serve, job and sleep makes
  a commit. `history` lists them; `restore` checkpoints the current state first, then
  `git restore --source=<sha> --staged --worktree -- .` and commits on top, so nothing is
  ever lost. Git runs in the project's own container, never on the host: a cloned
  repository can carry hooks and settings (core.fsmonitor, pager) that run programs, and
  inside the sandbox they can only touch the sandbox. History of a sleeping project is read
  with a throwaway container: no network, folder mounted read-only.
- **Background jobs.** `execute(background=True)` runs the command detached with its output
  in `/app/.stellar/logs/<job>.log` and its exit code beside it, killed after 6 hours, and
  recorded in `project_jobs`. `action='job'` reports status and the log tail. A project
  with a running job is never put to sleep.
- **Continuity across chats.** Every turn's system prompt lists the user's projects with
  their state and start command (`project_digest`), so "update my portfolio site" in a new
  chat works on the existing app instead of deploying a second one.

**Where this goes beyond Nikky's version:** his apps lived a fixed 90 hours and then
needed restarting by hand; his snapshots were file copies; his containers ran as root with
no limits and `unless-stopped` but no saved start command, so a restart brought back an
empty container. Here apps never die on a timer, come back with their server, keep a
real history with rollback, and are woken by visitors.

## 2. Model routing (routing.py)

| Tier | Model | Thinking | For |
|---|---|---|---|
| Swift | gemini-3.5-flash-lite | MINIMAL | greetings, short simple questions |
| Core | gemini-3-flash-preview | LOW | normal work (the old behaviour) |
| Obsidian | gemini-3.8-flash | HIGH | debugging, tracebacks, maths, design, full builds |
| Lunarity | gemma-4-31b-it | (none) | long reading and summarising; overflow when Flash is spent |

- Rules, not a model: classifying with a model call would spend quota to decide how to
  spend quota. Every decision is saved on the reply (`route_tier`, `route_model`,
  `route_reason`), shown as a small label under it, and logged, so the rules can be tuned
  from real use (the RouteLLM lesson: calibrate on your own traffic).
- Sticky: a short "yes, do it" stays on the tier of the turn before.
- Quota-aware: models with no key left today are skipped; a tier with nothing left falls
  along a fixed order (Obsidian -> Core -> Lunarity -> Swift) and the reason says so.
- Gemma is skipped for chats over 100k tokens (its window is far smaller than Flash's).
- Mid-turn recovery now walks the turn's own chain (tier models, then the old fallbacks)
  instead of always jumping to one fixed fallback model.
- A model picked by hand in Settings is used as it is ("Manual").
- Why it matters on free keys: Flash allows about 15 requests a key a day, Flash-Lite
  about 500 and Gemma about 1,500. Easy turns no longer spend Flash quota.

## 3. Hardening

- Rate limits per account: 20 messages a minute and 1,500 a day, 30 follow-ups a minute,
  60 uploads per 10 minutes (chat and terminal), 1,800 terminal inputs a minute, 30
  project actions a minute.

## 4. Projects panel (sidebar: Deploy)

Each project with its state (running, asleep, stopped, starting), address, last visit and
start command; Wake or Sleep; History with Restore (asks first); Delete (asks first;
removes container, files and history, and retires the address so nobody else can take it).

## Upgrading the live server

`git pull` and restart Stellar. The schema upgrades itself (version 16: three columns on
`messages`, three on `repo_history`, a new `project_jobs` table). Existing app containers
keep running as they are; each is replaced by the new kind (boot script, restart policy)
the next time it is restarted, woken or redeployed. Their files are kept.
