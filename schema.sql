-- Stellar database schema (phase 1).
--
-- Applied by init_db() in db.py. Safe to re-run: every statement uses
-- IF NOT EXISTS, so running it against an existing database is a no-op
-- rather than an error.
--
-- Later phases add tables here (tool_calls, scheduled_tasks, repo_history,
-- user_logs_prefs, ...). Phase 1 needs exactly three.

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
-- Phase 1 uses local username + password. Phase 8 swaps this for Google
-- OAuth, at which point password_hash becomes nullable and a google_sub
-- column appears. Keeping username as the email address now means that
-- migration is additive rather than a rewrite.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,   -- email address
    password_hash TEXT    NOT NULL,
    display_name  TEXT,
    -- Stellar is invite-only: a registered user cannot chat until an admin
    -- approves them. Enforced by the @require_approval decorator.
    is_approved   INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- chats
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    -- NULL until the first message arrives, then auto-generated from it.
    name       TEXT,
    -- Temporary chats are not listed in the sidebar and are swept
    -- periodically. Phase 8.
    is_temp    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Bumped on every new message so the sidebar can sort by recency
    -- without an aggregate query over messages.
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),

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

    -- Hidden messages are excluded from the UI but still sent to the model.
    -- Nothing sets this in phase 1; it exists now because phase 6 relies on
    -- it for two things - memory compression, and storing the partial reply
    -- when a user interrupts mid-generation. Adding the column now costs
    -- nothing and avoids a migration on live data later.
    hidden          INTEGER NOT NULL DEFAULT 0,

    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),

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

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------
-- The hot query is "give me this chat's visible messages in order", run on
-- every page load and before every model call. Without this index SQLite
-- scans every message in the table and sorts the result.
CREATE INDEX IF NOT EXISTS idx_messages_chat_time
    ON messages (chat_id, timestamp);

-- Sidebar: this user's chats, most recent first.
CREATE INDEX IF NOT EXISTS idx_chats_user_updated
    ON chats (user_id, updated_at DESC);

-- Rendering a transcript needs every tool call for a chat in order.
CREATE INDEX IF NOT EXISTS idx_tool_calls_chat_time
    ON tool_calls (chat_id, timestamp);

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

