# Deploying Stellar

For an Ubuntu 22.04 or 24.04 server, behind nginx, run by systemd, with a
wildcard certificate so the agent can host apps on subdomains.

Replace `stellarai.site` with your own domain everywhere, and
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
uv pip install -p ~/stellar-linux-venv/bin/python -r requirements.txt gunicorn
```

Redis must be reachable at `127.0.0.1:6379`. The script uses a throwaway
database and Redis db 5, so your real data is untouched. Stop the workers
afterwards with `pkill -f 'gunicorn.*app:create_app'`.

---

## 1. The server

- Ubuntu 22.04 or 24.04 with sudo.
- A domain, with two DNS records pointing at the server:
  - `A` for `stellarai.site`
  - `A` for `*.stellarai.site`

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
.venv/bin/pip install -r requirements.txt gunicorn
```

`gunicorn` is installed separately on purpose: it is how the app is
served, not something the app imports, so it is not in `requirements.txt`.

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
| `REDIS_URL` | `redis://127.0.0.1:6379/0` |
| `STELLAR_DOMAIN` | Your domain. Must match nginx's `server_name`. |

Leaving `STELLAR_DOMAIN` unset is the quiet failure to watch for. The app
falls back to a built-in default, so deployments still succeed and the
links it hands out point at a domain you do not own.

Then build the sandbox image and its network:

```bash
.venv/bin/python docker_setup.py
```

This is a required step, not an optimisation. The first `lab_execute` with
no image raises `ImageNotFound`, and nothing else builds it.

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
  -d stellarai.site -d '*.stellarai.site'
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

---

## 8. Check it

```bash
curl -fsS https://stellarai.site/healthz            # {"status": "ok"}
sudo journalctl -u stellar -n 50 --no-pager
```

Then in a browser: register (the first account is approved automatically
and is the admin), send a message, and confirm the reply arrives token by
token rather than all at once. Arriving in one lump means buffering is
still on somewhere.

---

## 9. How a deployed app is reached

1. `repo_control` picks a slug and starts a container, publishing the
   app's port on a random host port.
2. The subdomain, the host port and the container id go into
   `repo_history`.
3. A visit to `https://demo.stellarai.site/` matches the wildcard server
   block and is proxied to Gunicorn with the original `Host` header.
4. A `before_request` hook reads that header, looks the subdomain up, and
   proxies the request on to the container.
5. Session cookies for the main site are stripped on the way in, so a
   deployed app never sees a visitor's Stellar session.

That last point is a partial protection, not a guarantee. Deployed apps
live on a subdomain of the authenticated site, the session cookie is
`SameSite=Lax`, and the app has no CSRF tokens. A deliberately hostile
app could still set a cookie for the parent domain or post to it with a
visitor's session. Do not host apps you would not run yourself until
guest apps get their own separate domain. `notes/07-audit.md` records this
and the other known gaps.
