#!/usr/bin/env bash
# Give a containerised new-stellar access to the server's Docker.
#
# Every chat's sandbox - the Terminal, running code, building websites - is
# a container that Stellar asks Docker to create, through the file
# /var/run/docker.sock. Stellar running inside a container cannot see that
# file unless the container was created with it, so it reports "Docker is
# not running" and no sandbox is ever made. A running container cannot be
# given the file afterwards; it has to be created again. This does that,
# keeping the code, keys.env, the database and the port the site uses.
#
# Run on the server (the VPS), not inside the container:
#   sudo bash docker_access.sh CONTAINER --check   # look only, change nothing
#   sudo bash docker_access.sh CONTAINER           # make the change
#
# The new container is created with:
#   -v /var/run/docker.sock:/var/run/docker.sock  so Stellar can reach Docker
#   the app folder at the same path inside and out, because Docker resolves
#     the folders Stellar hands to sandboxes on the server, not inside
#   --network host  so Stellar reaches Redis and the apps it deploys at
#     127.0.0.1, and listens on the server's port directly
# The old container is stopped, never deleted: to go back, run the two
# commands printed at the end.
set -euo pipefail

OLD="${1:-}"
MODE="${2:-}"
NEW="new-stellar"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
stop_here() { printf '\nSTOPPED, nothing was changed: %s\n' "$*" >&2; exit 3; }

if [ -z "$OLD" ]; then
  say "Usage: sudo bash $0 CONTAINER [--check]"
  say "Containers running now:"
  docker ps --format '  {{.Names}}  ({{.ID}})  {{.Image}}  {{.Ports}}'
  exit 2
fi
command -v docker >/dev/null || stop_here "the docker command is not installed here. Run this on the server itself."
docker inspect "$OLD" >/dev/null 2>&1 || stop_here "there is no container called '$OLD'. 'docker ps' lists them."
if docker inspect "$NEW" >/dev/null 2>&1; then
  stop_here "a container called '$NEW' already exists. If it is a failed earlier attempt, remove it with: docker rm -f $NEW"
fi

ins() { docker inspect -f "$1" "$OLD"; }
IMAGE=$(ins '{{.Config.Image}}')
NETMODE=$(ins '{{.HostConfig.NetworkMode}}')
RUNNING=$(ins '{{.State.Running}}')
LABELS=$(ins '{{range $k, $v := .Config.Labels}}{{$k}} {{end}}')
MOUNTS=$(ins '{{range .Mounts}}{{.Source}}=>{{.Destination}} {{end}}')
BINDINGS=$(ins '{{range $p, $c := .HostConfig.PortBindings}}{{$p}}={{(index $c 0).HostIp}}:{{(index $c 0).HostPort}} {{end}}')

step "What the container looks like now"
say "container: $OLD   image: $IMAGE   running: $RUNNING   network: $NETMODE"
say "ports:     ${BINDINGS:-none published}"
say "folders:   ${MOUNTS:-none mounted (the code lives inside the container)}"

[ "$RUNNING" = "true" ] || stop_here "the container is not running. Start it first (docker start $OLD), so its files can be read."

# A container a platform manages (Coolify, Dokploy, CapRover, Compose...)
# would be recreated by that platform from its own settings, undoing this.
case " $LABELS " in
  *coolify*|*dokploy*|*caprover*|*com.docker.compose.project*|*portainer*)
    stop_here "this container is managed by a platform (labels: $LABELS). Make the change in its settings instead: add the volume /var/run/docker.sock:/var/run/docker.sock, mount the app folder at the same path inside and out, and use host networking." ;;
esac

case "$MOUNTS" in
  */var/run/docker.sock*)
    step "Docker access"
    say "This container already has the Docker socket. Checking Stellar can use it:"
    docker exec "$OLD" python3 -c "import docker; docker.from_env().ping(); print('Stellar can reach Docker')" \
      || say "It cannot: see the error above, and check Docker is running on the server ('docker info')."
    exit 0 ;;
esac

# Where the app folder is, and where it will be on the server.
APP_SRC=""
for m in $MOUNTS; do
  if [ "${m#*=>}" = "/app" ]; then APP_SRC="${m%%=>*}"; fi
done
if [ -n "$APP_SRC" ]; then
  APP="$APP_SRC"; COPY_OUT=no
  say "The app folder is already on the server at $APP."
else
  APP="/app"; COPY_OUT=yes
  if [ -e "$APP" ] && [ -n "$(ls -A "$APP" 2>/dev/null)" ]; then
    stop_here "$APP already exists on the server and is not empty, so the app cannot be copied there safely. Move it aside (mv $APP $APP.old) and run this again."
  fi
fi
docker exec "$OLD" test -f /app/app.py || stop_here "no /app/app.py inside the container: is this new-stellar's container?"

# Which port the site is reached on.
HOST_IP=""; HOST_PORT=""
if [ "$NETMODE" = "host" ]; then
  HOST_IP="0.0.0.0"; HOST_PORT="5000"
else
  count=0
  for b in $BINDINGS; do
    count=$((count + 1)); addr="${b#*=}"; HOST_IP="${addr%:*}"; HOST_PORT="${addr##*:}"
  done
  [ "$count" -ge 1 ] || stop_here "no port is published, so the site must be reached another way (a tunnel or proxy on a Docker network). Send this output to whoever is helping you."
  [ "$count" -eq 1 ] || stop_here "several ports are published ($BINDINGS); which one is the site is not clear. Send this output to whoever is helping you."
  [ -n "$HOST_IP" ] || HOST_IP="0.0.0.0"
fi

# A Cloudflare tunnel or Redis running inside the old container would stop
# with it.
if docker exec "$OLD" sh -c 'command -v pgrep >/dev/null && pgrep -x cloudflared' >/dev/null 2>&1; then
  stop_here "a Cloudflare tunnel (cloudflared) runs inside this container and would stop with it. Run cloudflared on the server itself first, pointing at http://localhost:$HOST_PORT."
fi
# Only the host part of REDIS_URL is ever kept: the line holds a password.
REDIS_HOST=$(docker exec "$OLD" sh -c "grep -E '^REDIS_URL=' /app/keys.env 2>/dev/null | tail -1" \
             | tr -d "\"'" \
             | sed -nE 's#^REDIS_URL=[a-z]+://([^@/]*@)?([A-Za-z0-9._-]+).*#\2#p')
if docker exec "$OLD" sh -c "grep -qE '^REDIS_URL=' /app/keys.env" 2>/dev/null && [ -z "$REDIS_HOST" ]; then
  stop_here "REDIS_URL in keys.env is not in the form redis://[:password@]host:port/db, so it cannot be moved safely."
fi
REDIS_PLAN="keep"
case "$REDIS_HOST" in
  ""|127.0.0.1|localhost)
    if docker ps --format '{{.Names}}' | grep -qx stellar-redis; then
      REDIS_PLAN="keep"
    elif docker exec "$OLD" sh -c 'command -v redis-server >/dev/null' 2>/dev/null \
         || ! (command -v ss >/dev/null && ss -ltn | grep -q ':6379 '); then
      REDIS_PLAN="create"
    fi ;;
  *)
    stop_here "keys.env points Redis at '$REDIS_HOST', a name host networking cannot reach. Send this output to whoever is helping you." ;;
esac

# Settings passed to the old container as environment variables travel too
# (FLASK_SECRET_KEY may be one: losing it signs everybody out).
ENV_FILE="${HOME:-/root}/new-stellar.env"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$OLD" \
  | grep -vE '^(PATH|HOME|HOSTNAME|TERM|LANG|GPG_KEY|PYTHON_[A-Z_]*|PIP_[A-Z_]*)=' \
  | { if [ "$REDIS_PLAN" = "create" ]; then grep -v '^REDIS_URL='; else cat; fi; } \
  | grep -v '^$' > "$ENV_FILE.pending" || true

step "The plan"
[ "$COPY_OUT" = yes ] && say "1. copy /app (code, keys.env, database) out of the container to $APP on the server"
say "2. save the container as an image (new-stellar-backup), which keeps its installed packages"
say "3. stop the old container (not delete it), and stop it restarting at boot"
say "4. start '$NEW': Docker socket, $APP at the same path, host network, listening on $HOST_IP:$HOST_PORT"
say "   with $(wc -l < "$ENV_FILE.pending" | tr -d ' ') setting(s) carried over from the old container's environment"
[ "$REDIS_PLAN" = "create" ] && say "5. create a password-protected Redis on the server (docker_setup.py --secure-redis)"
say "6. check Stellar can reach Docker, then build the sandbox image (docker_setup.py)"

if [ "$MODE" = "--check" ]; then
  rm -f "$ENV_FILE.pending"
  say ""
  say "Check only: nothing was changed. Run again without --check to do it."
  exit 0
fi

rollback() {
  say ""
  say "To go back to the old container:"
  say "  docker rm -f $NEW"
  say "  docker update --restart=unless-stopped $OLD && docker start $OLD"
}
trap 'say ""; say "Something failed above."; rollback' ERR

if [ "$COPY_OUT" = yes ]; then
  step "1. Copying the app out of the container"
  docker cp "$OLD:/app" "$APP"
fi
step "2. Saving the container as an image"
docker commit "$OLD" new-stellar-backup >/dev/null
step "3. Stopping the old container"
docker update --restart=no "$OLD" >/dev/null
docker stop "$OLD" >/dev/null
mv "$ENV_FILE.pending" "$ENV_FILE"; chmod 600 "$ENV_FILE"

step "4. Starting $NEW with Docker access"
docker run -d --name "$NEW" --restart unless-stopped --network host \
  --env-file "$ENV_FILE" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$APP:$APP" -w "$APP" \
  new-stellar-backup \
  sh -c "git pull --ff-only || echo 'git pull skipped'; \
         python3 -m pip install -q -r requirements.lock && \
         python3 -m flask --app app:create_app init-db && \
         exec python3 -m gunicorn -k gthread --workers 4 --threads 25 --timeout 3600 \
              --bind $HOST_IP:$HOST_PORT 'app:create_app()'" >/dev/null

health() {
  local url="http://127.0.0.1:$HOST_PORT/healthz"
  for _ in $(seq 1 60); do
    if docker exec "$NEW" python3 -c "import urllib.request,sys; urllib.request.urlopen('$url', timeout=3)" 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

if [ "$REDIS_PLAN" = "create" ]; then
  step "5. Creating Redis on the server"
  for _ in $(seq 1 40); do
    docker exec "$NEW" python3 -c "import docker" 2>/dev/null && break
    sleep 3
  done
  docker exec "$NEW" python3 docker_setup.py --secure-redis
  docker restart "$NEW" >/dev/null
fi

say "Waiting for Stellar to answer on port $HOST_PORT (the first start installs packages)..."
health || { docker logs --tail 40 "$NEW"; false; }
say "Stellar is up."

step "6. Docker access and the sandbox image"
docker exec "$NEW" python3 -c "import docker; docker.from_env().ping(); print('Stellar can reach Docker')"
docker exec "$NEW" python3 docker_setup.py

trap - ERR
step "Done"
say "new-stellar now runs in the container '$NEW' with access to Docker."
say "Open the site and press Terminal: it should give you a shell in the chat's sandbox."
say "The old container '$OLD' is stopped and kept, with its image saved as new-stellar-backup."
rollback
