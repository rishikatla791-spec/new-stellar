# Stellar Production Deployment Guide (Phase 9)

This guide walks through deploying Stellar to an Ubuntu/Debian Linux VPS using Nginx, Gunicorn, systemd, and Let's Encrypt with wildcard SSL for dynamic application subdomains.

---

---

## 0. Before any of this: prove it under four workers

Everything below runs the app as four processes. Development runs it as
one. That difference is where the bugs are, and none of them show up in
the test suite, because the suite runs in a single interpreter.

Gunicorn is Linux only. On Windows, use WSL:

```
wsl -d Ubuntu -- bash /mnt/c/Users/<you>/Downloads/stellar/deploy/four_worker_test.sh
```

One-time setup inside the distro, with no root needed:

```
uv venv --python 3.12 ~/stellar-linux-venv
uv pip install -p ~/stellar-linux-venv/bin/python -r requirements.txt gunicorn
```

Redis must be reachable at `127.0.0.1:6379`. The script writes to a
throwaway database and Redis db 5, so your real data is untouched.

It checks that four workers boot, that a follow-up is refused when no turn
is running, that a claim made by one worker is honoured by the others, that
the requests genuinely spread across workers, and that a full turn streams
and commits. Stop the workers afterwards with
`pkill -f 'gunicorn.*app:create_app'`.

## 1. Prerequisites

- An Ubuntu 22.04 / 24.04 server with root/sudo access.
- A domain name (e.g. `yourdomain.com`).
- DNS Records configured:
  - `A` record: `yourdomain.com` -> `<Server IP>`
  - `A` record (Wildcard): `*.yourdomain.com` -> `<Server IP>`
- Docker and Redis installed and running:
  ```bash
  sudo apt-get update
  sudo apt-get install -y docker.io redis-server nginx certbot python3-certbot-nginx
  sudo systemctl enable --now redis-server docker
  ```

---

## 2. Wildcard SSL Certificate via Let's Encrypt

A wildcard certificate covering both `yourdomain.com` and `*.yourdomain.com` requires a DNS-01 challenge (or certbot DNS plugin):

```bash
sudo certbot certonly --manual --preferred-challenges=dns \
  -d yourdomain.com -d *.yourdomain.com
```
Follow the prompt to add the `_acme-challenge` TXT records to your DNS registrar.

---

## 3. Configure Nginx

1. Copy the configuration file:
   ```bash
   sudo cp deploy/nginx_stellar.conf /etc/nginx/sites-available/stellar
   ```
2. Replace `stellarai.site` with your actual domain name and adjust the paths to your certificate files.
3. Enable the site and test configuration:
   ```bash
   sudo ln -s /etc/nginx/sites-available/stellar /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

**Key Nginx Directive:**
- `proxy_buffering off;` is critical. Without this, Nginx buffers the SSE stream, delaying token output until the buffer fills up.

---

## 4. Install Dependencies & Gunicorn

In the application root:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

---

## 5. Configure Systemd Service

1. Copy the systemd service file:
   ```bash
   sudo cp deploy/gunicorn_stellar.service /etc/systemd/system/stellar.service
   ```
2. Adjust `User`, `WorkingDirectory`, and paths in `/etc/systemd/system/stellar.service`.
3. Enable and start Stellar:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now stellar
   sudo systemctl status stellar
   ```

---

## 6. How Dynamic Subdomains Work (`repo_control`)

When an app is deployed via `repo_control`:
1. A unique subdomain slug is generated (e.g. `demo-app.yourdomain.com`).
2. An isolated Docker container is started with port mapping (e.g. internal port 5000 -> random host port).
3. The host and container port mapping are saved in `repo_history` and cached in Redis.
4. When a user visits `https://demo-app.yourdomain.com/`:
   - Nginx matches `*.yourdomain.com` and forwards the request to Gunicorn with the original `Host` header.
   - Stellar's `intercept_subdomains` `before_request` hook detects the subdomain, queries the host port, and streams the proxy response directly to/from the container.
   - User session cookies for the main Stellar site are stripped to isolate deployed apps securely.
