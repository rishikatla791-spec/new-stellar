# Phase 11: terminals

Two ways into the same sandbox the agent already uses: a terminal drawer
in the browser, and an SSH gateway you reach with an ordinary `ssh`
command. Both give you a real interactive shell in the chat's container.

## What a terminal actually is

`lab_execute` runs one command and hands back its output. A terminal is a
different thing: a conversation of keystrokes and screen updates with a
program that believes a person is sitting at a keyboard. That belief comes
from a PTY, a pseudo-terminal. With one, bash shows a prompt and colours,
line editing and Ctrl+C work, and full-screen programs like `top` and
`vim` can draw. Without one, none of that happens.

Docker provides it: an exec created with `tty=True` and started with
`socket=True` returns a raw two-way byte stream into a bash inside the
container. Bytes written to it are keystrokes. Bytes read from it are
the screen.

In the browser, xterm.js is the other half. It turns keystrokes into bytes
and turns the stream of bytes, including the escape codes for colour and
cursor movement, back into a drawn screen.

## Carrying it across four workers

The socket into the shell can only live in one process. But a browser's
stream and its keystrokes can land on any of the four Gunicorn workers.
So nothing reaches the socket directly; everything goes through Redis
pub/sub, the same trick cancellation uses:

| Channel | Carries |
|---|---|
| `term_out:{chat}` | the screen, from the shell to every watching browser |
| `term_in:{chat}` | keystrokes, from any worker to the shell |
| `term_ctrl:{chat}` | resize and close |

The screen travels base64-encoded, because a 4,096-byte read can cut a
multi-byte character in half and Server-Sent Events only carry text. The
browser turns it back into bytes before xterm decodes it.

**Which worker owns the shell is recorded in Redis**, under
`term_owner:{chat}`, claimed with SET NX and refreshed every ten seconds.
Every other worker only relays. If the owner dies, its claim lapses within
thirty seconds and the next stream to notice starts a fresh shell.

## The SSH gateway

`ssh_gateway.py` is a separate process, an SSH server built on paramiko,
listening on port 2222. It accepts any connection without a password,
because the password is not how you prove who you are. Instead:

1. You run `ssh anything@host -p 2222`.
2. The gateway prints a one-time code and a link.
3. You open the link while logged in to Stellar and press Approve.
4. The gateway attaches your SSH session to a shell in the sandbox of your
   most recent chat.

This is the device authorization pattern: the same thing that happens
when you sign a television in to a streaming service. It moves the
proof of identity to a place where you are already signed in.

It is only as safe as that approval step, which is why the approval is
the part that got the most attention.

## What the review found and fixed

The terminal worked well in one process: a real shell, resize, Ctrl+C,
correct characters from the server. The problems were all about more
than one of something.

- **One keystroke ran in two shells.** Ownership was decided inside each
  process, so a reconnect that landed on a different worker started a
  second shell on the same channels. Reproduced with two processes: two
  shell ids answered one `echo`. Fixed with the Redis claim above.
- **Opening the approval link approved it.** The page submitted itself
  whenever it was opened with a code, and the gateway prints exactly that
  link. Anyone could SSH in, send a logged-in user the link, and get a
  root shell in that user's sandbox the moment it opened. Approval is now
  an explicit click on a page that shows where the request came from; a
  code can be decided once; and Deny works.
- **Codes were four hex characters**, 65,536 possibilities. Now eight.
- **The gateway listened on every network interface** by default. Now
  loopback, unless `STELLAR_SSH_HOST` says otherwise.
- **Characters came out garbled in the browser.** `atob()` produces one
  character per byte, so every multi-byte character was mangled. The
  bytes are now handed to xterm as bytes.
- **Closed shells never died.** An interactive bash ignores SIGTERM, and
  Docker Desktop's proxy hides a dropped connection, so neither a kill nor
  a disconnect ended the shell. Shells now get SIGHUP, the hang-up signal
  a real terminal sends, and they are named so an orphan can be found.
- **Closing reopened the terminal ten seconds later**, because the
  takeover logic could not tell a deliberate close from a crash. A
  deliberate end is announced; only a silent death triggers takeover.
- **The terminal opened blank until you pressed Enter.** The stream
  subscribed after starting the shell, so the first prompt went nowhere.
- `paramiko` was imported but not declared, so a fresh install could not
  start the gateway.

## How it was proved

Two app processes sharing Redis and a database, which is what two
Gunicorn workers are, driven by scripts rather than by eye:

- a real shell answering arithmetic, UTF-8 intact, resize and Ctrl+C;
- one shell id answering after a reconnect on the other process;
- a prompt appearing unprompted, a close that ends the shell and stays
  ended, and a killed owner replaced by a new shell with the dead one's
  shell swept away;
- a real SSH client through the whole device flow: the link approving
  nothing, the page showing the origin, approval giving a working shell,
  a second decision refused, and a denial turning the session away in a
  second;
- the browser terminal drawing `│ → 🚀 café` correctly.

The suite gained fifteen checks for the parts that do not need Docker.
