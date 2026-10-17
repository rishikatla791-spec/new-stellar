# Independent New Stellar hosting

This deployment runs on an always-on server. It does not use a laptop,
Cloudflare quick tunnels, or the relay. The current `/app` hosting terminal
is inside a container without Docker access; the server owner must perform
the host-level setup or provide a server administration session.

## Server owner requirements

- Provide a dedicated Linux VM for New Stellar where possible. Running
  generated code requires a Docker-capable runner; access to Docker is
  powerful and must not be granted to arbitrary users of the parent site.
- Install Docker, Redis, Python, Nginx, and the application dependencies
  using [the production guide](deploy_guide.md). Preserve the existing
  `stellarai.site` service and its Nginx server blocks.
- Use a dedicated service user, `/srv/new-stellar` checkout, and container
  prefix `new-stellar`. Bind Gunicorn to `127.0.0.1:5100`, a dedicated
  password-protected Redis to loopback port 6382, and the optional preview
  gateway to `127.0.0.1:5151`. Check that these ports are unused first.
- Do not expose Docker's unauthenticated TCP API or mount its socket into
  generated project containers. New Stellar's trusted application service
  needs Docker access; generated project containers do not.

## Preserve the hosted application

When no relay trial is active, the hosted database and files are authoritative.
During the laptop trial, the latest data is on the runner at
`/home/rishi/new-stellar`; migrate that current copy, not an older hosted backup.
A backup was made
inside its container at `/app/.deployment-backups/20261017-214520` before
any runner activation. Keep that backup private: it includes credentials.

Before the final move, pause writes and take a fresh SQLite backup together
with `keys.env`, `encryption.key`, `vapid_private.pem`, uploads, outputs,
deployments, and sandbox files. Transfer privately to the server, keeping
the existing Flask session secret and encryption keys. Update absolute
`/app/...` data paths to `/srv/new-stellar/...`. Use the dedicated server
Redis URL, not the old container's URL. Back up the destination too.

For this direct deployment, remove `RELAY_SECRET`, `STELLAR_RELAY_ROLE`, and
`STELLAR_APP_LINKS=path` from the application service's environment and
configuration. No relay or laptop runner service is needed.
If the parent hosting platform starts `python app.py`, set
`STELLAR_MANAGED_START=1` to retain the Redis/Gunicorn supervisor after
removing relay mode. This managed supervisor uses port 5000; do not enable
it alongside the separate port-5100 systemd deployment described below.

Use these public settings in the server's private `keys.env`:

```dotenv
STELLAR_DOMAIN=new-stellar.stellarai.site
STELLAR_APP_ORIGIN=https://new-stellar.stellarai.site
STELLAR_CONTAINER_PREFIX=new-stellar
STELLAR_PREVIEW_ORIGIN=https://new-stellar-preview.stellarai.site
STELLAR_PREVIEW_PORT=5151
STELLAR_BROWSER_CHECKS=1
```

Set `REDIS_URL` to the dedicated password-protected Redis instance. Keep
API credentials and account configuration from the hosted app. Launch the
application with its production environment, for example:

```sh
/srv/new-stellar/.venv/bin/gunicorn -k gthread --workers 2 --threads 10 \
  --timeout 3600 --bind 127.0.0.1:5100 'app:create_app()'
```

Install this as a supervised systemd service, not a terminal background job.
The existing auto-pull daemon assumes port 5000; do not run it unchanged
beside the port-5100 service. Release updates should restart this service
only after checks pass.

## Domain and HTTPS routing

The domain owner must configure these names to reach the chosen server:

| Name | Nginx destination |
| --- | --- |
| `new-stellar.stellarai.site` | app on `127.0.0.1:5100` |
| `*.new-stellar.stellarai.site` | app on `127.0.0.1:5100` for project subdomains |
| `*.new-stellar-preview.stellarai.site` | preview gateway on `127.0.0.1:5151` |

A certificate for `*.stellarai.site` does **not** cover these nested project
hosts. Obtain certificates for `new-stellar.stellarai.site` and
`*.new-stellar.stellarai.site`, and a separate certificate for
`*.new-stellar-preview.stellarai.site`. Wildcard issuance requires DNS-01.
Automate renewal using the DNS provider's supported integration with
restricted credentials. If Cloudflare proxies these records, its edge
certificate must cover the nested hosts too; otherwise the owner must use
DNS-only routing with valid origin HTTPS and suitable server protections.

Adapt [the app Nginx example](nginx_stellar.conf) and its proxy snippet:
change the upstream port from 5000 to 5100 and the static directory to
`/srv/new-stellar/static`. Add dedicated server blocks, preserving the
parent site's configuration. Keep the original Host header and HTTPS
forwarding headers. Do not apply the main app's static-file rule to project
hosts. Run `nginx -t` before reloading.

For previews, follow [the preview guide](preview/README.md), replacing its
host with `new-stellar-preview.stellarai.site` and its upstream port with
5151. The gateway must share the application's database path, Redis, Docker
daemon, and container prefix. Configure its service environment accordingly.
Build the matching browser image on this server's Docker daemon.

## Live acceptance checks

1. Confirm the local and public `/healthz` show the intended release.
2. Check `/readyz` explicitly reports `database`, `redis`, and `docker` as
   `ok`. HTTP 200 alone is insufficient: Docker is an optional readiness
   dependency in the current application.
3. Sign in using an existing hosted account and confirm chat/file history.
4. Create a disposable project through the app, start its web server, and
   open `https://<project>.new-stellar.stellarai.site/` with normal TLS
   verification. Check its own CSS, JavaScript, and asset paths.
5. Test preview inside chat and in a separate tab, including WebSockets.
6. Confirm a different user cannot access private preview sessions and
   unknown hosts or expired preview tickets do not expose a project.
7. Stop and restart the test project; verify status and persistent files.
8. Restart the supervised application and preview services. Verify existing
   sessions and deployments recover. Check the parent site still works.
9. Disconnect the laptop and repeat the public deployment check. This is
   the acceptance criterion for independent hosting.

Keep the old hosted container and private backups available until these
checks pass. If the move fails, restore the original app hostname route to
the old container and reconcile any writes before retrying.
