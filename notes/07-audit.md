# Project audit

A full review of the project: six parallel reviewers over the core engine,
the Phase 8 tools, the chess subsystem, the front end and security, the
Phase 9 deployment work, and a sweep for loose ends. Roughly eighty
findings came back. This note records what was wrong, what was fixed, and
what was deliberately left.

Every fix below is covered by a regression check in `smoke_test.py`, which
went from 194 to 208 checks. Every one of these defects failed silently:
not one of them raised an error anyone would have seen.

## The two that mattered most

**A redirect walked straight past the SSRF guard.** `fetch_url` validated
the URL it was given and then let the HTTP library follow redirects
wherever they led. A page the agent reads could say "look at
`http://example.com/x`", and that host could answer with a redirect to the
cloud metadata service or to Redis on localhost. The body came back to the
model and into the transcript. Redirects are now followed one hop at a
time, and every hop is re-checked. Capped at five.

**Files the model wrote were served as web pages on the app's own origin.**
`.html`, `.htm` and `.svg` were in the inline set, so a file produced by
`lab_execute` and shared with `manage_files` opened as a page at
`/api/outputs/...` with the user's cookies. A script in it could read every
chat and post as the user. That is exactly the hole the sandboxed widget
iframe exists to close, reopened by the file server. Those three types now
download instead, and everything served from there carries `nosniff` and a
policy that denies scripting.

## Correctness

**A stream gave up while its own work was still running.** The idle
timeout was two minutes; a tool may block for ten. Thinking for three
minutes over a chess move, or running a long sandbox command, ended with
"Stream timed out." in the browser while the turn carried on invisibly on
the server. The timeout now exceeds the longest a tool can block. Dead
clients are still noticed within ten seconds, by the keepalive write
failing.

**A dropped connection printed the reply twice.** The resume index came
from the query string, which the browser always sends as zero, rather than
from the `Last-Event-ID` header, which is where the browser records how far
it actually got. Every reconnect replayed the turn into the same bubble.
The header now wins.

**The timeout frame stole an event's place in the queue.** It was numbered
with the next unread index but never stored, so a correct resume skipped
the real event that later took that number. It now carries no id at all.

**An overloaded model was treated as a network blip.** Google's overload
response also contains "503" and "unavailable", and the transient check ran
first, so the model-switch path was unreachable: the turn retried the same
overloaded model twice and died. Overload is now detected first.

**An older database upgraded into a broken state and reported itself
clean.** `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
exists, so columns added in later phases never appeared; an index on one of
them aborted the upgrade, and the drift check then said everything was
fine. Core tables now migrate column by column, and existing rows are
backfilled. Verified by upgrading a Phase 1 database and reading its data
back.

**The migration threw away what it copied.** `ALTER TABLE` commits itself
but the `UPDATE` that copied legacy values did not, and nothing committed
it. Every pre-existing scheduled task ended up with a null run time, never
matched "due", and never fired again, and the repair could not re-run
because the column now existed. It commits.

**The scheduler could not see its own table.** A database migrated by an
earlier build had no `lock_id`, which is named in the first statement of
every scheduler tick. The error was swallowed at debug level, so the
scheduler ran nothing and said nothing, forever. The column is added, and
the failure is now logged at warning.

**One bad run ended a repeating task permanently.** Only a successful run
re-armed a recurring task; any failure wrote a terminal status, and the
scheduler only ever claims pending rows. A single transient error silently
ended a daily digest. A recurring task now re-arms either way, and a
failure is logged.

**A task that could not start blocked every task behind it.** An exception
from the launch escaped the loop with the row already marked running, so it
sat claimed for thirty minutes and the rest of the tick never happened. The
claim is handed straight back and the loop continues.

**Sharing a file could destroy an earlier one.** Collisions were detected
by comparing file sizes, so two different four-byte files counted as the
same file and the second share overwrote the first, breaking a link the
user already had. Compared by content now.

## Chess

**Accuracy was computed on the wrong scale.** The curve takes a drop in win
percentage, between nought and a hundred; it was being fed centipawn loss.
An ordinary casual game came out as nought percent for both players, and
those numbers went on the end-of-game card and into the model's summary.
Each grade now records the win-percentage drop, and a well-played game
scores in the nineties.

**The board was mirrored.** a1 was drawn light. On a real board a1 is dark.

**A promotion invented a captured pawn.** Material was counted by comparing
against the starting line-up, so a promoted pawn read as captured while the
extra queen was clamped away: the tray showed a pawn nobody took and the
balance was out by nine. Captures are now counted from the move history.

**Stellar could not lose on time.** Its clock was charged but never
checked, so it played on from a displayed zero while the user's flag still
forfeited normally. Either side can now flag.

**A dying engine was orphaned.** When Stockfish misbehaved the reference was
dropped without quitting it, and the cleanup only kills a reference it
still holds, so a hung engine lived as long as the worker. It is quit
first.

**The fallback engine spoke a different score scale.** Its mate scores were
ten times the reviewer's, so a game-losing move could compute a negative
loss and be graded "best". Converted, and a mate is now reported as a mate.

## Smaller

- Login `next=` accepted `/\evil.com`, which browsers read as `//evil.com`:
  an off-site redirect from a link showing the real hostname. Backslashes
  are refused.
- Shared filenames were not URL-encoded, so a name with a space produced a
  link every Markdown renderer cut short.
- Truncating a long filename removed its extension, and the file server
  decides inline-versus-download by extension, so a long-named picture
  arrived as an unopenable download. The extension is kept.
- Paging past the end of a keyword search returned an inverted range and an
  empty body instead of saying so.
- Deployed containers had no process limit, though the lab container does.
  A fork bomb in a deployed app would exhaust the host.
- A snapshot followed symlinks out of the workspace, so the model could
  link the credentials file into its project and have it stored in the
  database and handed back on the next deploy. Symlinks are skipped.
- A status line was double-escaped and would have shown a literal `…`.
- The session cookie had no `Secure` flag. Now set by
  `SESSION_COOKIE_SECURE=1`, off by default so local development works.

## Known and not fixed

These are real, and were judged either too large to fold into an audit pass
or dependent on how the project is actually deployed.

- **Process-local state under multiple workers.** The record of which chat
  is generating lives in one process. Cancellation already has a Redis
  bridge; the follow-up injection route and the scheduler's "is this chat
  busy" check do not. Under the four workers the deploy unit configures,
  most follow-ups would fail and a scheduled task could run alongside a
  live turn in the same chat. This needs the same Redis treatment
  cancellation got.
- **Guest apps share a domain with the app.** Deployments get a subdomain
  of the main site, the session cookie is `Lax`, and there are no CSRF
  tokens, so a generated app can set a cookie for the parent domain or post
  to it with the visitor's session. The real fix is a separate domain for
  guest apps.
- **Sandbox containers reach host loopback.** The network is an ordinary
  bridge, so code in the sandbox can reach Redis and the metadata service
  directly, which is the SSRF guard bypassed by another route.
- **No disk quota and no container ceiling.** One command can fill the
  host filesystem, which is the same filesystem as the database.
- **Widget answers are not checked for ownership.** Anyone who learns a
  widget's id can answer someone else's widget. The id is a random uuid, so
  this is a secret, not a permission.
- **`debug=True` is hardcoded** in the development entry point, so the
  Werkzeug console is live on loopback.
- **Pinned dependencies drift from what is installed.** Six pins are behind
  the working environment, so a fresh install produces a combination nobody
  has run.
- **Dead ends**: a synchronous message route the UI never calls that still
  uses Phase 4 key handling, two write-only deployment caches, columns that
  are never written, and two `repo_control` arguments that are advertised
  to the model and ignored.
