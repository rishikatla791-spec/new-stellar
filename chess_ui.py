"""The chessboard widget, rendered by the server from the position.

A board is a pure function of the position. The model is not asked to draw
it; this module does, once, properly, and the same widget is then updated in
place for every move of a game.

Pieces are the Cburnett set - the open-source vector pieces that lichess and
Wikipedia use - inlined as SVG so they render identically on every machine.
Font glyphs were tried first and looked like a text document; they vary by
operating system and cannot show a white piece as white on a dark board.

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
               clock: dict | None = None) -> dict:
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
        # {"white": ms, "black": ms, "ticking": bool} or None for untimed
        "clock": clock,
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
  .cw{--sq:60px;--light:#eeeed2;--dark:#769656;
      --hl:rgba(255,255,51,.45);--sel:rgba(255,200,0,.62);
      font-family:var(--font);color:var(--text);
      display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
  .cw .bw{flex:0 0 auto;width:calc(var(--sq)*8)}
  .cw .bar{display:flex;align-items:center;gap:10px;height:46px;padding:4px 0}
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
      min-width:92px;text-align:center;font-variant-numeric:tabular-nums}
  .cw .clock.active{background:#e6e8ee;color:#141414}
  .cw .clock.low{color:#ff6b6b} .cw .clock.active.low{background:#ffd7d7;color:#a51d1d}
  .cw .clock.hidden{visibility:hidden}
  .cw .boardbox{position:relative}
  .cw .board{display:grid;grid-template-columns:repeat(8,var(--sq));
      grid-template-rows:repeat(8,var(--sq));border-radius:4px;overflow:hidden;
      box-shadow:0 3px 14px rgba(0,0,0,.5);user-select:none}
  .cw .sq{position:relative;display:flex;align-items:center;justify-content:center}
  .cw .sq.l{background:var(--light)} .cw .sq.d{background:var(--dark)}
  .cw .sq.last::before{content:"";position:absolute;inset:0;background:var(--hl)}
  .cw .sq.sel::before{content:"";position:absolute;inset:0;background:var(--sel)}
  .cw .sq.check::before{content:"";position:absolute;inset:0;
      background:radial-gradient(circle,rgba(255,20,20,.95) 0%,rgba(255,20,20,.4) 52%,transparent 70%)}
  .cw .sq.dot::after{content:"";position:absolute;width:30%;height:30%;border-radius:50%;
      background:rgba(0,0,0,.2);z-index:2}
  .cw .sq.cap::after{content:"";position:absolute;inset:2px;border-radius:50%;
      border:6px solid rgba(0,0,0,.2);z-index:2}
  .cw .sq.can{cursor:pointer}
  .cw .sq.can:hover::after{background:rgba(0,0,0,.3)}
  .cw .sq.cap.can:hover::after{border-color:rgba(0,0,0,.3);background:none}
  .cw .sq.own{cursor:pointer}
  .cw .sq.own:hover{filter:brightness(1.05)}
  .cw .p{position:absolute;inset:0;z-index:1;pointer-events:none;
      display:flex;align-items:center;justify-content:center;will-change:transform}
  .cw .p svg{width:90%;height:90%;filter:drop-shadow(0 1px 1px rgba(0,0,0,.35))}
  .cw .p.slide{transition:transform .14s ease-out}
  .cw .lbl{position:absolute;font-size:10.5px;font-weight:700;pointer-events:none;z-index:2;opacity:.9}
  .cw .lbl.f{right:3px;bottom:1px} .cw .lbl.r{left:3px;top:2px}
  .cw .sq.l .lbl{color:#769656} .cw .sq.d .lbl{color:#eeeed2}
  .cw .promo{position:absolute;z-index:5;display:none;flex-direction:column;
      background:#fff;border-radius:6px;box-shadow:0 4px 18px rgba(0,0,0,.6);overflow:hidden}
  .cw .promo.show{display:flex}
  .cw .promo button{width:var(--sq);height:var(--sq);border:0;background:#fff;padding:6px;cursor:pointer}
  .cw .promo button:hover{background:#ffe08a}
  .cw .promo button svg{width:100%;height:100%}
  .cw .panel{flex:1 1 200px;min-width:190px;max-width:250px;
      display:flex;flex-direction:column;gap:9px;align-self:stretch;padding-top:50px}
  .cw .status{font-size:14px;padding:9px 11px;border-radius:8px;
      background:var(--surface);border:1px solid var(--border)}
  .cw .status.turn{border-color:var(--accent)}
  .cw .status.over{border-color:var(--good)}
  .cw .status.err{border-color:var(--bad)}
  .cw .ttl{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--text-dim);margin-top:2px}
  .cw .moves{flex:1;min-height:120px;max-height:calc(var(--sq)*8 - 230px);overflow-y:auto;
      background:var(--surface);border:1px solid var(--border);border-radius:8px;
      font-family:var(--mono);font-size:13px}
  .cw .moves .empty{padding:10px;color:var(--text-dim);font-family:var(--font);font-size:13px}
  .cw .mv{display:grid;grid-template-columns:30px 1fr 1fr;padding:3px 8px;border-bottom:1px solid var(--border)}
  .cw .mv:last-child{border-bottom:0}
  .cw .mv .n{color:var(--text-dim)}
  .cw .mv .cur{background:rgba(109,140,255,.28);border-radius:4px;padding:0 5px;margin:0 -5px}
  .cw .ask{display:flex;gap:6px}
  .cw .ask input{flex:1;min-width:0;background:var(--surface);border:1px solid var(--border);
      border-radius:8px;padding:8px 10px;color:var(--text);font-family:inherit;font-size:13px}
  .cw .ask input:focus{outline:none;border-color:var(--accent)}
  .cw button.btn{background:var(--surface);border:1px solid var(--border);color:var(--text);
      border-radius:8px;padding:8px 12px;font-size:13px;white-space:nowrap;font-family:inherit;cursor:pointer}
  .cw button.btn:hover{border-color:var(--text-dim)}
  .cw button.btn.danger:hover{border-color:var(--bad);color:var(--bad)}
  .cw button.btn:disabled{opacity:.45;cursor:not-allowed}
  .cw .actions{display:flex;gap:6px;justify-content:space-between}
  .cw.busy .sq{cursor:default!important;filter:none!important}
  @media (max-width:700px){
    .cw{--sq:min(11.6vw,50px)}
    .cw .panel{max-width:none;flex-basis:100%;padding-top:0}
    .cw .moves{max-height:160px}
    .cw .clock{font-size:18px;min-width:78px}
  }
</style>

<div class="cw" id="cw">
  <div class="bw">
    <div class="bar" id="topBar">
      <div class="avatar" id="topAvatar"></div>
      <div class="who"><div class="name" id="topName"></div><div class="caps" id="topCaps"></div></div>
      <div class="clock" id="topClock">--:--</div>
    </div>
    <div class="boardbox">
      <div class="board" id="board" aria-label="Chessboard"></div>
      <div class="promo" id="promo"></div>
    </div>
    <div class="bar" id="botBar">
      <div class="avatar" id="botAvatar"></div>
      <div class="who"><div class="name" id="botName"></div><div class="caps" id="botCaps"></div></div>
      <div class="clock" id="botClock">--:--</div>
    </div>
  </div>
  <div class="panel">
    <div class="status" id="status"></div>
    <div class="ttl">Moves</div>
    <div class="moves" id="moves"></div>
    <div class="ask">
      <input id="askIn" placeholder="Ask about the position…" autocomplete="off">
      <button class="btn" id="askBtn">Ask</button>
    </div>
    <div class="actions">
      <button class="btn danger" id="resign">Resign</button>
      <button class="btn" id="newgame">New game</button>
    </div>
  </div>
</div>

<script>
(function(){
  var PIECES = __PIECES__;
  var G = __DATA__;
  var FILES = "abcdefgh";
  var root = document.getElementById("cw");
  var boardEl = document.getElementById("board");
  var promoEl = document.getElementById("promo");
  var sel = null, done = false, pieces = {}, legalFrom = {}, userWhite, flip;
  var lastLocalMove = null, clockBase = null, clockAt = 0, flagged = false;

  function svgFor(key){
    return '<svg viewBox="0 0 45 45" xmlns="http://www.w3.org/2000/svg">' + PIECES[key] + '</svg>';
  }
  function pieceKey(p){ return (p.w ? "w" : "b") + p.t.toUpperCase(); }

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
    pieces = parseFen(G.fen);
    legalFrom = {};
    G.legal.forEach(function(u){
      var f = u.slice(0,2), t = u.slice(2,4);
      var slot = (legalFrom[f] = legalFrom[f] || {});
      // Promotions: keep all four under the destination so a chooser can
      // be offered rather than silently queening.
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
    var col = flip ? 7 - file : file, row = flip ? rank - 1 : 8 - rank;
    return {col: col, row: row};
  }

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
        d.addEventListener("click", onClick);
        boardEl.appendChild(d);
      }
    }
  }

  /* Slide the piece on `to` in from `from`: render the final position, then
   * start the moved piece offset by the square delta and let a transition
   * carry it home. */
  function animate(uci){
    if (!uci) return;
    var from = sqPos(uci.slice(0,2)), to = sqPos(uci.slice(2,4));
    var el = boardEl.querySelector('.p[data-sq="' + uci.slice(2,4) + '"]');
    if (!el) return;
    var sq = boardEl.firstChild.getBoundingClientRect().width || 60;
    var dx = (from.col - to.col) * sq, dy = (from.row - to.row) * sq;
    el.style.transform = "translate(" + dx + "px," + dy + "px)";
    el.getBoundingClientRect();
    el.classList.add("slide");
    el.style.transform = "translate(0,0)";
  }

  /* Show the user's move the instant they make it, before the server
   * confirms. Castling moves the rook, en passant removes the pawn. The
   * server still validates; this is only what the eye sees meanwhile. */
  function applyLocal(uci){
    var from = uci.slice(0,2), to = uci.slice(2,4), promo = uci[4];
    var p = pieces[from]; if (!p) return;
    delete pieces[from];
    if (p.t === "k" && Math.abs(FILES.indexOf(to[0]) - FILES.indexOf(from[0])) === 2){
      var rank = from[1];
      var rookFrom = (to[0] === "g" ? "h" : "a") + rank, rookTo = (to[0] === "g" ? "f" : "d") + rank;
      pieces[rookTo] = pieces[rookFrom]; delete pieces[rookFrom];
    }
    if (p.t === "p" && from[0] !== to[0] && !pieces[to]) delete pieces[to[0] + from[1]];
    if (promo) p = {t: promo, w: p.w};
    pieces[to] = p;
    G.last = uci; G.waiting = false; G.turn = userWhite ? "black" : "white"; G.check = false;
    sel = null;
    render(); animate(uci);
    setStatus("Thinking…", "");
    renderClocks();
  }

  function onClick(){
    if (!G.waiting || done) return;
    var sq = this.dataset.sq, p = pieces[sq];
    if (sel && legalFrom[sel] && legalFrom[sel][sq]){
      var target = legalFrom[sel][sq];
      if (typeof target === "object"){ offerPromotion(sel, sq, target); return; }
      makeMove(target); return;
    }
    if (p && p.w === userWhite && legalFrom[sq]){ sel = (sel === sq ? null : sq); render(); return; }
    sel = null; render();
  }

  function offerPromotion(from, to, options){
    promoEl.innerHTML = "";
    var pos = sqPos(to), sqW = boardEl.firstChild.getBoundingClientRect().width || 60;
    var order = ["q","r","b","n"];
    var atTop = pos.row === 0;
    (atTop ? order : order.slice().reverse()).forEach(function(k){
      var b = document.createElement("button");
      b.innerHTML = svgFor((userWhite ? "w" : "b") + k.toUpperCase());
      b.addEventListener("click", function(){ promoEl.classList.remove("show"); makeMove(options[k]); });
      promoEl.appendChild(b);
    });
    promoEl.style.left = (pos.col * sqW) + "px";
    promoEl.style.top = (atTop ? 0 : (pos.row * sqW - 3 * sqW)) + "px";
    promoEl.classList.add("show");
  }

  function makeMove(uci){
    lastLocalMove = uci;
    applyLocal(uci);
    send({move: uci});
  }

  function send(data){
    if (done) return;
    done = true;
    root.classList.add("busy");
    if (!data.move) setStatus(data.ask ? "Asking Stellar…" : data.flag ? "Out of time" : "Leaving the game…", "");
    setButtons(false);
    window.stellar.finish(data);
  }

  function setButtons(on){
    ["askBtn","resign","newgame"].forEach(function(id){ document.getElementById(id).disabled = !on; });
  }

  function setStatus(text, kind){
    var el = document.getElementById("status");
    el.textContent = text;
    el.className = "status" + (kind ? " " + kind : "");
  }

  function capsNode(list, colorPrefix){
    var frag = document.createDocumentFragment();
    list.forEach(function(l){ var s = document.createElement("span"); s.innerHTML = svgFor(colorPrefix + l.toUpperCase()); frag.appendChild(s.firstChild); });
    return frag;
  }

  function renderBars(){
    var topIsWhite = flip;
    var whiteName = userWhite ? "You" : "Stellar", blackName = userWhite ? "Stellar" : "You";
    var whiteRating = userWhite ? "" : "(" + G.elo + ")", blackRating = userWhite ? "(" + G.elo + ")" : "";
    function fill(prefix, isWhite){
      var n = document.getElementById(prefix + "Name");
      n.innerHTML = "";
      n.appendChild(document.createTextNode(isWhite ? whiteName : blackName));
      var r = document.createElement("span"); r.className = "rating"; r.textContent = isWhite ? whiteRating : blackRating; n.appendChild(r);
      document.getElementById(prefix + "Avatar").innerHTML = svgFor(isWhite ? "wK" : "bK");
      var caps = document.getElementById(prefix + "Caps"); caps.innerHTML = "";
      caps.appendChild(capsNode(isWhite ? G.capW : G.capB, isWhite ? "b" : "w"));
      var lead = isWhite ? G.balance : -G.balance;
      if (lead > 0){ var a = document.createElement("span"); a.className = "adv"; a.textContent = "+" + lead; caps.appendChild(a); }
    }
    fill("top", topIsWhite); fill("bot", !topIsWhite);
  }

  function fmt(ms){
    if (ms < 0) ms = 0;
    var s = Math.ceil(ms / 1000), m = Math.floor(s / 60);
    return m + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  }

  function renderClocks(){
    var top = document.getElementById("topClock"), bot = document.getElementById("botClock");
    if (!clockBase){ top.className = "clock hidden"; bot.className = "clock hidden"; return; }
    var elapsed = Date.now() - clockAt;
    var w = clockBase.white, b = clockBase.black;
    var ticking = G.clock && G.clock.ticking && !G.over;
    if (ticking){ if (G.turn === "white") w -= elapsed; else b -= elapsed; }
    var topIsWhite = flip;
    var topMs = topIsWhite ? w : b, botMs = topIsWhite ? b : w;
    top.textContent = fmt(topMs); bot.textContent = fmt(botMs);
    var topActive = ticking && ((G.turn === "white") === topIsWhite);
    top.className = "clock" + (topActive ? " active" : "") + (topMs < 20000 ? " low" : "");
    bot.className = "clock" + (!topActive && ticking ? " active" : "") + (botMs < 20000 ? " low" : "");
    var mine = userWhite ? w : b;
    if (ticking && G.turn === G.user && mine <= 0 && !flagged && !done){ flagged = true; send({flag: true}); }
  }
  setInterval(renderClocks, 100);

  function renderMoves(){
    var box = document.getElementById("moves");
    box.innerHTML = "";
    if (!G.moves.length){ var e = document.createElement("div"); e.className = "empty"; e.textContent = "No moves yet"; box.appendChild(e); return; }
    for (var i = 0; i < G.moves.length; i += 2){
      var row = document.createElement("div"); row.className = "mv";
      var n = document.createElement("span"); n.className = "n"; n.textContent = (i/2 + 1) + "."; row.appendChild(n);
      var w = document.createElement("span"); w.textContent = G.moves[i]; if (i === G.moves.length - 1) w.className = "cur"; row.appendChild(w);
      var b = document.createElement("span"); b.textContent = G.moves[i+1] || ""; if (i + 1 === G.moves.length - 1) b.className = "cur"; row.appendChild(b);
      box.appendChild(row);
    }
    box.scrollTop = box.scrollHeight;
  }

  function paint(fresh){
    render();
    renderBars();
    renderMoves();
    renderClocks();
    setStatus(G.status, G.over ? "over" : (G.waiting ? "turn" : ""));
    setButtons(true);
    if (!G.waiting && !G.over) document.getElementById("askBtn").disabled = true;
    if (G.over) document.getElementById("resign").textContent = "Close";
    // The engine's reply slides in; the user's own move already did.
    if (!fresh && G.last && G.last !== lastLocalMove) animate(G.last);
  }

  document.getElementById("askBtn").addEventListener("click", function(){
    var v = document.getElementById("askIn").value.trim(); if (v) send({ask: v});
  });
  document.getElementById("askIn").addEventListener("keydown", function(e){
    if (e.key === "Enter"){ var v = this.value.trim(); if (v) send({ask: v}); }
  });
  document.getElementById("resign").addEventListener("click", function(){ send({exit: true, resign: !G.over}); });
  document.getElementById("newgame").addEventListener("click", function(){ send({newgame: true}); });

  // In-place updates: the parent forwards new position data; nothing reloads.
  window.addEventListener("stellar:update", function(e){ load(e.detail); paint(false); });

  load(G);
  paint(true);
})();
</script>
"""

_TEMPLATE = _TEMPLATE.replace("__PIECES__", _PIECES_JS)
