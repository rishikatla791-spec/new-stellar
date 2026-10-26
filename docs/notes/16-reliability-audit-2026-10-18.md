# Reliability audit — 18 October 2026

## Scope and baseline

This contribution is based on `rishikatla791-spec/new-stellar` at
`328c0cf`. The existing checkout contained substantial uncommitted work;
it was preserved, and these fixes were made on `codex/reliability-audit`
in a separate managed checkout. Nothing has been merged or deployed by
this audit.

The upstream `N1kky-wed/Stellar` tree at `8472ce3` was compared with the
fork. Its agent/tool architecture differs substantially; it is not a
drop-in update to this fork. Importing it wholesale would require a
separate migration and regression pass.

## Fixed defects

| Problem | Result after this change |
|---|---|
| Shell, search, image, fetch, email and repository tools could return failure text marked as a successful call | Explicit failure returns in these tools use `ToolFailure`; the agent receives `is_error` and the UI records failure. Ordinary text mentioning errors is not guessed to be failure. |
| A tool's own `TypeError` was confused with invalid model arguments | Arguments are bound against the signature before invocation; internal exceptions remain execution failures. |
| A failed clone could proceed as if deployment had succeeded | Clone exit status is checked for both initial deployment and redeployment. |
| An unsuccessful stop could still mark an app stopped | Failed stops retain the prior recorded state. |
| Starting a server without a working HTTP response could report success | No response or a server-side HTTP error is reported as failure. |
| Concurrent terminal input requests could reorder typed text or pasted commands | Input is queued, large pastes are chunked without splitting surrogate pairs, and old input is discarded after errors or session changes. Stale connection completions are ignored. |
| A proxy returning HTML with HTTP 200 could masquerade as an API success | The browser rejects unexpected non-JSON success responses. |
| Bad JSON shapes and invalid chat field types could raise server errors | Mutation requests reject malformed/non-object JSON; chat names, messages and attachment lists are validated. |
| API HTTP exceptions could return HTML or lose useful headers | API errors return JSON, preserve headers such as `Allow`, and hide internal 500 details. |
| Auth/API responses could be cached | Auth/API responses use private/no-store caching headers; deployed app responses keep their own behavior. |
| Malformed or misleading Origin values could reach unsafe parsing paths | Origin syntax is validated while retaining exact-host and configured public-domain checks. CSRF protection remains enabled. |
| Nginx did not overwrite the forwarded host | The proxy snippet now forwards the actual request host consistently. Existing deployments must install/reload that configuration themselves. |
| Configured secrets could leak through tool arguments, stored results or historical previews | Tool arguments/results are redacted before persistence; legacy previews and tool context are redacted on read. Existing raw database records are not rewritten. |
| Commit reporting assumed `.git` was a directory | Health reporting supports worktree/submodule git files, common refs, packed refs and detached HEADs. |
| The frontend builder installed a vulnerable, unused watcher dependency chain | The one-shot build uses Tailwind's compiler/scanner directly. Generated CSS is byte-for-byte identical to the former CLI build; the watcher, micromatch and braces chain is removed. |

The build dependency finding was
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).
There was no patched braces release listed at audit time. Removing the
unneeded chain avoids that dependency rather than suppressing the alert.

`ToolFailure` migration covers the tools listed above. Other registered
tools still have legacy textual outcomes; this is not a claim that every
possible tool response now has a structured error category.

## Verification

| Check | Observed result |
|---|---|
| Full Linux smoke suite with real Docker and Redis | **720 passed, 0 failed, 0 skipped** |
| New offline backend regressions | **17 passed** |
| New frontend behavior regressions | **6 passed** |
| Python source parsing, main.js syntax and diff whitespace | Passed |
| Locked Python dependency audit, Windows and Linux | No known vulnerabilities reported |
| Clean npm install, production GenUI build and npm audit | Build passed; zero vulnerabilities reported |
| Former CLI build versus replacement compiler | Identical CSS SHA-256 |
| Live Gemini model discovery and one short text request | 63 models discovered; `gemini-3.5-flash-lite` returned the requested `OK` |

CI now runs the regression tests with the full suite, and separately
installs, audits and rebuilds GenUI, checking committed generated output.
Tests and dependency audits describe the code/dependency versions and
environment checked; they do not certify that no defects remain.

Useful commands from the repository root, with a disposable Redis configured:

```sh
python tests/test_reliability.py
node tests/frontend_reliability.cjs
STELLAR_REQUIRE_DOCKER=1 python smoke_test.py
cd genui
npm ci
npm audit --audit-level=high
npm run build
```

The full Docker run was performed in native Linux under WSL because the
Windows Docker Desktop backend had an existing stale-socket startup
failure. The native Linux checkout used the same committed fixes.
No live email delivery, YouTube integration or image generation was
validated. Existing live credentials were not copied into the contribution.

## Hosted availability: still requires an operator action

At audit time, `https://stellarai.site/` returned HTTP 200. Its `/healthz`,
`/readyz` and `/auth/register` paths returned 404; it should not be treated
as the same Flask application as the child deployment.

The child `https://new-stellar.stellarai.site/`, including `/healthz`,
`/readyz` and `/auth/register`, returned HTTP 503 with:

> Application 'new-stellar' is stopped or unavailable. Start it in Repo Control.

This response is from the parent deployment router, before the child login
page is available. An account email does not provide server access or fix
that state. No production login, restart or deployment was performed.

Operator recovery steps:

1. Open the parent Stellar instance's Repo Control and locate `new-stellar`.
2. Inspect container state and logs. Restart the deployment if stopped;
   inspect the application's startup command if the container starts but
   its HTTP server does not. Restarting a container alone may not launch
   an application that was started manually inside it.
3. Check Redis connectivity, database persistence, the configured port,
   environment loading and Gunicorn startup from the deployment logs.
   Do not print secret values into logs.
4. Require HTTP 200 from the child `/healthz` and `/readyz`, then test
   Google sign-in from its public HTTPS domain. Confirm that domain is
   authorized in Firebase and that proxy host/scheme forwarding is correct.
5. Put Redis/Gunicorn and the deployment startup under a process supervisor
   or reproducible container entrypoint. `deploy/auto_pull.py` describes
   the nested-app setup; manually started processes and the polling script
   are not a durable replacement for startup supervision.

## Highest-value next upgrades

These extend existing sandboxes, plans, checkpoints, widgets and background
jobs instead of adding another generic shell wrapper.

1. **Browser testing tool:** real browser navigation, accessible DOM
   inspection, screenshots, console/network failures and test assertions.
   Attach evidence to each completed change so the agent can catch a
   broken UI or login redirect itself.
2. **Live app preview beside chat:** route a sandbox's development server
   through a separate isolated origin, including WebSocket support, with
   explicit open/restart/log controls and an external-browser option.
3. **Precise repository edits:** project-scoped symbol/text search, bounded
   file reads and validated patch application. Show the resulting diff
   and use existing checkpoints for rollback.
4. **Verification before completion:** a `run_checks` tool that records
   test/build/lint commands, exit codes and artifacts. Connect
   `report_done` to that evidence without claiming unrun checks passed.
5. **Durable tool traces:** finish migrating legacy tool outcomes to an
   explicit result envelope with failure categories, retryability,
   duration and redacted evidence. Retry transient reads with bounded
   backoff; use idempotency keys before retrying side effects.
6. **Scoped integrations and evaluation:** add approved MCP integrations
   with per-user authorization, and maintain fixture tasks measuring
   task success, regressions, latency and cost. Tool count alone does not
   establish reliability or parity with another coding product.
