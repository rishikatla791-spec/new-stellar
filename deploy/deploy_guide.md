# Deploying Stellar

For an Ubuntu 22.04 or 24.04 server, behind nginx, run by systemd, with a
wildcard certificate so the agent can host apps on subdomains.

Replace `example.com` with your own domain everywhere, and
`stellaradmin` with your own user. The two must be consistent across
nginx, systemd and `keys.env`; most deployment failures are one of them
disagreeing with the others.

---

## 0. First, prove it under four workers

Everything below runs the app as four processes. Development runs it as
one, and that difference is where the bugs are. None of them show up in
the test suite, because the suite runs in a single interpreter.

Gunicorn is Linux only. On Windows, use WSL:

```bash
wsl -d Ubuntu -- bash /mnt/c/Users/<you>/Downloads/stellar/deploy/four_worker_test.sh
```

One-time setup inside the distro, no root needed:

```bash
uv venv --python 3.12 ~/stellar-linux-venv
uv pip sync -p ~/stellar-linux-venv/bin/python requirements.lock
```

Redis must be reachable at `127.0.0.1:6379`, and it has a password, so
give the script its URL with database 5:
`STELLAR_TEST_REDIS='redis://:PASSWORD@127.0.0.1:6379/5'`. The script uses
a throwaway database, Redis db 5 and folders of its own, so your real data
is untouched. Stop the workers afterwards with
`pkill -f 'gunicorn.*app:create_app'`.

---

## 1. The server

- Ubuntu 22.04 or 24.04 with sudo.
- A domain, with two DNS records pointing at the server:
  - `A` for `example.com`
  - `A` for `*.example.com`

The wildcard is what makes deployed apps reachable. Without it only the
main site resolves.

```bash
sudo apt-get update
sudo apt-get install -y docker.io redis-server nginx certbot \
                        python3-venv python3-pip
sudo systemctl enable --now redis-server docker
```

`python3-venv` matters: Ubuntu splits it out of the base Python, and
without it `python3 -m venv` creates an environment with no pip.

Give Redis a password and keep it on loopback. Code the model writes runs
in sandbox containers on this machine, and a Redis without a password is
one every sandbox could read and write: live replies, terminal keystrokes,
SSH approvals. Ubuntu's `redis-server` already binds to 127.0.0.1; the
password is the part to add:

```bash
REDIS_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
sudo sed -i "s/^# *requirepass .*/requirepass $REDIS_PASS/" /etc/redis/redis.conf
grep -q "^requirepass" /etc/redis/redis.conf || echo "requirepass $REDIS_PASS" | sudo tee -a /etc/redis/redis.conf >/dev/null
sudo systemctl restart redis-server
echo "REDIS_URL=redis://:$REDIS_PASS@127.0.0.1:6379/0"   # goes into keys.env in step 4
```

---

## 2. The application user and its Docker access

```bash
sudo adduser --disabled-password --gecos "" stellaradmin
sudo usermod -aG docker stellaradmin
```

The `docker` group is not optional. The agent's sandbox and every deployed
app talk to the Docker daemon through its socket, and without membership
each one returns "Docker is not reachable". The systemd unit also grants
it with `SupplementaryGroups=docker`, so the service works even before
that user next logs in.

nginx also needs to traverse the home directory to reach the socket. On
current Ubuntu a new home is `0750`, which nginx cannot enter, and the
symptom is a 502 with "permission denied" in the error log:

```bash
sudo chmod o+x /home/stellaradmin
```

---

## 3. The code and its environment

```bash
sudo -u stellaradmin -i
git clone https://github.com/rishikatla791-spec/new-stellar.git my_app
cd my_app
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

`requirements.lock` pins every package, dependencies of dependencies
included, to the versions the test suite runs against; gunicorn is in it
for Linux. `requirements.txt` lists only what the app asks for directly, and
installing from it picks whatever is newest that day.

---

## 4. Configuration

```bash
cp keys.env.example keys.env
chmod 600 keys.env
nano keys.env
```

The minimum for the app to start at all:

| Setting | Why |
|---|---|
| `FLASK_SECRET_KEY` | Signs session cookies. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. If it changes, everyone is logged out. |
| `PRIMARY_API_KEY` | At least one Gemini key, or no turn can run. |
| `REDIS_URL` | `redis://:<password>@127.0.0.1:6379/0`, with the password from step 1 |
| `STELLAR_DOMAIN` | Your domain. Must match nginx's `server_name`. The systemd unit sets it; keys.env may instead. |
| `ADMIN_EMAILS` | Optional. Addresses that become administrators when they sign in with Google (see step 8). |

Leaving `STELLAR_DOMAIN` unset is the quiet failure to watch for. The app
then treats the server as a local install, so deployments still succeed
but the links it hands out are `<name>.localhost` addresses that work on
no one else's machine. `verify_env.py` reads only keys.env, so it calls the
install local unless `STELLAR_DOMAIN` is set there; setting it in keys.env
as well as the unit keeps the two from disagreeing.

Then build the sandbox image:

```bash
.venv/bin/python docker_setup.py
```

This is a required step, not an optimisation. The first `lab_execute` with
no image raises `ImageNotFound`, and nothing else builds it. The sandbox
networks are created by the app on first use, with bridges named `stl-...`
and addresses from `10.213.0.0/16`. If your server's own network uses that
range, set `STELLAR_SANDBOX_POOL` in keys.env to an unused /16.

Check the whole environment before going further:

```bash
.venv/bin/python verify_env.py
```

---

## 5. The certificate

A wildcard certificate needs a DNS-01 challenge, so certbot cannot do it
unattended without a plugin for your DNS provider.

```bash
sudo certbot certonly --manual --preferred-challenges=dns \
  -d example.com -d '*.example.com'
```

Quote the wildcard or the shell will expand it. Add the `_acme-challenge`
TXT records certbot prints, wait for them to propagate, then continue.

---

## 6. nginx

```bash
sudo mkdir -p /etc/nginx/snippets
sudo cp deploy/stellar_proxy_snippet.conf /etc/nginx/snippets/stellar_proxy.conf
sudo cp deploy/nginx_stellar.conf /etc/nginx/sites-available/stellar
sudo ln -sf /etc/nginx/sites-available/stellar /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Two things in that config earn their place:

`proxy_buffering off` is what makes streaming work. With buffering on,
nginx holds tokens until its buffer fills and the reply arrives in lumps.

The `/static/` rule is in the main server block only. When it also matched
the wildcard it intercepted every deployed app's own assets, so those apps
loaded their page and then failed to load any stylesheet or script.

The two server blocks differ on purpose in their timeouts. The main site
allows an hour, because a reply can stream that long; deployed apps get two
minutes and at most 20 connections each, so one slow app cannot hold the
workers Stellar needs. The app enforces its own limits as well.

Security headers: nginx adds HSTS. The app sets the rest itself on its own
pages: a Content-Security-Policy that allows only Stellar's own files,
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and a strict
referrer policy. While Gunicorn is down or restarting, nginx shows
`deploy/errors/stellar-down.html` instead of a bare 502; nginx reads it
from the project folder, which the `chmod o+x` in step 2 makes reachable.

---

## 7. systemd

```bash
sudo cp deploy/gunicorn_stellar.service /etc/systemd/system/stellar.service
sudo nano /etc/systemd/system/stellar.service     # user, paths, domain
sudo systemctl daemon-reload
sudo systemctl enable --now stellar
sudo systemctl status stellar
```

The unit waits for Redis and Docker, grants the docker group, sets
`SESSION_COOKIE_SECURE=1` because nginx terminates TLS, and tolerates a
missing `keys.env` so that the failure comes from the app with a message
naming the missing setting, rather than from systemd saying only "failed".
Before the workers start it runs `flask init-db`, so a schema upgrade
happens once, and one that fails stops the service with the reason in
`journalctl -u stellar` rather than in four workers' logs.

Then the sandbox firewall rules. Sandboxes reach the internet by design;
without these rules they also reach the host's own services, other
machines on the private network and the cloud metadata service:

```bash
sudo cp deploy/stellar-sandbox-egress.service /etc/systemd/system/
sudo nano /etc/systemd/system/stellar-sandbox-egress.service   # paths
sudo systemctl daemon-reload
sudo systemctl enable --now stellar-sandbox-egress
sudo deploy/sandbox_egress.sh --status
```

The rules match only interfaces named `stl-*`, the bridges Stellar creates,
so other containers on the machine are untouched. They were checked on
Docker Desktop's Linux VM (kernel 6.6, iptables 1.8.9 nf_tables, Docker
29.8): from a sandbox network the host, `host.docker.internal`, a private
address and the metadata address were all refused while the internet and
DNS still worked; an ordinary container was unaffected. On a cloud server
also require IMDSv2 with a hop limit of 1, so that even a gap in these
rules cannot hand out instance credentials.

---

### The SSH gateway (optional)

`ssh -p 2222 you@example.com` opens a shell in a chat's sandbox once a
signed-in user approves the code it prints. It is a separate service:

```bash
sudo cp deploy/stellar-ssh.service /etc/systemd/system/
sudo nano /etc/systemd/system/stellar-ssh.service     # user, paths
sudo systemctl daemon-reload
sudo systemctl enable --now stellar-ssh
sudo ufw allow 2222/tcp
```

It listens on all interfaces only because the unit says so; the code's
default is loopback. A connection signs in one of two ways:

- **SSH password.** A user sets one under Settings, "Terminal from your
  computer (SSH)", and connects with their email as the user name:
  `ssh you@gmail.com@your-server -p 2222`. Five wrong passwords lock that
  account and address out for 15 minutes.
- **Browser approval.** Any other user name gets a code to approve at
  `/device` while signed in.

The gateway also caps sessions per address and new connections per minute.

Behind Cloudflare's proxy (an orange-cloud record), SSH cannot reach the
server: the proxy carries web traffic only. Point a "DNS only" record such
as `ssh.example.com` at the server, or use its IP, and set
`STELLAR_SSH_PUBLIC_HOST` to it in keys.env so Settings shows the right
command.

### Backups

The database holds every account and chat. Back it up daily with SQLite's
online backup, which copies a consistent snapshot while the workers are
writing (copying the file with `cp` can catch it mid-write):

```bash
sudo cp deploy/stellar-backup.service deploy/stellar-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stellar-backup.timer
sudo systemctl start stellar-backup      # one now, to see it work
ls -l /home/stellaradmin/my_app/backups/
```

Each run checks the copy opens and passes an integrity check, and keeps two
weeks. Backups land on the same disk, so copy them off the machine too
(rsync, or your provider's snapshots), along with `keys.env` (kept
separately: it is every secret) and the `uploads/`, `outputs/` and
`deployments/` folders.

Practise a restore before you need one:

```bash
sudo systemctl stop stellar
cp backups/stellar-YYYYMMDD-HHMMSS.db stellar_local.db
rm -f stellar_local.db-wal stellar_local.db-shm
sudo systemctl start stellar          # init-db brings an older copy up to date
```

---

## 8. Check it

```bash
curl -fsS https://example.com/healthz            # {"status": "ok"}
curl -fsS https://example.com/readyz             # Redis and the database ready
sudo journalctl -u stellar -n 50 --no-pager
```

Then make yourself the administrator. Signing up never grants that, not
even to the first account: whoever reached a fresh server first used to
get it. Either put your address in `ADMIN_EMAILS` in keys.env and sign in
with Google, or sign up with a password and then run on the server:

```bash
.venv/bin/flask --app app:create_app make-admin you@example.com
```

Then in a browser: sign in, send a message, and confirm the reply arrives token by
token rather than all at once. Arriving in one lump means buffering is
still on somewhere.

---

### If Stellar itself runs inside a container

This guide runs Stellar directly on the server. If you put it inside a
Docker container instead, it cannot create sandboxes: it reaches Docker
through `/var/run/docker.sock`, which a container only has if it was
created with it, so the Terminal, running code and building websites all
report that Docker is not running. Recreate the container with the socket,
the app folder at the same path inside and out, and host networking.
`deploy/docker_access.sh` does that and keeps the code, keys.env, the
database and the port:

```bash
sudo bash deploy/docker_access.sh CONTAINER --check   # look, change nothing
sudo bash deploy/docker_access.sh CONTAINER           # make the change
```

## 9. How a deployed app is reached

1. `repo_control` picks a slug and starts a container, publishing the
   app's port on a random host port.
2. The subdomain, the host port and the container id go into
   `repo_history`.
3. A visit to `https://demo.example.com/` matches the wildcard server
   block and is proxied to Gunicorn with the original `Host` header.
4. A `before_request` hook reads that header, looks the subdomain up, and
   proxies the request on to the container.
5. Session cookies for the main site are stripped on the way in, so a
   deployed app never sees a visitor's Stellar session.

Several things stop a deployed app reaching into Stellar: Stellar's forms
and API require a CSRF token, an app's `Set-Cookie` headers lose any
`Domain` attribute, the production session cookie is `__Host-` prefixed,
and Stellar's pages refuse to be framed. Still, apps live on a subdomain of
the authenticated site, and a separate domain for them is the complete
fix. Do not host apps you would not run yourself. `notes/07-audit.md`
records this and the other known gaps.

## 10. Upgrading

```bash
sudo -u stellaradmin -i
cd my_app
.venv/bin/python deploy/backup_db.py --check     # a backup first, always
git pull
.venv/bin/pip install -r requirements.lock
exit
sudo systemctl restart stellar stellar-ssh       # init-db applies any schema change first
```

## 11. The landing pages on Vercel

The two landing pages can each live on their own Vercel site, without the
app: the crimson Petrova-lines page (`/welcome`) and the wormhole and
Gargantua page (`/cosmos`). Everything that moves on them is drawn by
WebGL in the visitor's browser, so each is just files - one HTML page, its
scripts, styles, a font and a few images (Petrova ~310 KB, cosmos
~580 KB). The Flask app itself is not a fit for Vercel (its serverless
functions keep no SQLite file and run no background threads); it stays on
the server above.

Make the static copies (run again whenever a page changes, then commit):

```
.venv/Scripts/python deploy/vercel_landing.py     # Windows; .venv/bin/python elsewhere
```

It renders each page as an anonymous visitor sees it, but without the
sign-up and sign-in links (and the cosmos page's "Crimson edition" link -
the other page is its own site), copies only the git-tracked files each
page loads into `deploy/vercel/petrova/` and `deploy/vercel/cosmos/`, and
writes each a `vercel.json` with the app's own security headers (the same
Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`,
`Referrer-Policy: same-origin`). It stops with an error if any file a page
or its stylesheets link to is missing, or a link into the app is left.

Deploy each folder as its own Vercel project, either way:

- From GitHub (redeploys on every push): on vercel.com, Add New, Project,
  import `new-stellar`. Root Directory: `deploy/vercel/petrova`. Framework
  preset: Other. Leave the build command and output directory empty.
  Deploy. Then Add New, Project again, the same repository, Root
  Directory `deploy/vercel/cosmos`.
- From this machine: `npm i -g vercel`, then in each folder run `vercel`
  (first time: log in, accept the defaults) and `vercel --prod`.

Each gets its own `*.vercel.app` address (a project's name sets it; a
custom domain goes in its Settings, Domains). Open both on a laptop: the
ribbons flow on one; on the other the entry plays, Gargantua takes the
hero and the footer flows.

Before the cosmos site is public: `static/cosmos/wormhole.jpg` is a
picture of the film's wormhole; a film still needs the rights holder's
permission to be published.

---

## Inside main Stellar (a project at new-stellar.example.com)

The live copy runs as a project inside main Stellar, in a container whose
own command is `sleep infinity`: nothing in it starts Redis or Gunicorn.
`deploy/auto_pull.py` does, and keeps them current. Start it once from the
Stellar Console (main Stellar → Terminal → new-stellar):

```bash
cd /app && setsid nohup python3 deploy/auto_pull.py > /dev/null 2>&1 < /dev/null &
```

Every 30 seconds it starts Redis or Gunicorn if either is down, pulls a new
commit on `main`, and upgrades Gunicorn onto it in place (no downtime;
nothing reloads if the new code does not compile). Its log is
`/app/auto_pull.log`; `/healthz` names the commit the server is running.

Never use the console's Restart or Stop buttons: they restart the container,
which stops everything including auto_pull, and the command above is needed
again.

## 12. Docker for the live site: a runner behind main Stellar

Main Stellar gives the projects it hosts no Docker, so inside it the labs,
terminals, code runs and deployed apps cannot work, and nothing can reach
into that container except main Stellar's own proxy (plain HTTP: no
WebSockets, and the Host header dropped). So the site is served by a
**runner**, a machine with Docker, and the copy inside main Stellar becomes
its **front**. `deploy/relay.py` is both ends:

    visitor -> main Stellar's proxy -> front (relay.py front, :5000)
            -> WebSocket over HTTPS -> Cloudflare quick tunnel -> runner
            -> Stellar on the runner (127.0.0.1:5000) -> Docker

- The runner opens a Cloudflare quick tunnel (no account, no DNS change,
  nothing opened on its network) and tells the front where it is every 30
  seconds. Every request then travels to it as one exchange over a pooled
  WebSocket; replies stream, so chat streams and terminals stay live.
- Everything is signed with `RELAY_SECRET`, which must be the same in both
  keys.env files. The runner forwards only to its own Stellar.
- Moving in: the first time the runner finds the front in relay mode, it
  downloads the front's data (database, keys.env, encryption and push keys,
  uploads, outputs, projects) through the signed `/__relay/export`, keeps
  what it had in `.relay/before-import-*`, installs it, and only then
  starts taking traffic. It keeps its own `REDIS_URL` and `RELAY_*` lines.
- While the runner is away (laptop off), the front serves the landing pages
  from its own Gunicorn (on 127.0.0.1:5002, without cookies) and answers
  everything else with an "offline" page; the front's copy runs no
  scheduled tasks, so nothing runs twice. `RELAY_FALLBACK=full` serves the
  whole site from the front's (older) copy instead.
- Deployed apps: main Stellar answers every subdomain of its domain
  itself, so an app's link is `https://new-stellar.example.com/a/<name>/`. The
  runner opens a quick tunnel for each running app and keeps its address in
  Redis; `/a/<name>/` wakes the app if it sleeps and redirects there. Apps
  on their own address never see Stellar's cookies.
- `https://new-stellar.example.com/__relay/status` says whether the
  runner is `up`, `moving in`, `down` or `waiting`.

### Set up the runner (a Windows laptop with WSL, or any Linux machine)

Docker Engine runs natively in WSL's Ubuntu, without Docker Desktop: a
server needs no window, and Docker Desktop's window is what crashed on this
laptop (its GPU process died on the hybrid AMD/NVIDIA graphics, taking the
engine with it). In Ubuntu, as root, install Docker Engine from
download.docker.com and cloudflared from pkg.cloudflare.com (their official
apt repositories), plus `python3-venv redis-server git sqlite3`. Turn off
Docker Desktop's WSL integration for Ubuntu (Settings, Resources, WSL
integration) so its CLI does not shadow the real one. Then, in Ubuntu:

```bash
git clone https://github.com/rishikatla791-spec/new-stellar.git ~/new-stellar
cd ~/new-stellar
cp /mnt/c/path/to/keys.env keys.env      # any working keys.env; moving in replaces it
echo "RELAY_SECRET=<the secret>" >> keys.env
sed -i -E 's#^(REDIS_URL=.*127\.0\.0\.1:)6379#\16380#' keys.env   # if Docker Desktop holds 6379
sudo bash deploy/runner_setup.sh "$USER"
```

On Windows, keep WSL running with no terminal open and start it at sign-in
(`powershell -ExecutionPolicy Bypass -File deploy\runner_windows.ps1`),
and in Windows' power settings let the laptop stay awake while plugged in
(and with the lid closed, if it lives closed).

### Go live

In the Stellar Console (main Stellar, Terminal, new-stellar), add the same
secret to the container's keys.env:

```bash
echo 'RELAY_SECRET=<the secret>' >> /app/keys.env
```

Within 30 seconds auto_pull moves the container's Gunicorn to 5002 and
starts the front; the runner finds it, moves in (a minute or so) and the
site is served from the runner. To go back, delete that line: auto_pull
stops the front and serves directly again.
