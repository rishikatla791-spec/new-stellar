# Phase 9: Production Deployment & Dynamic App Hosting (repo_control)

Phase 9 turns Stellar into a production-grade platform that can both handle
live, multi-user web traffic securely and allow the AI to provision, manage,
and host running web applications on dedicated subdomains.

---

## 1. The Deployment Architecture

Running in production requires solving two challenges that development servers
ignore:
1. **Concurrency and streaming:** Server-Sent Events (SSE) connections remain open
   for minutes while the LLM streams responses and calls tools. Standard WSGI
   servers block on long-lived connections.
2. **Reverse proxy buffering:** Typical web proxies buffer server responses in
   memory before sending them to the client. For token-by-token LLM output, this
   destroys the streaming experience unless explicitly turned off.

```
 Internet (Browser / Mobile)
            │
            ▼  (Port 80 -> 443 redirect)
  Nginx Reverse Proxy (*.stellarai.site)
  - TLS Termination (Let's Encrypt Wildcard)
  - proxy_buffering off; (Instant SSE delivery)
  - WebSocket / Upgrade headers
            │
            ▼  (UNIX Socket: stellar.sock)
  Gunicorn Application Server
  - Worker class: gthread
  - 4 workers x 25 threads (100 concurrent slots)
  - 3600s timeout for long generations
            │
            ▼
  Stellar Flask App (app.py)
  - intercept_subdomains() detects subdomain requests
  - If main domain -> Serves chat UI, auth, and API
  - If subdomain -> Proxies directly to the deployed Docker container
            │
            ▼  (127.0.0.1:<host_port>)
  Guest App Container (stellar-repo-<id>)
```

---

## 2. Server Configuration Files

- `deploy/nginx_stellar.conf`: Configures Nginx with wildcard domain support
  (`*.stellarai.site`), routes root requests to the Gunicorn UNIX socket,
  and disables proxy buffering (`proxy_buffering off;`).
- `deploy/gunicorn_stellar.service`: Systemd service running Gunicorn with 4
  workers and 25 threads per worker, 3600-second timeouts, and automatic restart.
- `deploy/deploy_guide.md`: Administrator setup instructions for wildcard DNS,
  Certbot SSL generation, and systemd service management.

---

## 3. Dynamic Subdomain Hosting (`repo_control`)

The `repo_control` tool allows the AI model (and users) to deploy and manage
isolated web applications.

### Actions

| Action | Description |
|---|---|
| `deploy` | Provisions an isolated Docker container with an ephemeral host port, assigns a unique subdomain, writes initial files, and records deployment state in `repo_history`. |
| `execute` | Runs bash commands (e.g. `npm install`, `python server.py`) inside the container with execution timeouts and automatic health checks. |
| `list_history` | Lists all past and active deployments for the authenticated user. |
| `snapshot` | Scans non-binary source files in the project workspace and persists them as JSON in SQLite (`repo_history.files_snapshot`). |
| `stop` | Automatically snapshots project state, stops the container, and updates status to `stopped`. |
| `restart` | Restores files from the database snapshot and relaunches the container. |
| `rename` | Updates the project title and regenerates a clean, unique subdomain. |

---

## 4. Security & Isolation

- **Cookie Sandboxing:** `intercept_subdomains()` strips primary Stellar session
  cookies (`session` and `stellar_session_main`) before forwarding requests to
  the guest container. This guarantees guest web apps cannot hijack user sessions.
- **Resource Limits:** Deployed containers run on isolated per-user bridge
  networks with inter-container communication (ICC) disabled, memory caps, and
  CPU throttles.
- **Approval Gate:** If the project owner's account is revoked or unapproved,
  subdomain requests are immediately denied with HTTP 403.
