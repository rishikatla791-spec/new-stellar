# The chess feature: what it is and how to build it elsewhere

This note is written so that the feature can be rebuilt in another project
without reading Stellar's code. It describes what the user sees, the three
parts that make it, the exact data that flows between them, the grading
maths, and a porting checklist. File names refer to this repo; the ideas
transfer to any chat app that can show HTML to the user and wait for a
reply.

## 1. What the user gets

- One chessboard inside the chat. It is drawn once and then updated in
  place for every move: pieces slide, captures fade, the eval bar breathes,
  the move list grows. The page never reloads and no second board appears.
- Moves by click or by drag. Legal targets shown as dots (empty square) or
  rings (capture). A promotion chooser. Last move highlighted. A king in
  check glows red. The board is flipped when the user plays black.
- A Stockfish opponent at a chosen strength, 1320 to 3190 elo (default
  2000). The model maps words to numbers: beginner 1400, casual 1700, club
  2000, strong 2400, master 2800.
- A real clock, 10 minutes per side by default, untimed with `minutes=0`.
  The server keeps the time. Running out loses.
- Live grading of every move on both sides, in the vocabulary chess sites
  use: Brilliant `!!`, Great `!`, Best, Excellent, Good, Inaccuracy `?!`,
  Mistake `?`, Blunder `??`, Forced. Shown as a verdict line under the
  status ("Qh5?? Blunder - Nxe5 was better"), as symbols in the move list,
  as an eval bar beside the board, and as an arrow on the board for the
  better move after a user mistake.
- At the end: an overlay card with the result, accuracy per side and the
  count of each grade. The model receives the same review as text and
  narrates it like a coach, then offers a rematch.
- Synthesised sounds for move, capture, check, mistake, brilliancy and game
  end, with a mute button. Animations are disabled for users who ask their
  OS for reduced motion.

## 2. The three parts

| Part | File | Job |
|---|---|---|
| Rules, engines, grading | `chess_engine.py` | Pure Python. No Flask, no Redis. Wraps python-chess and Stockfish; grades moves; computes accuracy and the review. |
| The widget | `chess_ui.py` | Turns a position into HTML, CSS and JS. One function, `board_data()`, produces the dict the widget draws from; `render()` wraps it in the template. |
| The game loop | `chess_play` in `app.py` | A tool the model calls once. It runs the whole game on the server, talking to the widget and the engines, and returns to the model only when words are needed. |

The model is not in the loop per move. Its whole instruction is "call
`chess_play`". This is what makes the feature fast and legal: a language
model narrating a board it tracks itself plays illegal moves and takes
fifteen seconds to do it. Here the board is authoritative and the model
only speaks at four moments: game over, the user asked a question, the user
resigned, or a new game started.

## 3. Data flow for one move

1. The loop shows the board with `waiting=true` and the list of legal moves
   in UCI ("e2e4"). It records the wall time.
2. The user clicks or drags. The widget applies the move locally at once
   (including the rook of a castle, the pawn of an en passant, the promoted
   piece), plays the move sound, shows "Thinking...", and calls
   `window.stellar.finish({move: "e2e4"})`. That message reaches the server
   through the generic widget channel (a Redis list the loop is blocked on).
3. The server validates the move against `board.legal_moves`. A move that
   is not legal is refused with a status line and the board is shown again.
   The client is never trusted.
4. The user's clock is charged the elapsed time. If it went to zero, the
   user forfeits.
5. Two things happen at the same time, in two different engine processes:
   the reviewer (full strength) evaluates the position after the user's
   move, and the player (elo limited) picks its reply. Running them in
   parallel means grading costs the user no waiting.
6. The reply is appended. The reviewer evaluates the new position. Both
   pending moves are graded (the user's and the engine's).
7. One update is sent carrying the whole position: FEN, both grades, the
   eval, the clocks, the move list. The widget slides the engine's piece,
   plays the right sound, draws the verdict, and, if the user just
   blundered, an arrow for the better move.

Measured on this machine: about 400 ms from the user's click to the reply
on screen, grading included.

## 4. The engines

- **Referee**: python-chess. It generates legal moves, applies them,
  detects mate, stalemate, repetition, fifty moves and insufficient
  material. Nothing else decides legality.
- **Player**: Stockfish through the UCI protocol with
  `UCI_LimitStrength=true` and `UCI_Elo=<n>`. Think time by strength:
  0.12 s up to 1800, 0.25 s up to 2400, 0.5 s above.
- **Reviewer**: a second Stockfish process with no strength limit,
  0.1 s per position, `MultiPV=2` so it also reports the second-best line.
  The judge must be stronger than the player, or a 1400 opponent would
  grade its own blunders as best moves.
- Both processes are opened once per game (`EngineSession` context
  managers) and reused for every move. Spawning Stockfish per move would
  cost more than the search itself.
- **Fallback**: if no Stockfish binary is found, a pure-Python alpha-beta
  search with quiescence and piece-square tables plays instead. It is much
  weaker and slower but the feature keeps working.

The binary lives in `engines/` (gitignored); `setup_engine.py` downloads it.

## 5. Grading, exactly

Every position after `i` plies has a reviewer record `analysis[i]`:

```
{"cp": int,          # White's point of view, centipawns
 "mate": int|None,   # moves to mate, signed, or None
 "best": "e2e4",     # the engine's first choice, UCI
 "second_cp": int}   # eval of the second-best move, or None
```

Mates are folded into `cp` as +-(10000 - n) so a mate in 1 outranks a mate
in 5 and both outrank any material. A checkmated position is +-10000 with
`mate: 0`; a stalemate is 0.

To grade move `i` (played from position `i` into position `i+1`), with
`sign = +1` for White and `-1` for Black:

```
best_eval  = analysis[i].cp   * sign     # what the mover could have had
after_eval = analysis[i+1].cp * sign     # what the mover got
loss       = max(0, best_eval - after_eval)
```

| Condition | Grade |
|---|---|
| Only one legal move | Forced |
| Move equals `analysis[i].best` (or `loss == 0`), it is a sacrifice, and `after_eval > -150` | Brilliant |
| Move is best and the second-best move is at least 120 cp worse | Great |
| Move is best (otherwise) | Best |
| `loss <= 25` | Excellent |
| `loss <= 60` | Good |
| `loss <= 110` | Inaccuracy |
| `loss <= 250` | Mistake |
| else | Blunder |

Each grade record is `{"cls", "loss", "best_uci", "best_san"}` so the UI
can say what was better without asking the engine again.

**Sacrifice** (for Brilliant): the moved piece is not a pawn or king; it
gave up at least 200 cp more than it captured; after the move an enemy
piece can take it and either nothing defends it or the cheapest attacker is
worth less than it. This is a heuristic, deliberately simple.

**Accuracy** per side uses the lichess curve on average centipawn loss
(ACPL) over the side's non-forced moves:

```
accuracy = clamp(103.1668 * exp(-0.04354 * ACPL) - 3.1669, 0, 100)
```

**Eval bar** height is a win probability, also from lichess:

```
white% = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)
```

clamped to 3..97 so a sliver of each colour always shows; a mate score
pins it to 0 or 100. The bar flips with the board.

**Review** at game end, per side: accuracy, a count per grade, the three
costliest moves with the better alternative, and the Brilliant and Great
moves as highlights. The same dict goes to the widget's end card and, as a
sentence or two, to the model.

## 6. Game state

One Redis key per chat, `chessgame:{chat_id}`, seven-day expiry:

```
{"moves":    ["e2e4", "c7c5", ...],   # UCI, the only source of truth
 "elo":      2000,
 "user":     "white",
 "widget":   "<interaction id>",       # the frame being updated
 "clock":    {"white": 571000, "black": 588000} | null,   # ms
 "forfeit":  null | "white" | "black",
 "analysis": [ ...one record per position... ],
 "quality":  [ ...one record per move... ]}
```

The board is rebuilt from `moves` at the top of every loop iteration. That
is cheap and means a resumed game, a page reload, or a crash mid-move can
never leave a board that disagrees with its history.

## 7. The widget protocol

The widget is an iframe with `sandbox="allow-scripts"` only: no same-origin
access, no cookies, no network to the app. It talks through `postMessage`.

Server to client:

- First render: the full HTML with the position JSON inlined as `__DATA__`.
- Every later move: `{__stellar: "update", data}` posted into the same
  frame. The host page turns that into a `stellar:update` DOM event; the
  widget's script reloads the dict and re-renders in place. The full HTML
  is still sent alongside for a client that lost the frame (page reload).

The data dict, `board_data()`:

```
fen, turn, user, elo, legal[], last, check, moves[] (SAN), status,
waiting, over, result, capW[], capB[], balance, clock{white,black,ticking},
quality[], eval{cp,mate,text}, review
```

Client to server, always through `window.stellar.finish(...)`, exactly one
message per shown board:

| Message | Meaning |
|---|---|
| `{move: "e7e8q"}` | a move, validated server side |
| `{ask: "is my king safe?"}` | returns to the model with the FEN and move list; the game resumes on the same frame afterwards |
| `{exit: true, resign: true}` | resign; review text goes to the model |
| `{exit: true}` | close the board (game stays saved) |
| `{newgame: true}` | discard and start again |
| `{flag: true}` | the client's clock hit zero. Honoured only if the server's own count agrees within 500 ms, so a forged flag cannot end a game and network skew cannot lose one |

## 8. What makes it feel fast and smooth

- The user's move is applied locally the instant it is clicked, before the
  server has seen it. The server's single reply carries both moves.
- One render per move pair. Dozens of waits on the same widget id, never a
  new frame (`_await_widget(close=False)`).
- Grading runs in a thread while the player engine thinks.
- Pieces move with a 220 ms eased transform from the origin square; the
  captured piece shrinks and fades; the check glow pulses once; the verdict
  and the new move-list row fade in; the arrow fades out after three
  seconds; the eval bar animates its height over 700 ms. All CSS, no
  animation library.
- Sounds are two-oscillator envelopes from the Web Audio API: no assets,
  no network, no autoplay problems because the first sound follows a click.
- Pieces are the Cburnett SVG set (the one lichess uses) inlined as
  twelve path strings, so every machine draws the same board and white is
  unmistakably white. Font glyphs were tried first and failed at exactly
  that.

## 9. Porting checklist

You need: Python with `chess` (python-chess), a Stockfish binary, and a
chat host that can (a) push an HTML widget to the user, (b) block a
server-side function until the widget replies, (c) push a JSON update into
the same widget. In Stellar (b) and (c) are Redis lists and Server-Sent
Events; any queue and any live channel will do.

1. Copy `chess_engine.py` whole. It has no dependencies on the rest of the
   app. Point `find_stockfish()` at your binary.
2. Copy `chess_ui.py` whole. It only needs `chess`. Its template expects
   the host to define `window.stellar.finish(obj)` and to dispatch a
   `stellar:update` event with the new data dict; the CSS reads the host's
   colour variables (`--surface`, `--border`, `--text`, `--text-dim`,
   `--accent`, `--good`, `--bad`, `--font`, `--mono`), so define them or
   replace them.
3. Rewrite `chess_play` against your own show/await primitives. Keep the
   shape: replay from the move list, check forfeit and game over, engine
   turn, else show and wait, then dispatch on the reply type. Keep the
   server as the only judge of legality and time.
4. Store state somewhere with expiry, keyed by conversation.
5. Tell the model one thing in its system prompt: to play chess, call the
   tool; never draw a board itself; never track moves itself; and how to
   map words to elo.
6. Test with a simulated clicker before a human: a script that reads each
   shown board's `legal` list and answers with a random move measures the
   click-to-reply latency and exercises every branch (illegal move, forged
   flag, ask, resign, resume, untimed). Ours is 34 checks and runs in a
   minute.

## 10. Mistakes made on the way (so you can skip them)

- A local `import chess.engine` inside a method makes `chess` a local name
  for that whole method, and any `chess.WHITE` above it raises
  `UnboundLocalError`. Import at module level.
- SAN for a principal variation must be computed as the line advances,
  not against the root position.
- Search code that pushes a move must pop it in a `finally`, or a timeout
  leaves the board corrupted.
- The grading engine must not be the elo-limited one.
- Never let the client decide that a move was legal or that a clock ran
  out.
- One widget per game, updated in place. A new frame per move flashes and
  scrolls the chat.
