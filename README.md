# Stellar

A self-hosted AI agent you chat with. It answers with Google's Gemini models
and can act, not only talk: it runs code in a Linux sandbox of its own,
searches and reads the web, makes images and slide decks, watches YouTube
videos, plays chess, schedules tasks, remembers things you tell it, and
deploys web apps to their own addresses.

This is a from-scratch rebuild of [N1kky-wed/Stellar](https://github.com/N1kky-wed/Stellar),
written as a learning project. `notes/` explains how each part works and why
it is built the way it is.

## What you need

- **Python 3.12** and **[uv](https://docs.astral.sh/uv/)** (or plain pip)
- **Docker** (Docker Desktop on Windows and macOS, Docker Engine on Linux)
  for the sandbox, Redis and deployed apps
- **A Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey).
  Several keys work better than one: Stellar moves to the next when one
  reaches its limit.

## Setup

Commands are for Windows; on Linux and macOS use `.venv/bin/` in place of
`.venv/Scripts/`.

1. **Install the exact package versions the tests run against:**

   ```bash
   uv venv --python 3.12 .venv
   uv pip sync requirements.lock
   ```

   Without uv: `python -m venv .venv` then
   `.venv/Scripts/pip install -r requirements.lock`.

2. **Create your settings file** and fill in at least `FLASK_SECRET_KEY` and
   `PRIMARY_API_KEY`:

   ```bash
   copy keys.env.example keys.env
   ```

   The file explains every setting. Generate the secret key with
   `python -c "import secrets; print(secrets.token_hex(32))"`.

3. **Start Docker**, then build the sandbox image and start Redis with a
   password (it writes `REDIS_URL` into keys.env for you):

   ```bash
   .venv/Scripts/python.exe docker_setup.py
   .venv/Scripts/python.exe docker_setup.py --secure-redis
   ```

4. **Check everything**:

   ```bash
   .venv/Scripts/python.exe verify_env.py
   ```

   Each check says PASS, SKIP (optional and not set up) or FAIL with what to
   do about it.

5. **Run it**:

   ```bash
   .venv/Scripts/python.exe app.py
   ```

   and open http://localhost:5000.

6. **Make yourself the administrator.** Stellar is invite-only: new accounts
   wait until an administrator approves them, and signing up never makes
   anyone an administrator, not even the first account. Either sign up and
   then run

   ```bash
   .venv/Scripts/flask.exe --app app:create_app make-admin you@example.com
   ```

   or put your address in `ADMIN_EMAILS` in keys.env and sign in with Google.

## Settings

All live in `keys.env`; `keys.env.example` documents each one.

| Setting | Needed for |
|---|---|
| `FLASK_SECRET_KEY` | Required. Signs sign-in cookies. |
| `PRIMARY_API_KEY`, `BACKUP_API_KEY_1`... | Required. Gemini keys, used in turn. |
| `REDIS_URL` | Required. Written by `docker_setup.py --secure-redis`. |
| `ADMIN_EMAILS` | Administrators by email, confirmed by Google sign-in. |
| `TAVILY_API_KEY` | Web search ([tavily.com](https://tavily.com), free tier). |
| `YOUTUBE_API_KEY` | YouTube search with view counts. Optional; Tavily also works. |
| `EMAIL_USER`, `EMAIL_PASS` | Sending email to your own address (a Gmail app password). |
| `FIREBASE_*` | "Continue with Google". See `notes/10-google-sign-in.md`. |
| `STELLAR_DOMAIN`, `SESSION_COOKIE_SECURE` | Production only. See the deploy guide. |

Anything optional that is not set simply turns that feature off; the app
says so in plain words rather than failing.

## Accounts

The **Admin** page (account menu, administrators only) approves, revokes and
removes accounts, shows whether each API key is working (by fingerprint,
never the key), and lists recent changes. The same is available on the
server's command line:

```bash
.venv/Scripts/flask.exe --app app:create_app list-users
.venv/Scripts/flask.exe --app app:create_app approve-user someone@example.com
.venv/Scripts/flask.exe --app app:create_app revoke-user someone@example.com
.venv/Scripts/flask.exe --app app:create_app make-admin someone@example.com
```

## Running on a server

`deploy/deploy_guide.md` walks through an Ubuntu server step by step: nginx
with a wildcard certificate (for deployed apps), four Gunicorn workers under
systemd, Redis with a password, firewall rules for the sandbox, the SSH
gateway, daily backups, and upgrading.

## Tests

```bash
.venv/Scripts/python.exe smoke_test.py          # offline: no API calls, no quota spent
.venv/Scripts/python.exe smoke_test.py --live   # also two real Gemini turns
```

The suite needs Redis, and uses its own throwaway database, Redis database
15, temp folders and container names, so it never touches your data. If
Redis is not reachable it says so and exits with code 2. Sections that need
Docker are skipped, and listed at the end, when Docker is not running; set
`STELLAR_REQUIRE_DOCKER=1` to treat that as a failure.

Behaviour that only shows with several server processes is checked by
`deploy/four_worker_test.sh`, which runs four Gunicorn workers (Linux or WSL).
GitHub Actions runs the suite on every push (`.github/workflows/ci.yml`).

## How it fits together

| Part | Where |
|---|---|
| The web app: routes, the agent loop, every tool | `app.py` |
| Database tables | `schema.sql` (applied on every start) |
| The page | `templates/`, `static/main.js`, `static/main.css` |
| Chess board and engine | `chess_ui.py`, `chess_engine.py` (`setup_engine.py` downloads Stockfish, optional) |
| SSH access to a chat's sandbox | `ssh_gateway.py` |
| The sandbox image | `dockerfiles/Dockerfile.lab` |
| Server configuration | `deploy/` |
| How and why it is built this way | `notes/` |

A reply is two requests: the page registers a message, then attaches to a
stream of events (tokens, tool calls, widgets) kept in Redis. A reply
therefore survives a page reload, and any server process can serve it.

## Limits

- Each user: at most 3 sandboxes and 5 deployed apps running at once, and
  2 GB of files. One file may not pass 1 GB.
- A sandbox stops after 30 minutes unused and is removed after 14 days
  stopped; its files stay until the chat is deleted.
- Deleting a chat deletes its messages, files and sandbox. Apps deployed
  from it keep running.

## Security

Code the model writes runs in Docker containers with dropped privileges,
resource caps and their own network, and on a Linux server firewall rules
stop it reaching the host or the private network. The host never follows a
link a sandbox made. The page loads only its own scripts, never renders
model output as HTML, and runs model-made widgets in sandboxed frames that
cannot reach the network. State-changing requests need a CSRF token, and
API keys are kept out of logs and replies.

Deployed apps share the site's domain, so host only apps you would run
yourself. `notes/07-audit.md` lists this and the other known gaps.

## Credits

Chess pieces: the Cburnett set by Colin M.L. Burnett, CC BY-SA 3.0.
