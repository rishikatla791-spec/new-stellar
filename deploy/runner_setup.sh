#!/bin/bash
# Make this machine Stellar's runner: the machine with Docker that serves
# new-stellar.stellarai.site through the copy inside main Stellar (see
# deploy/relay.py, and deploy/deploy_guide.md section 12).
#
#   sudo bash deploy/runner_setup.sh rishi      # the user who owns this checkout
#
# Run it from the checkout, once; again after changing it. It needs Docker
# Engine, cloudflared, python3-venv, redis-server and git installed, and
# keys.env in the checkout with RELAY_SECRET (the same secret as in the
# container's keys.env). It installs two services, both restarted if they
# ever stop and started at boot:
#   stellar-app     Redis, Gunicorn on 127.0.0.1:5000, new commits from git
#                   (deploy/auto_pull.py in the runner role)
#   stellar-runner  the relay: a Cloudflare quick tunnel, the announcements
#                   to the front, one tunnel per deployed app (deploy/relay.py)
set -euo pipefail

USER_NAME=${1:?"the user who owns this checkout, e.g.: sudo bash deploy/runner_setup.sh rishi"}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
cd "$ROOT"

for tool in docker cloudflared redis-server git python3; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool (see deploy_guide.md section 12)"; exit 1; }
done
python3 -m venv --help >/dev/null 2>&1 || { echo "Missing: python3-venv"; exit 1; }
grep -q '^RELAY_SECRET=.\+' keys.env 2>/dev/null || {
  echo "keys.env needs RELAY_SECRET=... (the same secret as the container's keys.env)"; exit 1; }

# The app starts its own Redis, with the password from keys.env; the
# distribution's Redis service would only hold the port.
systemctl disable --now redis-server >/dev/null 2>&1 || true
usermod -aG docker "$USER_NAME"

as_user() { sudo -u "$USER_NAME" -H bash -c "cd '$ROOT' && $*"; }
[ -x .venv/bin/python ] || as_user "python3 -m venv .venv"
as_user ".venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.lock"
as_user "sg docker -c '.venv/bin/python docker_setup.py'"

unit() {   # name, description, after, command
  cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=$2
After=network-online.target docker.service $3
Wants=network-online.target docker.service

[Service]
User=$USER_NAME
SupplementaryGroups=docker
WorkingDirectory=$ROOT
Environment=STELLAR_RELAY_ROLE=runner
Environment=STELLAR_BIND=127.0.0.1
Environment=PATH=$ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$ROOT/.venv/bin/python $4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}
unit stellar-app "Stellar runner: the app (Redis, Gunicorn, updates from git)" "" "deploy/auto_pull.py"
unit stellar-runner "Stellar runner: relay to main Stellar, tunnels" "stellar-app.service" "deploy/relay.py runner"
systemctl daemon-reload
systemctl enable --now stellar-app stellar-runner
sleep 3
systemctl --no-pager --lines=0 status stellar-app stellar-runner | grep -E "●|Active:"
echo
echo "Done. Logs: $ROOT/auto_pull.log, app.log, relay.log"
echo "Until the container's keys.env has the same RELAY_SECRET, relay.log says"
echo "'front not in relay mode yet'; then this machine moves in and serves the site."
