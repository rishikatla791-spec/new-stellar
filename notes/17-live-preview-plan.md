# Live preview and browser verification

## Delivery plan

1. Reuse owned deployed projects. Add an authenticated, CSRF-protected launch
   endpoint; do not accept arbitrary upstream URLs or ports from the browser.
2. Run an optional asynchronous preview gateway on loopback behind Nginx.
   Give each project its own preview hostname on a separate configured
   domain. Exchange a short-lived, single-use Redis ticket for a host-only
   preview cookie. Recheck account approval, session epoch, project ownership,
   running state and Docker labels before forwarding. Fail closed when a
   dependency is unavailable.
3. Proxy streaming HTTP and WebSocket traffic with bounded concurrency,
   body size, connection and idle timeouts. Strip gateway credentials and
   hop-by-hop headers. Never forward the Stellar login cookie. Set framing
   policy to the exact configured Stellar origin.
4. Add a responsive preview pane beside chat with project selection, reload,
   external-open and close controls. Display launch/configuration errors;
   discard stale asynchronous launches. Opening a preview never wakes or
   restarts an app without the existing deployment controls.
5. Add a `browser_test` tool with bounded, declarative interactions and
   assertions. Run Chromium in a disposable container on a temporary
   internal Docker network connected only to the owned target app. Capture
   screenshot, console errors, HTTP failures and assertions as owned chat
   artifacts. No arbitrary remote URLs, JavaScript evaluation, host browser
   profiles or Docker socket inside the runner.
6. Test ownership, CSRF, expired/replayed tickets, account revocation,
   mismatched hosts, stopped/replaced containers, malformed inputs,
   streaming/WS behavior, upstream failures, concurrency and teardown.
   Run real browser fixtures for successful and failing interactions,
   blocked cross-origin traffic, screenshots and resource cleanup. Run
   existing regression and smoke suites before contributing the change.

## Deployment and acceptance

The feature is optional: existing chat/deploy behavior must work without
preview configuration or a browser image. Missing dependencies must produce
actionable messages, never a false test pass. Configure wildcard DNS/TLS
and the gateway service before enabling previews in production; build the
browser image before offering browser checks. Keep implementation and
deployment reviewable in a pull request. No production access is assumed.

Acceptance requires a working embedded preview and external tab, a working
WebSocket echo through the gateway, successful and deliberately failing
browser tests with evidence, negative isolation checks, and passing existing
tests. This plan cannot cover every possible application or prove absence
of defects; the final report must state exactly what was exercised.
