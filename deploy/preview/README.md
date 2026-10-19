# Live preview and browser checks

The optional preview service streams an **owned, running deployed project**
beside chat or in a separate tab. It supports WebSockets, including same-origin
development-server connections. Deploy and serve a project with existing
`repo_control` first; this version does not expose arbitrary `/lab` ports.

## Server setup

Agent browser tests can also run independently of the live preview gateway:
build the browser image (step 5), set `STELLAR_BROWSER_CHECKS=1`, and restart
the app. They connect directly to owned Docker projects over an isolated
internal network, so they do not require preview DNS or a public preview
origin. External fonts, images and scripts are blocked by these tests; serve
required assets from the application itself. The embedded live preview still
requires the origins, gateway, DNS and HTTPS setup below.

1. Install the application plus the gateway's locked optional dependencies:

   ```sh
   uv pip sync --python .venv/bin/python deploy/preview/requirements.lock
   ```

2. Add these public settings to `keys.env` (substitute your real hostnames):

   ```dotenv
   STELLAR_APP_ORIGIN=https://new-stellar.stellarai.site
   STELLAR_PREVIEW_ORIGIN=https://preview.stellarai.site
   STELLAR_BROWSER_CHECKS=1
   ```

   Preview and Stellar must use separate, non-overlapping hosts. Sibling
   hosts on the same site are recommended so browser third-party-cookie
   blocking does not interfere. The configured origins must match the
   actual HTTPS addresses. No path, userinfo, wildcard or query is allowed.

3. Point wildcard DNS `*.preview.stellarai.site` to the server and obtain
   TLS for that wildcard. Adapt `nginx.conf.example`; it requires an HTTP
   context map and rate-limit zone. Run `nginx -t` before reloading Nginx.
   Never route preview traffic through the Flask deployment subdomain proxy.

4. Start `python -m deploy.preview.gateway` in the same repository/environment
   as Stellar, or adapt and install `stellar-preview.service`. The gateway
   binds only `127.0.0.1:5051` (`STELLAR_PREVIEW_PORT` overrides it). It needs
   the same database path, Redis database, container prefix and Docker daemon
   as the web app. Restart the web app after changing its configuration so
   its frame policy includes the preview domain. Do not expose port 5051
   directly on the public interface.

5. Build the browser image on the same Docker daemon:

   ```sh
   docker build -f dockerfiles/Dockerfile.browser -t stellar-browser:1.63.0 .
   ```

   `STELLAR_BROWSER_IMAGE` can select a separately built compatible image.
   The included Dockerfile pairs Playwright 1.63.0 with its matching browser
   image, as required by the [Playwright Docker guidance](https://playwright.dev/python/docs/docker).

6. Open **Preview** in the sidebar, choose a running project, or click
   **Preview** on its Deploy card. Reload renews the 15-minute access
   session. **Open tab** exchanges a new ticket for a separate preview tab.
   **Run browser check** stores evidence in the current chat's files.

The standalone gateway and web app must see the database under the same
absolute path; this path scopes Redis capabilities. For a nested/container
deployment, expose the gateway through a dedicated reverse-proxy route to
that container, and keep its database/Redis/Docker configuration consistent.
The example systemd service assumes a direct VPS installation; adapt paths
and service users rather than installing it unchanged inside another app.

## Isolation, lifecycle and limits

- Each project gets `p-<12-hex-project-id>.<preview-host>`. Launch tickets are
  random, single-use, expire after 45 seconds and are not access-logged by
  the gateway. Cookies are host-only, HttpOnly and Secure in HTTPS mode.
- Every request checks current account approval, session epoch, ownership,
  deployment state and live Docker labels/port binding. Unknown, expired,
  stopped, replaced or revoked resources fail closed. Existing streams and
  WebSockets recheck access while active. No Docker port comes from a URL
  supplied by the user or model.
- Framing is allowed only from the configured Stellar origin. The iframe
  allows scripts/forms and its own origin, but has no top-navigation or
  popup permission. The Stellar session and gateway cookie are excluded
  from forwarding. Credentials explicitly supplied by the preview app,
  including Authorization headers, remain scoped to that app. App cookies
  pass only with no Domain attribute and no reserved Stellar name.
  Cross-origin CORS permissions from the app are stripped, and service-worker
  registration is disabled. Apps requiring cross-origin authentication or
  service workers may need their public deployed URL instead.
- Uploads are capped at 8 MiB; upstream connect timeout is 5 seconds and
  read-idle timeout 60 seconds. The gateway admits 32 proxied requests in
  total and 8 per project. WebSocket messages are capped at 1 MiB. Nginx
  applies a separate request rate limit before authentication.
- Browser checks use an internal network, resource-capped disposable
  container, non-root user, no host volumes, no Docker socket and only
  declarative bounded steps. Cross-origin HTTP and WebSocket requests are
  blocked. Service workers and downloads are disabled. Missing/blocked
  dependencies and failed assertions are reported as failures, not passes.
- The browser image uses Playwright's default Chromium sandbox setting;
  Docker, network and resource isolation are the boundary here. This is
  not a hardened browser service for arbitrary public websites.
- One browser check per account and two per Stellar instance may run at once,
  with ten launches per account per hour.
  Runner wait is capped at 100 seconds. Cleanup runs on all normal exception
  paths; uniquely named resources left by a crashed worker or lost Docker
  response have expiry labels and are reaped on the next browser launch
  after five minutes. A Docker outage can delay cleanup until recovery.
- A screenshot records the app as displayed, including any secrets the app
  itself renders. Reports redact configured secrets from text and remain
  behind chat ownership checks. Avoid putting credentials into test steps.
- Browser tests cover immediate page behavior, not an entire application's
  workflows. The UI button runs a smoke check; ask the agent to use
  `browser_test` with explicit interactions/assertions for deeper checks.

Some apps deny embedding through their own policy or hardcode a different
WebSocket/public origin. Set their development-server allowed-host and HMR
origin options for the project preview hostname. Browser third-party-cookie
policies can still prevent embedding across unrelated sites; use Open tab
or the public deployment link in that case. The pane cannot inspect a
cross-origin iframe's contents and therefore never equates its load event
with an app health or test pass.

## Tests

```sh
python tests/test_preview.py
python tests/test_preview_app.py
python tests/browser_integration.py
python tests/preview_ui_integration.py
python tests/preview_end_to_end.py
```

The first two test capabilities, Flask authorization and a real loopback
HTTP/WebSocket gateway against controlled fixtures. The integration tests
require Docker and the built browser image; they run real Chromium. The
combined end-to-end test uses real Redis and HTTPS for the complete launch
and browser-evidence path. Existing
backend, frontend and Docker smoke checks must also pass. No production
configuration or data is changed by these tests.
