#!/usr/bin/env bash
#
# Run Stellar the way production does - four Gunicorn workers - and check
# the things that only break when there is more than one process. The
# checks themselves are in four_worker_check.py.
#
# Gunicorn is Linux-only, so on Windows run this inside WSL:
#
#     wsl -d Ubuntu -- bash /mnt/c/path/to/stellar/deploy/four_worker_test.sh
#
# It needs a Linux virtualenv (uv venv --python 3.12 ~/stellar-linux-venv,
# then uv pip sync -p ~/stellar-linux-venv/bin/python requirements.lock) and
# a reachable Redis. Redis has a password, so pass its URL with database 5:
#
#     STELLAR_TEST_REDIS='redis://:PASSWORD@127.0.0.1:6379/5' bash deploy/four_worker_test.sh
#
# Optional:
#   STELLAR_UPGRADE_DB=/path/to/old.db   also upgrade a COPY of that database
#                                       under four workers (the original is
#                                       only read)
#   STELLAR_LIVE=1                      also run one real reply (one Gemini
#                                       request) with an invalid first key
#
# It writes to a throwaway database, Redis db 5 and folders and container
# names of its own, and never touches stellar_local.db or your workspaces.

set -u

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${STELLAR_LINUX_VENV:-$HOME/stellar-linux-venv}"
PORT="${STELLAR_TEST_PORT:-8010}"
REDIS="${STELLAR_TEST_REDIS:-redis://127.0.0.1:6379/5}"
WORK="$(mktemp -d)"
LOG="$WORK/gunicorn.log"
BASE="http://127.0.0.1:$PORT"
PY="$VENV/bin/python"

[ -x "$PY" ] || { echo "No Linux venv at $VENV. See the header."; exit 1; }
"$PY" -c 'import gunicorn' 2>/dev/null || { echo "gunicorn is not installed in $VENV"; exit 1; }
cd "$PROJ" || exit 1

export REDIS_URL="$REDIS"
export STELLAR_SANDBOX_DIR="$WORK/sandbox_runs" STELLAR_DEPLOYMENTS_DIR="$WORK/deployments"
export STELLAR_OUTPUTS_DIR="$WORK/outputs" STELLAR_UPLOADS_DIR="$WORK/uploads"
export STELLAR_CONTAINER_PREFIX="stl4w"
export PYTHONUNBUFFERED=1
# No model key unless the live phase asks for one: nothing spends quota by
# accident. A blank value counts as unset, and stops a numbered family.
export PRIMARY_API_KEY= PRIMARY_API_KEY_1= BACKUP_API_KEY= BACKUP_API_KEY_1=

"$PY" - "$REDIS" <<'PY' || { echo "  Redis is not reachable (set STELLAR_TEST_REDIS, see the header)"; exit 1; }
import sys, redis
redis.from_url(sys.argv[1], socket_connect_timeout=3).ping()
PY

start() {
  pkill -f "gunicorn.*app:create_app" 2>/dev/null
  sleep 1
  export DATABASE_NAME="$1"           # absolute, so it stays off the /mnt mount
  # As the systemd unit does: the schema first, once, then the workers.
  "$VENV/bin/flask" --app app:create_app init-db >>"$LOG" 2>&1 || { echo "  FAIL  init-db"; return 1; }
  # %(p)s is the worker's process id: the only way to tell from outside
  # which of the four served a given request.
  nohup "$VENV/bin/gunicorn" \
    -k gthread --workers 4 --threads 25 --timeout 3600 \
    --bind "127.0.0.1:$PORT" \
    --access-logfile - --error-logfile - \
    --access-logformat 'PID=%(p)s %(m)s %(U)s -> %(s)s' \
    "app:create_app()" >>"$LOG" 2>&1 &
  for _ in $(seq 1 40); do
    curl -fsS "$BASE/healthz" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "  FAIL  gunicorn did not come up"; tail -n 20 "$LOG"; return 1
}

stop() {
  pkill -f "gunicorn.*app:create_app" 2>/dev/null
  for _ in $(seq 1 20); do pgrep -f "gunicorn.*app:create_app" >/dev/null || return 0; sleep 0.5; done
}

fails=0
phase() { echo; echo "  -- $1"; }

if [ -n "${STELLAR_UPGRADE_DB:-}" ]; then
  phase "an existing database, upgraded by four workers starting together"
  # immutable: the original is only read, and not even a lock file is made.
  "$PY" -c "import sqlite3,sys; s=sqlite3.connect('file:'+sys.argv[1]+'?immutable=1', uri=True); d=sqlite3.connect(sys.argv[2]); s.backup(d)" \
    "$STELLAR_UPGRADE_DB" "$WORK/upgrade.db"
  : > "$LOG"
  if start "$WORK/upgrade.db"; then
    "$PY" deploy/four_worker_check.py upgrade "$WORK/upgrade.db" "$STELLAR_UPGRADE_DB" || fails=$((fails + $?))
  else
    fails=$((fails + 1))
  fi
  stop
  rm -f "$WORK/upgrade.db"*            # a copy of real data: not kept
fi

phase "claims, follow-ups and a scheduled task across four workers (no model calls)"
: > "$LOG"
if start "$WORK/four.db"; then
  "$PY" deploy/four_worker_check.py workers "$BASE" "$WORK/four.db" "$LOG" || fails=$((fails + $?))
else
  fails=$((fails + 1))
fi
stop

if [ "${STELLAR_LIVE:-}" = 1 ]; then
  phase "one real reply: an invalid first key, a reconnect, a stop from elsewhere"
  export PRIMARY_API_KEY="not-a-real-key-for-the-rotation-check"
  # The first real key, in the app's own order; read here, never printed.
  BACKUP_API_KEY_1="$("$PY" - <<'PY'
from dotenv import dotenv_values
v = dotenv_values("keys.env")
names = [n for base in ("PRIMARY_API_KEY", "BACKUP_API_KEY")
         for n in [base] + [f"{base}_{i}" for i in range(1, 20)]]
print(next((v[n].strip() for n in names if (v.get(n) or "").strip()), ""))
PY
)"
  export BACKUP_API_KEY_1
  : > "$LOG"
  if [ -z "$BACKUP_API_KEY_1" ]; then
    echo "  SKIP  no Gemini key in keys.env"
  elif start "$WORK/live.db"; then
    "$PY" deploy/four_worker_check.py live "$BASE" "$WORK/live.db" "$LOG" || fails=$((fails + $?))
  else
    fails=$((fails + 1))
  fi
  stop
fi

echo
if [ "$fails" = 0 ]; then
  echo "  Four workers agree. Log: $LOG"
else
  echo "  $fails check(s) failed. Log: $LOG"
fi
exit "$fails"
