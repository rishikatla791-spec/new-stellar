# Checkpointed application builds

An imperative request to build/create/implement and deploy an application uses
the build workflow in Develop mode. Advice questions remain ordinary chat.

Builds get up to 96 model steps and a 30-minute execution window, checked between
steps, with durable SQLite progress every step and a visible checkpoint every
eight steps. These bounds replace the ordinary eight-step cutoff for builds;
they do not remove resource limits or bypass provider quotas. A long tool can
finish after the execution window before the next boundary check.

The agent keeps the same in-memory model/tool history during automatic
continuation. Provider key/model fallbacks retain the normal recovery rules.
On provider exhaustion, cancellation, a worker interruption or a remaining-work
limit, the job is blocked/paused instead of marked completed. The owner can use
**Resume build**, or type **continue**, to start a new query from saved project
files, tool history and verification evidence. Previous commands are never
automatically replayed. A worker killed during a side effect requires inspection
of the current project before the agent acts again. There is no unattended retry
daemon after a host reboot; stale running/resuming checkpoints become resumable.

The owner-only build status and resume routes are:

- `GET /api/chats/<id>/build-run`
- `POST /api/chats/<id>/build-run/resume`

The database records metadata, verification phases and the original request,
not credentials or complete tool arguments/results. Existing tool-call records
remain the detailed source of history. Build records cascade when their account
or chat is deleted.

## Completion evidence

For every project touched by the build, the completion gate requires:

1. A successful foreground test command with a positive reported test count.
   Supported forms include `python3 -m unittest`, `pytest`, `python3 test_app.py`
   and `npm test`. Writing a test file, starting a background test process or
   discovering zero tests is insufficient.
2. `repo_control(action='serve', ...)` to persist the server startup command.
3. `repo_control(action='verify', ...)` to check public HTTP and referenced
   same-origin JavaScript/stylesheets. Redirects are restricted to the recorded
   project origins, including its registered runner tunnel. Responses, redirect
   count, assets, time and bytes are bounded. Third-party assets are excluded.

Code execution invalidates previous verification; unrecognized editing commands
also invalidate test evidence. Failed mutations invalidate old verification too.
The model is prompted to perform outstanding steps rather than silently ending.
Repeated no-tool replies eventually pause instead of burning the entire quota.

This gate is a deployment smoke check, not proof of complete application
correctness. Test quality, application-specific API assertions, browser behavior,
database migrations and restart recovery still require explicit tests. The model
must report any unavailable verification. A provider quota block cannot be
repaired by increasing tool budgets.

## Regression coverage

`tests/test_build_workflow.py` covers the real producer beyond eight steps,
verification phases, positive test counts, failed mutations, bounded work,
owner isolation, durable blocked state, duplicate resume refusal, queue failure,
public errors/assets, redirects and response bounds. The operations browser
fixture also exercises safe build status rendering, resume, and a saved final
reply arriving without token events.
