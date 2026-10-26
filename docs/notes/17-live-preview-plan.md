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

## Implemented and checked

- The pane, one-use launch API, HTTP/WebSocket gateway, bounded browser tool,
  evidence links, deployment examples and optional locked dependencies are implemented.
- 17 capability/gateway regressions and 8 Flask/API regressions pass. These
  include expired/replayed capabilities, revocation, wrong ownership/host,
  stopped/replaced containers, CSRF, upload limits, upstream failure, socket
  expiry, resource cleanup and feature-disabled behavior.
- Real Chromium fixtures pass for desktop/mobile interaction, deliberate
  assertion failure, console failure, HTTP failure, blocked external fetch,
  external HTTPS redirect and metadata-IP redirect, followed by leak checks.
- The actual Stellar page passes desktop/mobile preview controls, separate
  tabs with no opener, evidence rendering, gateway errors and stale launch
  cancellation. Screenshots are generated for inspection and uploaded by CI.
- A combined HTTPS test uses the actual Flask API, real Redis, gateway and
  owned Docker container: frame interactions work, secure host-only cookies
  are exchanged, and the browser tool saves a passing owned report/screenshot.
- The existing 720-check Docker/Redis smoke suite, 17 backend regressions
  and 6 frontend regressions pass. The optional dependency lock audit reports
  no known vulnerabilities at the time checked.

The redirect fixtures exposed a limitation of Playwright's request routing
alone. The runner now also uses a fixed origin-checking HTTP proxy, so a
redirect cannot bypass the origin boundary. Tests also exposed Docker log
tail filtering: a late stderr line could hide the JSON stdout record. The
controller now reads a bounded tail containing the report.

Not deployed to the public VPS by this contribution. See
[`deploy/preview/README.md`](../deploy/preview/README.md) for activation,
limits and compatibility notes. TLS/browser-policy behavior was exercised
in Chromium with local test certificates; production DNS, certificate
renewal, Nginx and other browsers still require deployment validation.
