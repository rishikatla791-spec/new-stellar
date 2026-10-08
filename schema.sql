-- Stellar database schema.
--
-- Applied by init_db() in app.py on every start, and by `flask init-db`.
-- Safe to re-run: every statement uses IF NOT EXISTS, so running it against
-- an existing database only adds what is missing.
--
-- A table that already exists is left as it is, so a column added to it
-- here would never arrive. New columns on existing tables are added by
-- _ADDED_COLUMNS in
-- app.py, and every change here bumps SCHEMA_VERSION there, which is
-- stored in the database's user_version. The "(phase N)" notes below say
-- which build step a table arrived in.

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
-- Accounts sign in with an email and password, with Google (through
-- Firebase), or both once linked. username is always the email address.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,   -- email address
    -- '' for an account that signs in with Google instead.
    password_hash TEXT    NOT NULL,
    -- The Google account's id, once linked. Matched before the email,
    -- because an email address can change and this id never does.
    google_sub    TEXT,
    display_name  TEXT,
    -- Stellar is invite-only: a registered user cannot chat until an admin
    -- approves them. Enforced by the @require_approval decorator.
    is_approved   INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Every login cookie records the epoch it was issued under; raising it
    -- ends all of that account's sessions at once (revoke, Google link,
    -- "sign out everywhere"). Cookies are signed, not stored, so this
    -- counter is the only way to take one back.
    session_epoch  INTEGER NOT NULL DEFAULT 0,
    -- 1 once the address is proven: by Google sign-in, or by an admin.
    -- send_self_email only writes to proven addresses.
    email_verified INTEGER NOT NULL DEFAULT 0,
    approved_at    TEXT,
    -- Set when an admin removes access; NULL for an account still waiting.
    revoked_at     TEXT,
    last_login_at  TEXT,
    -- IANA zone from the browser, for scheduled tasks.
    timezone       TEXT,
    preferred_model TEXT,
    -- A password for the SSH gateway only, set in Settings. Separate from
    -- the sign-in password, which a Google account does not have. NULL
    -- means SSH sign-in goes through browser approval.
    ssh_password_hash TEXT
);

-- Who did what to which account. No foreign keys on purpose: the record
-- outlives the account it describes.
CREATE TABLE IF NOT EXISTS admin_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER,              -- NULL when done from the command line
    admin_name  TEXT,
    target_id   INTEGER,
    target_name TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- chats
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    -- NULL until the first message arrives, then auto-generated from it.
    name       TEXT,
    -- Reserved for temporary chats; nothing sets it yet.
    is_temp    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Bumped on every new message so the sidebar can sort by recency
    -- without an aggregate query over messages.
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    -- What the model last reported a request in this chat cost, in tokens.
    context_tokens INTEGER,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- messages
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    -- 'user' or 'stellar'. CHECK keeps typos out of the data: without it
    -- a stray 'assistant' silently vanishes from every filtered query.
    message_type    TEXT    NOT NULL CHECK (message_type IN ('user', 'stellar')),
    message_content TEXT    NOT NULL,

    -- Hidden messages are left out of the transcript the user reads.
    -- hidden_reason says why, and that decides whether the MODEL still sees
    -- them: 'summary' (a compression's state document) and 'interrupted'
    -- (older partial replies) are sent; 'archived' (what compression set
    -- aside) is not - sending those meant compression never shrank anything.
    hidden          INTEGER NOT NULL DEFAULT 0,
    hidden_reason   TEXT,

    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Order within the chat. A number rather than the timestamp, which has
    -- one-second resolution: a reply saved after a follow-up must still
    -- sort before it, and a position can sit between two others.
    position        REAL,

    -- Which routing tier and model answered this reply under Auto, and the
    -- router's reason (routing.py). NULL for user messages and older rows.
    route_tier      TEXT,
    route_model     TEXT,
    route_reason    TEXT,

    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- tool_calls  (phase 4)
-- ---------------------------------------------------------------------
-- One row per tool invocation. Without this the transcript is a lie: the
-- reply survives a refresh but every trace of the agent searching, fetching
-- or running something vanishes.
--
-- Also the substrate for two later features. read_tool_output pages through
-- `result` when a large output was truncated in context, and phase 6's
-- compression sets `hidden` on old rows to reclaim context.
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,

    -- The reply this call contributed to. NULL while the turn is still
    -- running, since the reply row does not exist until the model stops.
    message_id  INTEGER,

    tool_name   TEXT    NOT NULL,
    -- JSON object of the arguments the model chose.
    arguments   TEXT    NOT NULL,
    result      TEXT,
    duration_ms INTEGER,
    is_error    INTEGER NOT NULL DEFAULT 0,
    hidden      INTEGER NOT NULL DEFAULT 0,
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (chat_id)    REFERENCES chats(id)    ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- user_memory  (phase 8)
-- ---------------------------------------------------------------------
-- Facts the model chose to keep about a user, prepended to every turn.
-- One note per row rather than one growing blob, so a note can be
-- deleted by number and the oldest can be dropped at the cap.
CREATE TABLE IF NOT EXISTS user_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    note       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- scheduled_tasks  (phase 8)
-- ---------------------------------------------------------------------
-- A task is an instruction that becomes a normal turn in its chat when
-- run_at (UTC) passes. status/lock_id/claimed_at exist so several
-- workers can poll the same table: one UPDATE claims a row, and a claim
-- older than SCHEDULE_STALE_MINUTES is treated as abandoned.
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    chat_id       INTEGER NOT NULL,
    task_prompt   TEXT    NOT NULL,
    run_at        TEXT    NOT NULL,                 -- 'YYYY-MM-DD HH:MM:SS' UTC
    every_minutes INTEGER NOT NULL DEFAULT 0,       -- 0 = once
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'running', 'done', 'cancelled', 'failed')),
    lock_id       TEXT,
    claimed_at    TEXT,
    last_run      TEXT,
    runs          INTEGER NOT NULL DEFAULT 0,
    -- The turn this run started, so a task left 'running' can be checked
    -- against it: still alive, finished, or gone with its worker.
    query_id      TEXT,
    -- The user's IANA time zone when it was scheduled. A daily task keeps
    -- its local hour across daylight-saving changes.
    timezone      TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- repo_history  (phase 9)
-- ---------------------------------------------------------------------
-- Deployed repository and custom-stack applications managed by repo_control.
-- Each deployment runs in an isolated container and is reachable via a unique
-- subdomain. files_snapshot holds a JSON-serialized tree of files so that
-- a stopped or destroyed container can be restored with its full state.
CREATE TABLE IF NOT EXISTS repo_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    project_name    TEXT DEFAULT 'Untitled Project',
    process_id      TEXT NOT NULL UNIQUE,
    container_id    TEXT,
    subdomain       TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'stopped'
                    CHECK (status IN ('running', 'stopped', 'failed', 'exited', 'deploying')),
    deployment_url  TEXT,
    host_port       INTEGER,
    files_snapshot  TEXT,
    build_logs      TEXT,
    resource_usage  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_updated    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Phase 1: projects that live on. The saved command that starts the
    -- app's server (run whenever its container starts); the last visit or
    -- edit, for the 90-hour keep-alive; and why a stopped app is stopped -
    -- 'idle' or 'cap' sleep and wake on the next visit, 'user' stays put.
    start_command   TEXT,
    last_active_at  TEXT,
    stopped_by      TEXT,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Long commands run in a project's container in the background, so a build
-- that takes twenty minutes is not killed at the ten-minute tool limit. The
-- output goes to /app/.stellar/logs/<job_id>.log inside the project; this
-- row is how Stellar finds it again, in this turn or a later one.
CREATE TABLE IF NOT EXISTS project_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL UNIQUE,
    process_id  TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    command     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'done', 'failed', 'lost')),
    exit_code   INTEGER,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_project_jobs_process ON project_jobs (process_id, status);

-- ---------------------------------------------------------------------
-- widgets  (phase 2: generative UI)
--
-- Every widget the model puts on screen, so a reload shows it again
-- instead of a summary card. One row per frame: a game or a wizard that
-- updates itself in place stays one row, with live_id the interaction id
-- that is answering now. Live views (render_ui) never wait for an answer
-- and can be updated from a later turn by their id.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS widgets (
    id          TEXT PRIMARY KEY,
    live_id     TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    -- The reply it belongs to; NULL while the turn is still running.
    message_id  INTEGER,
    kind        TEXT NOT NULL CHECK (kind IN ('widget', 'chess', 'live')),
    title       TEXT NOT NULL DEFAULT '',
    html        TEXT NOT NULL,
    -- JSON object: a live view's state, or a game's latest position.
    state       TEXT,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'answered', 'closed', 'live')),
    result      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (chat_id)    REFERENCES chats(id)    ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_widgets_chat ON widgets (chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_widgets_live ON widgets (live_id);

-- ---------------------------------------------------------------------
-- attachments
-- ---------------------------------------------------------------------
-- Files a user attached to a message. Uploaded before the message is sent,
-- so message_id is NULL until the message they ride on exists; a pending
-- upload the user removes, or abandons by switching chat, is deleted.
--
-- The file itself lives on disk twice: a canonical copy under uploads/,
-- which is what the model and the browser are given, and a working copy
-- in the chat's sandbox at /lab/uploads/, which code the agent runs may
-- change freely without altering what the user actually sent.
CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    message_id    INTEGER,
    stored_name   TEXT    NOT NULL,
    original_name TEXT    NOT NULL,
    mime_type     TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (chat_id)    REFERENCES chats(id)    ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------
-- The hot query is "give me this chat's visible messages in order", run on
-- every page load and before every model call. Without this index SQLite
-- scans every message in the table and sorts the result.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);

CREATE INDEX IF NOT EXISTS idx_messages_chat_time
    ON messages (chat_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_messages_chat_position
    ON messages (chat_id, position);

-- Foreign-key columns used to find rows when their parent goes. Without
-- these, deleting a chat with a long tool history scanned the whole
-- tool_calls table under the write lock (5 s for 20,000 rows).
CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls (message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments (message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_user ON attachments (user_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_chat ON scheduled_tasks (chat_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user ON scheduled_tasks (user_id);

-- Sidebar: this user's chats, most recent first.
CREATE INDEX IF NOT EXISTS idx_chats_user_updated
    ON chats (user_id, updated_at DESC);

-- Rendering a transcript needs every tool call for a chat in order.
CREATE INDEX IF NOT EXISTS idx_tool_calls_chat_time
    ON tool_calls (chat_id, timestamp);

-- Rendering a transcript and building history both read a chat's
-- attachments grouped by message.
CREATE INDEX IF NOT EXISTS idx_attachments_chat_message
    ON attachments (chat_id, message_id);

-- Memory is read on every turn; the scheduler polls for due rows.
CREATE INDEX IF NOT EXISTS idx_user_memory_user
    ON user_memory (user_id, id);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
    ON scheduled_tasks (status, run_at);

-- Subdomain reverse proxying and deployment history queries (phase 9).
CREATE INDEX IF NOT EXISTS idx_repo_history_user
    ON repo_history (user_id);
CREATE INDEX IF NOT EXISTS idx_repo_history_subdomain
    ON repo_history (subdomain);
CREATE INDEX IF NOT EXISTS idx_repo_history_process
    ON repo_history (process_id);


-- ---------------------------------------------------------------------
-- pending_cleanup: files still to remove after a deletion
-- ---------------------------------------------------------------------
-- Written in the same transaction as the deletion of a chat (chat_id set)
-- or an account (chat_id NULL), and removed once its container and folders
-- are gone. A server that stops part-way finishes the job when it starts
-- again. No foreign keys: the rows outlive what they name, on purpose.
CREATE TABLE IF NOT EXISTS pending_cleanup (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- retired_subdomains: names a deployment no longer uses
-- ---------------------------------------------------------------------
-- A renamed app, or one whose account was removed, keeps its old name
-- reserved: links to it may still be out there, and another user must not
-- be able to take the name and receive them. The old name redirects to the
-- app's new one while the app exists. No foreign key on user_id, so the
-- name stays taken after the account is gone.
CREATE TABLE IF NOT EXISTS retired_subdomains (
    subdomain   TEXT PRIMARY KEY,
    user_id     INTEGER,
    process_id  TEXT,
    retired_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
