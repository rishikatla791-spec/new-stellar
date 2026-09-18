"""The chessboard widget, rendered by the server from the position.

A board is a pure function of the position. The model is not asked to draw
it; this module does, once, properly, and the same widget is then updated in
place for every move of a game - pieces slide, captures fade, grades pop
onto squares, the eval bar breathes, and nothing ever reloads.

Pieces are the Cburnett set - the open-source vector pieces that lichess and
Wikipedia use - inlined as SVG so they render identically on every machine.

    Piece artwork: Colin M.L. Burnett, CC BY-SA 3.0
    https://commons.wikimedia.org/wiki/Category:SVG_chess_pieces
"""

from __future__ import annotations

import json

import chess

PIECE_ORDER = ("q", "r", "b", "n", "p")
START_COUNT = {"p": 8, "n": 2, "b": 2, "r": 2, "q": 1}
VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9}


def captured(board: chess.Board) -> tuple[list[str], list[str], int]:
    """(taken by white, taken by black, material balance from white's view)."""
    by_white: list[str] = []
    by_black: list[str] = []
    for letter in PIECE_ORDER:
        pt = chess.PIECE_SYMBOLS.index(letter)
        missing_black = START_COUNT[letter] - len(board.pieces(pt, chess.BLACK))
        missing_white = START_COUNT[letter] - len(board.pieces(pt, chess.WHITE))
        by_white += [letter] * max(0, missing_black)
        by_black += [letter] * max(0, missing_white)
    balance = sum(VALUES[p] for p in by_white) - sum(VALUES[p] for p in by_black)
    return by_white, by_black, balance


def board_data(board: chess.Board, moves_san: list[str], *, user_color: str,
               elo: int, status_text: str, waiting: bool,
               result: str | None = None, last_move: str | None = None,
               clock: dict | None = None, quality: list | None = None,
               eval: dict | None = None, review: dict | None = None) -> dict:
    """Everything the widget needs to draw one position.

    Sent whole on the first render and again as an in-place update for
    every move afterwards, so the widget never needs to reload.
    """
    by_white, by_black, balance = captured(board)
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "user": user_color,
        "elo": elo,
        "legal": [m.uci() for m in board.legal_moves] if waiting else [],
        "last": last_move,
        "check": board.is_check(),
        "moves": moves_san,
        "status": status_text,
        "waiting": waiting,
        "over": bool(result),
        "result": result,
        "capW": by_white,
        "capB": by_black,
        "balance": balance,
        "clock": clock,
        # one entry per move played: {cls, loss, best_san, best_uci}
        "quality": quality or [],
        # {cp, mate, text} from White's point of view, for the eval bar
        "eval": eval,
        # per-side accuracy and counts, present once the game is over
        "review": review,
    }


def render(board: chess.Board, moves_san: list[str], **kw) -> str:
    """Full widget HTML for the first render of a game."""
    data = board_data(board, moves_san, **kw)
    payload = json.dumps(data).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", payload)


# ---------------------------------------------------------------------
# Piece artwork (Cburnett, CC BY-SA 3.0), 45x45 viewBox
# ---------------------------------------------------------------------
_PIECES = {
"wK": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22.5 11.63V6M20 8h5" stroke-linejoin="miter"/><path d="M22.5 25s4.5-7.5 3-10.5c0 0-1-2.5-3-2.5s-3 2.5-3 2.5c-1.5 3 3 10.5 3 10.5" fill="#fff" stroke-linecap="butt" stroke-linejoin="miter"/><path d="M12.5 37c5.5 3.5 14.5 3.5 20 0v-7s9-4.5 6-10.5c-4-6.5-13.5-3.5-16 4V27v-3.5c-2.5-7.5-12-10.5-16-4-3 6 6 10.5 6 10.5v7" fill="#fff"/><path d="M12.5 30c5.5-3 14.5-3 20 0m-20 3.5c5.5-3 14.5-3 20 0m-20 3.5c5.5-3 14.5-3 20 0"/></g>""",
"bK": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22.5 11.63V6" stroke-linejoin="miter"/><path d="M22.5 25s4.5-7.5 3-10.5c0 0-1-2.5-3-2.5s-3 2.5-3 2.5c-1.5 3 3 10.5 3 10.5" fill="#000" stroke-linecap="butt" stroke-linejoin="miter"/><path d="M12.5 37c5.5 3.5 14.5 3.5 20 0v-7s9-4.5 6-10.5c-4-6.5-13.5-3.5-16 4V27v-3.5c-2.5-7.5-12-10.5-16-4-3 6 6 10.5 6 10.5v7" fill="#000"/><path d="M20 8h5" stroke-linejoin="miter"/><path d="M32 29.5s8.5-4 6.03-9.65C34.15 14 25 18 22.5 24.5l.01 2.1-.01-2.1C20 18 9.906 14 6.997 19.85c-2.497 5.65 4.853 9 4.853 9M12.5 30c5.5-3 14.5-3 20 0m-20 3.5c5.5-3 14.5-3 20 0m-20 3.5c5.5-3 14.5-3 20 0" stroke="#fff"/></g>""",
"wQ": """<g fill="#fff" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 12a2 2 0 1 1-4 0 2 2 0 1 1 4 0zm16.5-4.5a2 2 0 1 1-4 0 2 2 0 1 1 4 0zM41 12a2 2 0 1 1-4 0 2 2 0 1 1 4 0zM16 8.5a2 2 0 1 1-4 0 2 2 0 1 1 4 0zM33 9a2 2 0 1 1-4 0 2 2 0 1 1 4 0z"/><path d="M9 26c8.5-1.5 21-1.5 27 0l2-12-7 11V11l-5.5 13.5-3-15-3 15-5.5-14V25L7 14l2 12z" stroke-linecap="butt"/><path d="M9 26c0 2 1.5 2 2.5 4 1 1.5 1 1 .5 3.5-1.5 1-1.5 2.5-1.5 2.5-1.5 1.5.5 2.5.5 2.5 6.5 1 16.5 1 23 0 0 0 1.5-1 0-2.5 0 0 .5-1.5-1-2.5-.5-2.5-.5-2 .5-3.5 1-2 2.5-2 2.5-4-8.5-1.5-18.5-1.5-27 0z" stroke-linecap="butt"/><path d="M11.5 30c3.5-1 18.5-1 22 0M12 33.5c6-1 15-1 21 0" fill="none"/></g>""",
"bQ": """<g fill="#000" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 26c8.5-1.5 21-1.5 27 0l2.5-12.5L31 25l-.3-14.1-5.2 13.6-3-14.5-3 14.5-5.2-13.6L14 25 6.5 13.5 9 26z" stroke-linecap="butt"/><path d="M9 26c0 2 1.5 2 2.5 4 1 1.5 1 1 .5 3.5-1.5 1-1.5 2.5-1.5 2.5-1.5 1.5.5 2.5.5 2.5 6.5 1 16.5 1 23 0 0 0 1.5-1 0-2.5 0 0 .5-1.5-1-2.5-.5-2.5-.5-2 .5-3.5 1-2 2.5-2 2.5-4-8.5-1.5-18.5-1.5-27 0z" stroke-linecap="butt"/><path d="M11 38.5a35 35 1 0 0 23 0" fill="none" stroke-linecap="butt"/><circle cx="6" cy="12" r="2"/><circle cx="14" cy="9" r="2"/><circle cx="22.5" cy="8" r="2"/><circle cx="31" cy="9" r="2"/><circle cx="39" cy="12" r="2"/><path d="M11 29a35 35 1 0 1 23 0m-21.5 2.5h20m-21 3a35 35 1 0 0 22 0m-23 3a35 35 1 0 0 24 0" fill="none" stroke="#fff"/></g>""",
"wR": """<g fill="#fff" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 39h27v-3H9v3zm3-3v-4h21v4H12zm-1-22V9h4v2h5V9h5v2h5V9h4v5" stroke-linecap="butt"/><path d="m34 14-3 3H14l-3-3"/><path d="M31 17v12.5H14V17" stroke-linecap="butt" stroke-linejoin="miter"/><path d="m31 29.5 1.5 2.5h-20l1.5-2.5"/><path d="M11 14h23" fill="none" stroke-linejoin="miter"/></g>""",
"bR": """<g fill="#000" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 39h27v-3H9v3zm3.5-7 1.5-2.5h17l1.5 2.5h-20zm-.5 4v-4h21v4H12z" stroke-linecap="butt"/><path d="M14 29.5v-13h17v13H14z" stroke-linecap="butt" stroke-linejoin="miter"/><path d="M14 16.5 11 14h23l-3 2.5H14zM11 14V9h4v2h5V9h5v2h5V9h4v5H11z" stroke-linecap="butt"/><path d="M12 35.5h21m-20-4h19m-18-2h17m-17-13h17M11 14h23" fill="none" stroke="#fff" stroke-width="1" stroke-linejoin="miter"/></g>""",
"wB": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><g fill="#fff" stroke-linecap="butt"><path d="M9 36c3.39-.97 10.11.43 13.5-2 3.39 2.43 10.11 1.03 13.5 2 0 0 1.65.54 3 2-.68.97-1.65.99-3 .5-3.39-.97-10.11.46-13.5-1-3.39 1.46-10.11.03-13.5 1-1.354.49-2.323.47-3-.5 1.354-1.94 3-2 3-2z"/><path d="M15 32c2.5 2.5 12.5 2.5 15 0 .5-1.5 0-2 0-2 0-2.5-2.5-4-2.5-4 5.5-1.5 6-11.5-5-15.5-11 4-10.5 14-5 15.5 0 0-2.5 1.5-2.5 4 0 0-.5.5 0 2z"/><path d="M25 8a2.5 2.5 0 1 1-5 0 2.5 2.5 0 1 1 5 0z"/></g><path d="M17.5 26h10M15 30h15m-7.5-14.5v5M20 18h5" stroke-linejoin="miter"/></g>""",
"bB": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><g fill="#000" stroke-linecap="butt"><path d="M9 36c3.39-.97 10.11.43 13.5-2 3.39 2.43 10.11 1.03 13.5 2 0 0 1.65.54 3 2-.68.97-1.65.99-3 .5-3.39-.97-10.11.46-13.5-1-3.39 1.46-10.11.03-13.5 1-1.354.49-2.323.47-3-.5 1.354-1.94 3-2 3-2z"/><path d="M15 32c2.5 2.5 12.5 2.5 15 0 .5-1.5 0-2 0-2 0-2.5-2.5-4-2.5-4 5.5-1.5 6-11.5-5-15.5-11 4-10.5 14-5 15.5 0 0-2.5 1.5-2.5 4 0 0-.5.5 0 2z"/><path d="M25 8a2.5 2.5 0 1 1-5 0 2.5 2.5 0 1 1 5 0z"/></g><path d="M17.5 26h10M15 30h15m-7.5-14.5v5M20 18h5" stroke="#fff" stroke-linejoin="miter"/></g>""",
"wN": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10c10.5 1 16.5 8 16 29H15c0-9 10-6.5 8-21" fill="#fff"/><path d="M24 18c.38 2.91-5.55 7.37-8 9-3 2-2.82 4.34-5 4-1.042-.94 1.41-3.04 0-3-1 0 .19 1.23-1 2-1 0-4.003 1-4-4 0-2 6-12 6-12s1.89-1.9 2-3.5c-.73-.994-.5-2-.5-3 1-1 3 2.5 3 2.5h2s.78-1.992 2.5-3c1 0 1 3 1 3" fill="#fff"/><path d="M9.5 25.5a.5.5 0 1 1-1 0 .5.5 0 1 1 1 0zm5.433-9.75a.5 1.5 30 1 1-.866-.5.5 1.5 30 1 1 .866.5z" fill="#000"/></g>""",
"bN": """<g fill="none" fill-rule="evenodd" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10c10.5 1 16.5 8 16 29H15c0-9 10-6.5 8-21" fill="#000"/><path d="M24 18c.38 2.91-5.55 7.37-8 9-3 2-2.82 4.34-5 4-1.042-.94 1.41-3.04 0-3-1 0 .19 1.23-1 2-1 0-4.003 1-4-4 0-2 6-12 6-12s1.89-1.9 2-3.5c-.73-.994-.5-2-.5-3 1-1 3 2.5 3 2.5h2s.78-1.992 2.5-3c1 0 1 3 1 3" fill="#000"/><path d="M9.5 25.5a.5.5 0 1 1-1 0 .5.5 0 1 1 1 0zm5.433-9.75a.5 1.5 30 1 1-.866-.5.5 1.5 30 1 1 .866.5z" fill="#fff" stroke="#fff"/><path d="M24.55 10.4l-.45 1.45.5.15c3.15 1 5.65 2.49 7.9 6.75S35.75 29.06 35.25 39l-.05.5h2.25l.05-.5c.5-10.06-.88-16.85-3.25-21.34-2.37-4.49-5.79-6.64-9.19-7.16l-.51-.1z" fill="#fff" stroke="none"/></g>""",
"wP": """<path d="m22.5 9c-2.21 0-4 1.79-4 4 0 .89.29 1.71.78 2.38C17.33 16.5 16 18.59 16 21c0 2.03.94 3.84 2.41 5.03-3 1.06-7.41 5.55-7.41 13.47h23c0-7.92-4.41-12.41-7.41-13.47 1.47-1.19 2.41-3 2.41-5.03 0-2.41-1.33-4.5-3.28-5.62.49-.67.78-1.49.78-2.38 0-2.21-1.79-4-4-4z" fill="#fff" stroke="#000" stroke-width="1.5" stroke-linecap="round"/>""",
"bP": """<path d="m22.5 9c-2.21 0-4 1.79-4 4 0 .89.29 1.71.78 2.38C17.33 16.5 16 18.59 16 21c0 2.03.94 3.84 2.41 5.03-3 1.06-7.41 5.55-7.41 13.47h23c0-7.92-4.41-12.41-7.41-13.47 1.47-1.19 2.41-3 2.41-5.03 0-2.41-1.33-4.5-3.28-5.62.49-.67.78-1.49.78-2.38 0-2.21-1.79-4-4-4z" fill="#000" stroke="#000" stroke-width="1.5" stroke-linecap="round"/>""",
}

_PIECES_JS = json.dumps(_PIECES)


_TEMPLATE = r"""
<style>
  .cw{--sq:58px;--light:#eeeed2;--dark:#769656;
      --hl:rgba(255,255,51,.45);--sel:rgba(255,200,0,.62);
      font-family:var(--font);color:var(--text);
      display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
  .cw .bw{flex:0 0 auto;width:calc(var(--sq)*8 + 22px)}
  .cw .bar{display:flex;align-items:center;gap:10px;height:46px;padding:4px 0 4px 22px}
  .cw .avatar{width:36px;height:36px;border-radius:6px;background:#2a2f3a;
      display:flex;align-items:center;justify-content:center;flex:0 0 auto}
  .cw .avatar svg{width:30px;height:30px}
  .cw .who{display:flex;flex-direction:column;min-width:0;flex:1}
  .cw .name{font-weight:600;font-size:14px;line-height:1.2}
  .cw .name .rating{font-weight:400;color:var(--text-dim);margin-left:4px}
  .cw .caps{display:flex;align-items:center;height:14px;margin-top:2px}
  .cw .caps svg{width:14px;height:14px;margin-right:-3px}
  .cw .caps .adv{font-size:11px;color:var(--text-dim);margin-left:8px;font-family:var(--mono)}
  .cw .clock{font-family:var(--mono);font-size:22px;font-weight:600;letter-spacing:.02em;
      padding:6px 12px;border-radius:6px;background:#22262f;color:#8b91a1;
      min-width:92px;text-align:center;font-variant-numeric:tabular-nums;
      transition:background .25s,color .25s}
  .cw .clock.active{background:#e6e8ee;color:#141414}
  .cw .clock.low{color:#ff6b6b} .cw .clock.active.low{background:#ffd7d7;color:#a51d1d;animation:lowTime 1s ease-in-out infinite}
  .cw .clock.hidden{visibility:hidden}

  .cw .row{display:flex;gap:8px;align-items:stretch}
  .cw .evalbar{width:14px;height:calc(var(--sq)*8);border-radius:3px;background:#3a3f4a;
      position:relative;overflow:hidden;flex:0 0 auto}
  .cw .evalfill{position:absolute;left:0;right:0;bottom:0;background:#f0f0f0;
      transition:height .7s cubic-bezier(.2,.8,.2,1)}
  .cw .evalbar.flipped .evalfill{bottom:auto;top:0}
  .cw .evalnum{position:absolute;left:0;right:0;text-align:center;font-family:var(--mono);
      font-size:9px;font-weight:700;letter-spacing:-.02em;pointer-events:none}
  .cw .evalnum.w{bottom:2px;color:#141414} .cw .evalnum.b{top:2px;color:#e6e8ee}
  .cw .evalbar.flipped .evalnum.w{bottom:auto;top:2px} .cw .evalbar.flipped .evalnum.b{top:auto;bottom:2px}

  .cw .boardbox{position:relative;flex:0 0 auto}
  .cw .board{display:grid;grid-template-columns:repeat(8,var(--sq));
      grid-template-rows:repeat(8,var(--sq));border-radius:4px;overflow:hidden;
      box-shadow:0 3px 14px rgba(0,0,0,.5);user-select:none;touch-action:none}
  .cw .sq{position:relative;display:flex;align-items:center;justify-content:center}
  .cw .sq.l{background:var(--light)} .cw .sq.d{background:var(--dark)}
  .cw .sq.last::before{content:"";position:absolute;inset:0;background:var(--hl);animation:fadeIn .3s}
  .cw .sq.sel::before{content:"";position:absolute;inset:0;background:var(--sel)}
  .cw .sq.check::before{content:"";position:absolute;inset:0;
      background:radial-gradient(circle,rgba(255,20,20,.95) 0%,rgba(255,20,20,.4) 52%,transparent 70%);
      animation:checkPulse .9s ease-out}
  .cw .sq.dot::after{content:"";position:absolute;width:30%;height:30%;border-radius:50%;
      background:rgba(0,0,0,.2);z-index:2;animation:popIn .18s ease-out}
  .cw .sq.cap::after{content:"";position:absolute;inset:2px;border-radius:50%;
      border:6px solid rgba(0,0,0,.2);z-index:2;animation:popIn .18s ease-out}
  .cw .sq.can{cursor:pointer}
  .cw .sq.can:hover::after{background:rgba(0,0,0,.3)}
  .cw .sq.cap.can:hover::after{border-color:rgba(0,0,0,.3);background:none}
  .cw .sq.own{cursor:grab}
  .cw .sq.own:hover{filter:brightness(1.05)}
  .cw .p{position:absolute;inset:0;z-index:1;pointer-events:none;
      display:flex;align-items:center;justify-content:center;will-change:transform}
  .cw .p svg{width:90%;height:90%;filter:drop-shadow(0 1px 1px rgba(0,0,0,.35))}
  .cw .p.slide{transition:transform .22s cubic-bezier(.2,.8,.2,1)}
  .cw .p.lifted{opacity:.35}
  .cw .p.taken{animation:taken .28s ease-in forwards;z-index:0}
  .cw .ghost{position:absolute;width:var(--sq);height:var(--sq);z-index:6;pointer-events:none;
      display:flex;align-items:center;justify-content:center;transform:translate(-50%,-50%)}
  .cw .ghost svg{width:100%;height:100%;filter:drop-shadow(0 6px 8px rgba(0,0,0,.5));transform:scale(1.12)}
  .cw .lbl{position:absolute;font-size:10.5px;font-weight:700;pointer-events:none;z-index:2;opacity:.9}
  .cw .lbl.f{right:3px;bottom:1px} .cw .lbl.r{left:3px;top:2px}
  .cw .sq.l .lbl{color:#769656} .cw .sq.d .lbl{color:#eeeed2}
  .cw .arrows{position:absolute;inset:0;pointer-events:none;z-index:3}
  .cw .arrows .a{animation:arrowFade 3.2s ease-in forwards}
  .cw .promo{position:absolute;z-index:7;display:none;flex-direction:column;
      background:#fff;border-radius:6px;box-shadow:0 4px 18px rgba(0,0,0,.6);overflow:hidden;
      animation:popIn .18s ease-out}
  .cw .promo.show{display:flex}
  .cw .promo button{width:var(--sq);height:var(--sq);border:0;background:#fff;padding:6px;cursor:pointer}
  .cw .promo button:hover{background:#ffe08a}
  .cw .promo button svg{width:100%;height:100%}

  .cw .gameover{position:absolute;inset:0;z-index:8;display:flex;align-items:center;
      justify-content:center;background:rgba(10,12,18,.62);backdrop-filter:blur(2px);
      animation:fadeIn .45s ease-out;border-radius:4px}
  .cw .card{background:#1a1e28;border:1px solid #333a4a;border-radius:12px;padding:18px 20px;
      width:min(88%,330px);box-shadow:0 12px 40px rgba(0,0,0,.6);animation:cardIn .45s cubic-bezier(.2,1.2,.4,1)}
  .cw .card h3{margin:0;font-size:20px;text-align:center}
  .cw .card .why{margin:4px 0 14px;text-align:center;color:var(--text-dim);font-size:13px}
  .cw .rv{width:100%;border-collapse:collapse;font-size:13px}
  .cw .rv th{font-weight:500;color:var(--text-dim);text-align:right;padding:3px 6px;font-size:11px;
      letter-spacing:.08em;text-transform:uppercase}
  .cw .rv th:first-child{text-align:left}
  .cw .rv td{padding:3px 6px;text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
  .cw .rv td:first-child{text-align:left;font-family:var(--font)}
  .cw .rv tr.acc td{font-size:17px;font-weight:600;padding-bottom:8px}
  .cw .rv .sym{display:inline-block;width:16px;height:16px;border-radius:50%;color:#fff;
      font-size:10px;font-weight:800;text-align:center;line-height:16px;margin-right:6px;vertical-align:middle}
  .cw .card .btns{display:flex;gap:8px;margin-top:14px;justify-content:center}

  .cw .panel{flex:1 1 200px;min-width:190px;max-width:250px;
      display:flex;flex-direction:column;gap:9px;align-self:stretch;padding-top:50px}
  .cw .status{font-size:14px;padding:9px 11px;border-radius:8px;
      background:var(--surface);border:1px solid var(--border);transition:border-color .25s}
  .cw .status.turn{border-color:var(--accent)}
  .cw .status.over{border-color:var(--good)}
  .cw .status.err{border-color:var(--bad)}
  .cw .verdict{font-size:13px;padding:0 2px;min-height:18px;line-height:1.4;animation:fadeIn .3s}
  .cw .verdict .sym{display:inline-block;width:17px;height:17px;border-radius:50%;color:#fff;
      font-size:10px;font-weight:800;text-align:center;line-height:17px;margin-right:6px;vertical-align:-3px}
  .cw .verdict .better{color:var(--text-dim)}
  .cw .ttl{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--text-dim);margin-top:2px}
  .cw .moves{flex:1;min-height:120px;max-height:calc(var(--sq)*8 - 250px);overflow-y:auto;
      background:var(--surface);border:1px solid var(--border);border-radius:8px;
      font-family:var(--mono);font-size:13px}
  .cw .moves .empty{padding:10px;color:var(--text-dim);font-family:var(--font);font-size:13px}
  .cw .mv{display:grid;grid-template-columns:30px 1fr 1fr;padding:3px 8px;border-bottom:1px solid var(--border)}
  .cw .mv:last-child{border-bottom:0}
  .cw .mv.new{animation:slideIn .25s ease-out}
  .cw .mv .n{color:var(--text-dim)}
  .cw .mv .cur{background:rgba(109,140,255,.28);border-radius:4px;padding:0 5px;margin:0 -5px}
  .cw .mv .q{font-size:10px;font-weight:800;margin-left:4px;vertical-align:1px}
  .cw .ask{display:flex;gap:6px}
  .cw .ask input{flex:1;min-width:0;background:var(--surface);border:1px solid var(--border);
      border-radius:8px;padding:8px 10px;color:var(--text);font-family:inherit;font-size:13px}
  .cw .ask input:focus{outline:none;border-color:var(--accent)}
  .cw button.btn{background:var(--surface);border:1px solid var(--border);color:var(--text);
      border-radius:8px;padding:8px 12px;font-size:13px;white-space:nowrap;font-family:inherit;cursor:pointer;
      transition:border-color .15s,transform .1s}
  .cw button.btn:hover{border-color:var(--text-dim)}
  .cw button.btn:active{transform:translateY(1px)}
  .cw button.btn.danger:hover{border-color:var(--bad);color:var(--bad)}
  .cw button.btn.primary{background:var(--accent);border-color:var(--accent);color:#0d0f14;font-weight:600}
  .cw button.btn:disabled{opacity:.45;cursor:not-allowed}
  .cw .actions{display:flex;gap:6px;justify-content:space-between}
  .cw .actions .sound{flex:0 0 auto;font-size:12px;padding:8px 9px}
  .cw.busy .sq{cursor:default!important;filter:none!important}

  @keyframes fadeIn{from{opacity:0}to{opacity:1}}
  @keyframes popIn{0%{transform:scale(0);opacity:0}70%{transform:scale(1.18);opacity:1}100%{transform:scale(1);opacity:1}}
  @keyframes taken{to{opacity:0;transform:scale(.55)}}
  @keyframes checkPulse{0%{opacity:.2}40%{opacity:1}100%{opacity:1}}
  @keyframes lowTime{0%,100%{opacity:1}50%{opacity:.55}}
  @keyframes slideIn{from{opacity:0;transform:translateX(-6px)}to{opacity:1;transform:none}}
  @keyframes cardIn{from{opacity:0;transform:translateY(14px) scale(.96)}to{opacity:1;transform:none}}
  @keyframes arrowFade{0%,60%{opacity:.9}100%{opacity:0}}

  @media (max-width:700px){
    .cw{--sq:min(calc((100vw - 72px) / 8),48px)}
    .cw .panel{max-width:none;flex-basis:100%;padding-top:0}
    .cw .moves{max-height:160px}
    .cw .clock{font-size:18px;min-width:78px}
  }
  @media (prefers-reduced-motion:reduce){
    .cw *{animation:none!important;transition:none!important}
  }
</style>

<div class="cw" id="cw">
  <div class="bw">
    <div class="bar">
      <div class="avatar" id="topAvatar"></div>
      <div class="who"><div class="name" id="topName"></div><div class="caps" id="topCaps"></div></div>
      <div class="clock" id="topClock">--:--</div>
    </div>
    <div class="row">
      <div class="evalbar" id="evalbar"><div class="evalfill" id="evalfill"></div><div class="evalnum w" id="evalW"></div><div class="evalnum b" id="evalB"></div></div>
      <div class="boardbox" id="boardbox">
        <div class="board" id="board" aria-label="Chessboard"></div>
        <svg class="arrows" id="arrows"></svg>
        <div class="promo" id="promo"></div>
      </div>
    </div>
    <div class="bar">
      <div class="avatar" id="botAvatar"></div>
      <div class="who"><div class="name" id="botName"></div><div class="caps" id="botCaps"></div></div>
      <div class="clock" id="botClock">--:--</div>
    </div>
  </div>
  <div class="panel">
    <div class="status" id="status"></div>
    <div class="verdict" id="verdict"></div>
    <div class="ttl">Moves</div>
    <div class="moves" id="moves"></div>
    <div class="ask">
      <input id="askIn" placeholder="Ask about the position…" autocomplete="off">
      <button class="btn" id="askBtn">Ask</button>
    </div>
    <div class="actions">
      <button class="btn danger" id="resign">Resign</button>
      <button class="btn" id="newgame">New game</button>
      <button class="btn sound" id="soundBtn" title="Toggle sound">Sound on</button>
    </div>
  </div>
</div>

<script>
(function(){
  var PIECES = __PIECES__;
  var G = __DATA__;
  var Q = {
    brilliant:  ["!!", "#1baca6", "Brilliant"],
    great:      ["!",  "#5c8bb0", "Great move"],
    best:       ["★",  "#96bc4b", "Best move"],
    excellent:  ["✓",  "#96bc4b", "Excellent"],
    good:       ["✓",  "#96af8b", "Good"],
    inaccuracy: ["?!", "#f7c631", "Inaccuracy"],
    mistake:    ["?",  "#e58f2a", "Mistake"],
    blunder:    ["??", "#ca3431", "Blunder"],
    forced:     ["□",  "#8b91a1", "Forced"]
  };
  var FILES = "abcdefgh";
  var root = document.getElementById("cw");
  var boardEl = document.getElementById("board");
  var boxEl = document.getElementById("boardbox");
  var promoEl = document.getElementById("promo");
  var arrowsEl = document.getElementById("arrows");
  var sel = null, done = false, pieces = {}, prevPieces = {}, legalFrom = {}, userWhite, flip;
  var lastLocalMove = null, clockBase = null, clockAt = 0, flagged = false;
  var drag = null, ghost = null, muted = false, AC = null, shownMoves = 0;

  function svgFor(key){ return '<svg viewBox="0 0 45 45" xmlns="http://www.w3.org/2000/svg">' + PIECES[key] + '</svg>'; }
  function pieceKey(p){ return (p.w ? "w" : "b") + p.t.toUpperCase(); }
  function sqSize(){ var f = boardEl.firstChild; return f ? f.getBoundingClientRect().width : 58; }

  function parseFen(fen){
    var rows = fen.split(" ")[0].split("/"), m = {};
    for (var r = 0; r < 8; r++){
      var f = 0;
      for (var i = 0; i < rows[r].length; i++){
        var c = rows[r][i];
        if (c >= "1" && c <= "8") { f += parseInt(c, 10); }
        else { m[FILES[f] + (8 - r)] = {t: c.toLowerCase(), w: c === c.toUpperCase()}; f++; }
      }
    }
    return m;
  }

  function load(data){
    G = data;
    userWhite = G.user === "white";
    flip = !userWhite;
    prevPieces = pieces;
    pieces = parseFen(G.fen);
    legalFrom = {};
    G.legal.forEach(function(u){
      var f = u.slice(0,2), t = u.slice(2,4);
      var slot = (legalFrom[f] = legalFrom[f] || {});
      if (u.length === 5){ (slot[t] = (typeof slot[t] === "object" ? slot[t] : {}))[u[4]] = u; }
      else slot[t] = u;
    });
    sel = null; done = false; flagged = false;
    root.classList.remove("busy");
    promoEl.classList.remove("show");
    clockBase = G.clock ? {white: G.clock.white, black: G.clock.black} : null;
    clockAt = Date.now();
  }

  function sqPos(sq){
    var file = FILES.indexOf(sq[0]), rank = parseInt(sq[1], 10);
    return {col: flip ? 7 - file : file, row: flip ? rank - 1 : 8 - rank};
  }

  // -- sound (synthesised; no assets, no network) ---------------------------
  function tone(freq, dur, type, gain, when){
    if (muted) return;
    try {
      AC = AC || new (window.AudioContext || window.webkitAudioContext)();
      if (AC.state === "suspended") AC.resume();
      var o = AC.createOscillator(), g = AC.createGain();
      o.type = type || "sine"; o.frequency.value = freq;
      var t = AC.currentTime + (when || 0);
      g.gain.setValueAtTime(gain || 0.07, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g); g.connect(AC.destination); o.start(t); o.stop(t + dur);
    } catch (e) {}
  }
  var SFX = {
    move:    function(){ tone(560, .07, "triangle", .06); },
    capture: function(){ tone(300, .11, "square", .05); tone(200, .12, "triangle", .05, .04); },
    check:   function(){ tone(880, .12, "sine", .07); tone(1100, .12, "sine", .05, .08); },
    bad:     function(){ tone(220, .18, "sawtooth", .04); },
    good:    function(){ tone(700, .08, "sine", .05); tone(1050, .1, "sine", .05, .07); },
    end:     function(){ [523,659,784,1047].forEach(function(f,i){ tone(f, .22, "sine", .06, i*.09); }); }
  };

  // -- board ----------------------------------------------------------------
  function render(){
    boardEl.innerHTML = "";

    for (var rr = 0; rr < 8; rr++){
      for (var ff = 0; ff < 8; ff++){
        var rank = flip ? rr + 1 : 8 - rr;
        var file = flip ? 7 - ff : ff;
        var sq = FILES[file] + rank;
        var d = document.createElement("div");
        var light = (file + rank) % 2 === 1;
        var cls = "sq " + (light ? "l" : "d");
        if (G.last && (G.last.slice(0,2) === sq || G.last.slice(2,4) === sq)) cls += " last";
        if (sel === sq) cls += " sel";
        var p = pieces[sq];
        if (p && p.t === "k" && G.check && p.w === (G.turn === "white")) cls += " check";
        if (sel && legalFrom[sel] && legalFrom[sel][sq]) cls += (p ? " cap" : " dot") + " can";
        if (G.waiting && !done && p && p.w === userWhite && legalFrom[sq]) cls += " own";
        d.className = cls;
        if (p){
          var s = document.createElement("div");
          s.className = "p"; s.dataset.sq = sq;
          s.innerHTML = svgFor(pieceKey(p));
          d.appendChild(s);
        }
        if (ff === 0){ var l = document.createElement("span"); l.className = "lbl r"; l.textContent = rank; d.appendChild(l); }
        if (rr === 7){ var l2 = document.createElement("span"); l2.className = "lbl f"; l2.textContent = FILES[file]; d.appendChild(l2); }
        d.dataset.sq = sq;
        boardEl.appendChild(d);
      }
    }
  }

  function animate(uci, capturedPiece){
    if (!uci) return;
    var from = sqPos(uci.slice(0,2)), to = sqPos(uci.slice(2,4));
    var el = boardEl.querySelector('.p[data-sq="' + uci.slice(2,4) + '"]');
    if (!el) return;
    var s = sqSize();
    if (capturedPiece){
      var tgt = el.parentElement, g = document.createElement("div");
      g.className = "p taken"; g.innerHTML = svgFor(pieceKey(capturedPiece));
      tgt.insertBefore(g, el);
      setTimeout(function(){ if (g.parentElement) g.parentElement.removeChild(g); }, 320);
    }
    el.style.transform = "translate(" + ((from.col - to.col) * s) + "px," + ((from.row - to.row) * s) + "px)";
    el.getBoundingClientRect();
    el.classList.add("slide");
    el.style.transform = "translate(0,0)";
  }

  function drawArrow(uci, color){
    arrowsEl.innerHTML = "";
    if (!uci) return;
    var s = sqSize(), f = sqPos(uci.slice(0,2)), t = sqPos(uci.slice(2,4));
    var x1 = f.col * s + s/2, y1 = f.row * s + s/2, x2 = t.col * s + s/2, y2 = t.row * s + s/2;
    var dx = x2 - x1, dy = y2 - y1, len = Math.sqrt(dx*dx + dy*dy) || 1;
    var ux = dx/len, uy = dy/len, head = s * .38, w = s * .16;
    var bx = x2 - ux * head, by = y2 - uy * head;
    var ns = "http://www.w3.org/2000/svg";
    arrowsEl.setAttribute("viewBox", "0 0 " + (s*8) + " " + (s*8));
    var gr = document.createElementNS(ns, "g"); gr.setAttribute("class", "a");
    var line = document.createElementNS(ns, "line");
    line.setAttribute("x1", x1 + ux*s*.3); line.setAttribute("y1", y1 + uy*s*.3);
    line.setAttribute("x2", bx); line.setAttribute("y2", by);
    line.setAttribute("stroke", color); line.setAttribute("stroke-width", w); line.setAttribute("stroke-linecap", "round");
    var tri = document.createElementNS(ns, "polygon");
    var px = -uy, py = ux;
    tri.setAttribute("points", [x2, y2, bx + px*w*1.3, by + py*w*1.3, bx - px*w*1.3, by - py*w*1.3].join(" "));
    tri.setAttribute("fill", color);
    gr.appendChild(line); gr.appendChild(tri); arrowsEl.appendChild(gr);
  }

  function applyLocal(uci){
    var from = uci.slice(0,2), to = uci.slice(2,4), promo = uci[4];
    var p = pieces[from]; if (!p) return;
    var taken = pieces[to] || null;
    delete pieces[from];
    if (p.t === "k" && Math.abs(FILES.indexOf(to[0]) - FILES.indexOf(from[0])) === 2){
      var rank = from[1];
      var rookFrom = (to[0] === "g" ? "h" : "a") + rank, rookTo = (to[0] === "g" ? "f" : "d") + rank;
      pieces[rookTo] = pieces[rookFrom]; delete pieces[rookFrom];
    }
    if (p.t === "p" && from[0] !== to[0] && !pieces[to]){ taken = pieces[to[0] + from[1]] || null; delete pieces[to[0] + from[1]]; }
    if (promo) p = {t: promo, w: p.w};
    pieces[to] = p;
    G.last = uci; G.waiting = false;
    G.turn = userWhite ? "black" : "white"; G.check = false;
    sel = null;
    render(); animate(uci, taken); arrowsEl.innerHTML = "";
    (taken ? SFX.capture : SFX.move)();
    setStatus("Thinking…", "");
    renderClocks();
  }

  function moveTo(from, to){
    var target = legalFrom[from] && legalFrom[from][to];
    if (!target) return false;
    if (typeof target === "object"){ offerPromotion(from, to, target); return true; }
    makeMove(target); return true;
  }
  function makeMove(uci){ lastLocalMove = uci; applyLocal(uci); send({move: uci}); }

  function offerPromotion(from, to, options){
    promoEl.innerHTML = "";
    var pos = sqPos(to), s = sqSize(), atTop = pos.row === 0;
    ["q","r","b","n"].forEach(function(k){
      var b = document.createElement("button");
      b.innerHTML = svgFor((userWhite ? "w" : "b") + k.toUpperCase());
      b.addEventListener("click", function(){ promoEl.classList.remove("show"); makeMove(options[k]); });
      promoEl.appendChild(b);
    });
    promoEl.style.left = (pos.col * s) + "px";
    promoEl.style.top = (atTop ? 0 : (pos.row - 3) * s) + "px";
    promoEl.classList.add("show");
  }

  // -- pointer: click-to-move and drag-and-drop share one handler ------------
  boardEl.addEventListener("pointerdown", function(e){
    if (!G.waiting || done) return;
    var sqEl = e.target.closest(".sq"); if (!sqEl) return;
    var sq = sqEl.dataset.sq, p = pieces[sq];
    if (sel && legalFrom[sel] && legalFrom[sel][sq]){ e.preventDefault(); moveTo(sel, sq); return; }
    if (p && p.w === userWhite && legalFrom[sq]){
      e.preventDefault();
      sel = sq; render();
      drag = {from: sq, moved: false, x: e.clientX, y: e.clientY, id: e.pointerId};
      try { boardEl.setPointerCapture(e.pointerId); } catch (err) {}
      return;
    }
    sel = null; render();
  });
  boardEl.addEventListener("pointermove", function(e){
    if (!drag) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
    if (!drag.moved){
      drag.moved = true;
      var p = pieces[drag.from];
      ghost = document.createElement("div"); ghost.className = "ghost"; ghost.innerHTML = svgFor(pieceKey(p));
      boxEl.appendChild(ghost);
      var orig = boardEl.querySelector('.p[data-sq="' + drag.from + '"]'); if (orig) orig.classList.add("lifted");
    }
    var r = boxEl.getBoundingClientRect();
    ghost.style.left = (e.clientX - r.left) + "px"; ghost.style.top = (e.clientY - r.top) + "px";
  });
  function endDrag(e){
    if (!drag) return;
    var d = drag; drag = null;
    if (ghost){ ghost.remove(); ghost = null; }
    var orig = boardEl.querySelector('.p[data-sq="' + d.from + '"]'); if (orig) orig.classList.remove("lifted");
    if (!d.moved) return;                          // a plain click: selection stays
    var el = document.elementFromPoint(e.clientX, e.clientY);
    var target = el && el.closest && el.closest(".sq");
    if (!(target && moveTo(d.from, target.dataset.sq))){ sel = null; render(); }
  }
  boardEl.addEventListener("pointerup", endDrag);
  boardEl.addEventListener("pointercancel", endDrag);

  function send(data){
    if (done) return;
    done = true;
    root.classList.add("busy");
    if (!data.move) setStatus(data.ask ? "Asking Stellar…" : data.flag ? "Out of time" : data.newgame ? "Starting a new game…" : "Leaving the game…", "");
    setButtons(false);
    window.stellar.finish(data);
  }
  function setButtons(on){ ["askBtn","resign","newgame"].forEach(function(id){ document.getElementById(id).disabled = !on; }); }
  function setStatus(text, kind){ var el = document.getElementById("status"); el.textContent = text; el.className = "status" + (kind ? " " + kind : ""); }

  // -- panels ------------------------------------------------------------------
  function capsNode(list, colorPrefix){
    var frag = document.createDocumentFragment();
    list.forEach(function(l){ var s = document.createElement("span"); s.innerHTML = svgFor(colorPrefix + l.toUpperCase()); frag.appendChild(s.firstChild); });
    return frag;
  }
  function renderBars(){
    var topIsWhite = flip;
    var wName = userWhite ? "You" : "Stellar", bName = userWhite ? "Stellar" : "You";
    var wR = userWhite ? "" : "(" + G.elo + ")", bR = userWhite ? "(" + G.elo + ")" : "";
    function fill(prefix, isWhite){
      var n = document.getElementById(prefix + "Name"); n.innerHTML = "";
      n.appendChild(document.createTextNode(isWhite ? wName : bName));
      var r = document.createElement("span"); r.className = "rating"; r.textContent = isWhite ? wR : bR; n.appendChild(r);
      document.getElementById(prefix + "Avatar").innerHTML = svgFor(isWhite ? "wK" : "bK");
      var caps = document.getElementById(prefix + "Caps"); caps.innerHTML = "";
      caps.appendChild(capsNode(isWhite ? G.capW : G.capB, isWhite ? "b" : "w"));
      var lead = isWhite ? G.balance : -G.balance;
      if (lead > 0){ var a = document.createElement("span"); a.className = "adv"; a.textContent = "+" + lead; caps.appendChild(a); }
    }
    fill("top", topIsWhite); fill("bot", !topIsWhite);
  }

  function fmt(ms){ if (ms < 0) ms = 0; var s = Math.ceil(ms / 1000), m = Math.floor(s / 60); return m + ":" + (s % 60 < 10 ? "0" : "") + (s % 60); }
  function renderClocks(){
    var top = document.getElementById("topClock"), bot = document.getElementById("botClock");
    if (!clockBase){ top.className = "clock hidden"; bot.className = "clock hidden"; return; }
    var elapsed = Date.now() - clockAt, w = clockBase.white, b = clockBase.black;
    var ticking = G.clock && G.clock.ticking && !G.over;
    if (ticking){ if (G.turn === "white") w -= elapsed; else b -= elapsed; }
    var topIsWhite = flip, topMs = topIsWhite ? w : b, botMs = topIsWhite ? b : w;
    top.textContent = fmt(topMs); bot.textContent = fmt(botMs);
    var topActive = ticking && ((G.turn === "white") === topIsWhite);
    top.className = "clock" + (topActive ? " active" : "") + (topMs < 20000 ? " low" : "");
    bot.className = "clock" + (!topActive && ticking ? " active" : "") + (botMs < 20000 ? " low" : "");
    var mine = userWhite ? w : b;
    if (ticking && G.turn === G.user && mine <= 0 && !flagged && !done){ flagged = true; send({flag: true}); }
  }
  setInterval(renderClocks, 100);

  function renderMoves(){
    var box = document.getElementById("moves"), q = G.quality || [];
    box.innerHTML = "";
    if (!G.moves.length){ var e = document.createElement("div"); e.className = "empty"; e.textContent = "No moves yet"; box.appendChild(e); shownMoves = 0; return; }
    function cell(i){
      var s = document.createElement("span");
      if (i >= G.moves.length) return s;
      s.appendChild(document.createTextNode(G.moves[i]));
      if (q[i] && Q[q[i].cls]){ var t = document.createElement("span"); t.className = "q"; t.style.color = Q[q[i].cls][1]; t.textContent = Q[q[i].cls][0]; t.title = Q[q[i].cls][2]; s.appendChild(t); }
      if (i === G.moves.length - 1) s.className = "cur";
      return s;
    }
    for (var i = 0; i < G.moves.length; i += 2){
      var row = document.createElement("div"); row.className = "mv" + (i + 1 >= shownMoves ? " new" : "");
      var n = document.createElement("span"); n.className = "n"; n.textContent = (i/2 + 1) + "."; row.appendChild(n);
      row.appendChild(cell(i)); row.appendChild(cell(i + 1));
      box.appendChild(row);
    }
    shownMoves = G.moves.length;
    box.scrollTop = box.scrollHeight;
  }

  function evalPercent(ev){
    if (!ev) return 50;
    if (ev.mate != null) return ev.mate === 0 ? (G.turn === "white" ? 0 : 100) : (ev.mate > 0 ? 100 : 0);
    var p = 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * ev.cp)) - 1);
    return Math.max(3, Math.min(97, p));
  }
  function renderEval(){
    var bar = document.getElementById("evalbar"), fill = document.getElementById("evalfill");
    bar.className = "evalbar" + (flip ? " flipped" : "");
    var pct = evalPercent(G.eval);
    fill.style.height = pct + "%";
    var txt = G.eval ? G.eval.text : "0.0";
    var whiteAhead = pct >= 50;
    document.getElementById("evalW").textContent = whiteAhead ? txt.replace("+", "") : "";
    document.getElementById("evalB").textContent = whiteAhead ? "" : txt.replace("-", "");
  }

  function lastUserQuality(){
    var q = G.quality || [], n = G.moves.length;
    var userMovesAreEven = userWhite;            // white moves sit at even indices
    for (var i = n - 1; i >= 0; i--){ if ((i % 2 === 0) === userMovesAreEven) return {i: i, q: q[i]}; }
    return null;
  }
  function renderVerdict(){
    var el = document.getElementById("verdict"); el.innerHTML = "";
    var lu = lastUserQuality(); if (!lu || !lu.q || !Q[lu.q.cls]) return;
    var meta = Q[lu.q.cls];
    var sym = document.createElement("span"); sym.className = "sym"; sym.style.background = meta[1]; sym.textContent = meta[0]; el.appendChild(sym);
    var txt = document.createElement("span"); txt.textContent = G.moves[lu.i] + " · " + meta[2]; txt.style.color = meta[1]; el.appendChild(txt);
    if ((lu.q.cls === "inaccuracy" || lu.q.cls === "mistake" || lu.q.cls === "blunder") && lu.q.best_san){
      var b = document.createElement("span"); b.className = "better"; b.textContent = " — " + lu.q.best_san + " was better"; el.appendChild(b);
    }
  }

  function renderGameOver(){
    var old = boxEl.querySelector(".gameover"); if (old) old.remove();
    if (!G.over) return;
    var youWon = (G.result === "1-0" && userWhite) || (G.result === "0-1" && !userWhite);
    var draw = G.result === "1/2-1/2";
    var ov = document.createElement("div"); ov.className = "gameover";
    var card = document.createElement("div"); card.className = "card";
    var h = document.createElement("h3"); h.textContent = draw ? "Draw" : (youWon ? "You won" : "Stellar won"); card.appendChild(h);
    var why = document.createElement("div"); why.className = "why"; why.textContent = G.status; card.appendChild(why);
    if (G.review){
      var rv = G.review, me = G.user, them = me === "white" ? "black" : "white";
      var tbl = document.createElement("table"); tbl.className = "rv";
      var thead = document.createElement("tr"); ["", "You", "Stellar"].forEach(function(t){ var th = document.createElement("th"); th.textContent = t; thead.appendChild(th); }); tbl.appendChild(thead);
      var acc = document.createElement("tr"); acc.className = "acc";
      [["Accuracy"], [rv[me].accuracy.toFixed(1) + "%"], [rv[them].accuracy.toFixed(1) + "%"]].forEach(function(t){ var td = document.createElement("td"); td.textContent = t[0]; acc.appendChild(td); }); tbl.appendChild(acc);
      ["brilliant","great","best","excellent","good","inaccuracy","mistake","blunder"].forEach(function(k){
        var a = rv[me].counts[k] || 0, b = rv[them].counts[k] || 0; if (!a && !b) return;
        var tr = document.createElement("tr");
        var td0 = document.createElement("td"); var sym = document.createElement("span"); sym.className = "sym"; sym.style.background = Q[k][1]; sym.textContent = Q[k][0]; td0.appendChild(sym); td0.appendChild(document.createTextNode(Q[k][2])); tr.appendChild(td0);
        var td1 = document.createElement("td"); td1.textContent = a; tr.appendChild(td1);
        var td2 = document.createElement("td"); td2.textContent = b; tr.appendChild(td2);
        tbl.appendChild(tr);
      });
      card.appendChild(tbl);
    }
    var btns = document.createElement("div"); btns.className = "btns";
    var again = document.createElement("button"); again.className = "btn primary"; again.textContent = "Play again"; again.addEventListener("click", function(){ send({newgame: true}); });
    var close = document.createElement("button"); close.className = "btn"; close.textContent = "Close"; close.addEventListener("click", function(){ send({exit: true}); });
    btns.appendChild(again); btns.appendChild(close); card.appendChild(btns);
    ov.appendChild(card); boxEl.appendChild(ov);
  }

  function paint(fresh){
    render(); renderBars(); renderMoves(); renderEval(); renderVerdict(); renderClocks();
    setStatus(G.status, G.over ? "over" : (G.waiting ? "turn" : ""));
    setButtons(true);
    if (!G.waiting && !G.over) document.getElementById("askBtn").disabled = true;
    document.getElementById("resign").textContent = G.over ? "Close" : "Resign";
    if (!fresh && G.last && G.last !== lastLocalMove){
      var to = G.last.slice(2,4), moverWhite = G.turn !== "white";
      var taken = prevPieces[to] && prevPieces[to].w !== moverWhite ? prevPieces[to] : null;
      animate(G.last, taken);
      if (G.over) SFX.end(); else if (G.check) SFX.check(); else (taken ? SFX.capture : SFX.move)();
    } else if (!fresh && G.over){ SFX.end(); }
    var lu = lastUserQuality();
    if (!fresh && lu && lu.q && (lu.q.cls === "mistake" || lu.q.cls === "blunder" || lu.q.cls === "inaccuracy") && lu.q.best_uci && lu.i >= G.moves.length - 2){
      drawArrow(lu.q.best_uci, Q[lu.q.cls][1]); if (lu.q.cls !== "inaccuracy") SFX.bad();
    } else if (!fresh && lu && lu.q && (lu.q.cls === "brilliant" || lu.q.cls === "great") && lu.i >= G.moves.length - 2){ SFX.good(); }
    renderGameOver();
  }

  document.getElementById("askBtn").addEventListener("click", function(){ var v = document.getElementById("askIn").value.trim(); if (v) send({ask: v}); });
  document.getElementById("askIn").addEventListener("keydown", function(e){ if (e.key === "Enter"){ var v = this.value.trim(); if (v) send({ask: v}); } });
  document.getElementById("resign").addEventListener("click", function(){ send({exit: true, resign: !G.over}); });
  document.getElementById("newgame").addEventListener("click", function(){ send({newgame: true}); });
  document.getElementById("soundBtn").addEventListener("click", function(){ muted = !muted; this.textContent = muted ? "Sound off" : "Sound on"; });

  window.addEventListener("stellar:update", function(e){ load(e.detail); paint(false); });

  load(G);
  paint(true);
})();
</script>
"""

_TEMPLATE = _TEMPLATE.replace("__PIECES__", _PIECES_JS)
