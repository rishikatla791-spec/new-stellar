#!/usr/bin/env bash
#
# Run Stellar the way production does - four Gunicorn workers - and check
# the things that only break when there is more than one process.
#
# Gunicorn is Linux-only, so on Windows run this inside WSL:
#
#     wsl -d Ubuntu -- bash /mnt/c/path/to/stellar/deploy/four_worker_test.sh
#
# It needs a Linux virtualenv (uv venv ~/stellar-linux-venv, then
# uv pip install -r requirements.txt gunicorn) and a reachable Redis.
# It writes to a throwaway database and Redis db 5, and never touches
# stellar_local.db.
#
# What it proves, and why it cannot be proved in the test suite: the suite
# runs in one interpreter, and every bug here is a bug about four.

set -u

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${STELLAR_LINUX_VENV:-$HOME/stellar-linux-venv}"
PORT="${STELLAR_TEST_PORT:-8010}"
REDIS="${STELLAR_TEST_REDIS:-redis://127.0.0.1:6379/5}"
DB="${STELLAR_TEST_DB:-$HOME/stellar_four_worker.db}"
LOG="$HOME/stellar-four-worker.log"
BASE="http://127.0.0.1:$PORT"
JAR="$(mktemp)"
PY="$VENV/bin/python"

fails=0
say() { if [ "$1" = 1 ]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fails=$((fails + 1)); fi; }
redis_py() { "$PY" - "$REDIS" "$@"; }

[ -x "$PY" ] || { echo "No Linux venv at $VENV. See the header."; exit 1; }
"$PY" -c 'import gunicorn' 2>/dev/null || { echo "gunicorn is not installed in $VENV"; exit 1; }

# ---------------------------------------------------------------- start
pkill -f "gunicorn.*app:create_app" 2>/dev/null
sleep 1
rm -f "$DB" "$DB-wal" "$DB-shm" "$LOG"
cd "$PROJ" || exit 1

export DATABASE_NAME="$DB"          # absolute, so it stays off the /mnt mount
export REDIS_URL="$REDIS"
export PYTHONUNBUFFERED=1

# %(p)s is the worker process id, and it is the only way to tell from
# outside which of the four served a given request.
nohup "$VENV/bin/gunicorn" \
  -k gthread --workers 4 --threads 25 --timeout 3600 \
  --bind "127.0.0.1:$PORT" \
  --access-logfile - --error-logfile - \
  --access-logformat 'PID=%(p)s %(m)s %(U)s -> %(s)s' \
  "app:create_app()" > "$LOG" 2>&1 &

for _ in $(seq 1 40); do
  curl -fsS "$BASE/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$BASE/healthz" >/dev/null 2>&1 || { echo "  FAIL  gunicorn did not come up"; tail -n 20 "$LOG"; exit 1; }

WORKERS=$(grep -ac "Booting worker with pid" "$LOG")
say "$([ "$WORKERS" = 4 ] && echo 1 || echo 0)" "four workers booted (saw $WORKERS)"

# --------------------------------------------------------------- set up
curl -fsS -c "$JAR" -b "$JAR" -o /dev/null -X POST "$BASE/auth/register" \
  -d "username=worker@test.local&password=workerpassword123" || true
curl -fsS -c "$JAR" -b "$JAR" -o /dev/null -X POST "$BASE/auth/login" \
  -d "username=worker@test.local&password=workerpassword123" || true
CHAT=$(curl -fsS -c "$JAR" -b "$JAR" -X POST "$BASE/api/chats" \
       -H 'Content-Type: application/json' \
       | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["id"])')

inject() {
  curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -H 'Connection: close' \
    -X POST "$BASE/api/chats/$CHAT/inject" \
    -H 'Content-Type: application/json' -d "{\"message\":\"$1\"}"
}

# ------------------------------------------- the cross-worker claim test
code=$(inject "nothing running")
say "$([ "$code" = 409 ] && echo 1 || echo 0)" "a follow-up is refused when no turn is running ($code)"

# Written straight into Redis, so NO worker holds this claim in memory.
# Before the claim was shared, only the worker that owned it would have
# accepted a follow-up, and the other three would have answered 409.
redis_py "$CHAT" <<'PY' >/dev/null
import sys, redis
redis.from_url(sys.argv[1], decode_responses=True).set(
    f"generating:{int(sys.argv[2])}", "q-held-by-another-worker", ex=120)
PY

MARK=$(wc -l < "$LOG")
codes=""
for i in $(seq 1 16); do codes="$codes $(inject "follow-up $i")"; done
bad=$(printf '%s' "$codes" | tr ' ' '\n' | grep -c -v -e '^20[02]$' -e '^$')
say "$([ "$bad" = 0 ] && echo 1 || echo 0)" "every worker honoured a claim it did not make ($bad rejected of 16)"

PIDS=$(tail -n +$((MARK + 1)) "$LOG" | grep -a "POST /api/chats/$CHAT/inject" \
       | grep -oaE 'PID=<?[0-9]+>?' | sort -u)
N=$(printf '%s\n' "$PIDS" | grep -c 'PID=')
say "$([ "${N:-0}" -ge 2 ] && echo 1 || echo 0)" "those requests really did spread across workers ($N distinct)"

redis_py "$CHAT" <<'PY' >/dev/null
import sys, redis
redis.from_url(sys.argv[1]).delete(f"generating:{int(sys.argv[2])}")
PY
code=$(inject "claim gone")
say "$([ "$code" = 409 ] && echo 1 || echo 0)" "refused again once the claim is cleared ($code)"

# ------------------------------------------------------- a real turn
QID=$(curl -fsS -b "$JAR" -X POST "$BASE/api/chats/$CHAT/query" \
      -H 'Content-Type: application/json' \
      -d '{"message":"Reply with exactly the word PONG and nothing else."}' \
      | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["query_id"])')
OUT=$(curl -s -b "$JAR" --max-time 90 "$BASE/api/stream/$QID?from=0")
printf '%s' "$OUT" | grep -q '"type": "message"' \
  && say 1 "a full turn streamed and committed under gunicorn" \
  || say 0 "the turn did not complete"

ERRS=$(grep -acE 'Traceback|\[ERROR\]|CRITICAL' "$LOG")
say "$([ "$ERRS" = 0 ] && echo 1 || echo 0)" "no tracebacks across four workers"
[ "$ERRS" = 0 ] || grep -aE 'Traceback|\[ERROR\]' "$LOG" | head -n 5 | sed 's/^/        /'

rm -f "$JAR"
echo
if [ "$fails" = 0 ]; then
  echo "  Four workers agree. Log: $LOG"
else
  echo "  $fails check(s) failed. Log: $LOG"
fi
echo "  Stop them with: pkill -f 'gunicorn.*app:create_app'"
exit "$fails"
