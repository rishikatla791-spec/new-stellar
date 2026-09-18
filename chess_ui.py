"""The chessboard widget, rendered by the server from the position.

The model used to write this HTML itself, every move. That cost a full model
round trip per render, and produced a different board each time - once with
outline glyphs for one side and filled for the other, so both colours looked
identical on a dark background. A board is a pure function of the position:
there is no reason for a language model to be in the loop drawing it.

This module renders one designed board from FEN and the move list. It is
handed to the browser through the same widget channel the model uses, so
the click handling and the pause are unchanged - only the author differs.
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


def render(board: chess.Board, moves_san: list[str], *, user_color: str,
           elo: int, status_text: str, waiting: bool, result: str | None = None,
           last_move: str | None = None) -> str:
    """Build the widget HTML for a position."""
    by_white, by_black, balance = captured(board)

    data = {
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
    }
    # A closing tag inside the JSON would end the script block early.
    payload = json.dumps(data).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", payload)


_TEMPLATE = r"""
<style>
  .cw{--sq:54px;--light:#eeeed2;--dark:#769656;
      --hl:rgba(255,255,51,.42);--sel:rgba(255,200,0,.6);
      font-family:var(--font);color:var(--text);
      display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
  .cw .bw{flex:0 0 auto}
  .cw .bar{display:flex;justify-content:space-between;align-items:center;
      height:34px;padding:0 2px;font-size:13.5px}
  .cw .bar .who{display:flex;align-items:baseline;gap:8px}
  .cw .name{font-weight:500}
  .cw .sub{color:var(--text-dim);font-size:12px}
  .cw .caps{display:flex;align-items:center;gap:1px;font-size:17px;line-height:1;
      font-family:"Segoe UI Symbol","Noto Sans Symbols 2","DejaVu Sans","Apple Symbols",sans-serif}
  .cw .caps .cp.w{color:#f4f4f4;text-shadow:0 0 1px #000,0 0 2px rgba(0,0,0,.8)}
  .cw .caps .cp.b{color:#1a1a1a;text-shadow:0 0 1px rgba(255,255,255,.5)}
  .cw .caps .adv{font-size:12px;color:var(--text-dim);margin-left:5px;font-family:var(--mono)}
  .cw .board{display:grid;grid-template-columns:repeat(8,var(--sq));
      grid-template-rows:repeat(8,var(--sq));border-radius:5px;overflow:hidden;
      box-shadow:0 3px 14px rgba(0,0,0,.5);user-select:none}
  .cw .sq{position:relative;display:flex;align-items:center;justify-content:center}
  .cw .sq.l{background:var(--light)} .cw .sq.d{background:var(--dark)}
  .cw .sq.last::before{content:"";position:absolute;inset:0;background:var(--hl)}
  .cw .sq.sel::before{content:"";position:absolute;inset:0;background:var(--sel)}
  .cw .sq.check::before{content:"";position:absolute;inset:0;
      background:radial-gradient(circle,rgba(255,30,30,.9) 0%,rgba(255,30,30,.35) 55%,transparent 72%)}
  .cw .sq.dot::after{content:"";position:absolute;width:32%;height:32%;border-radius:50%;
      background:rgba(0,0,0,.22)}
  .cw .sq.cap::after{content:"";position:absolute;inset:3px;border-radius:50%;
      border:5px solid rgba(0,0,0,.22)}
  .cw .sq.can{cursor:pointer}
  .cw .sq.can:hover::after{background:rgba(0,0,0,.34)}
  .cw .sq.cap.can:hover::after{border-color:rgba(0,0,0,.34);background:none}
  .cw .sq.own{cursor:pointer}
  .cw .sq.own:hover{filter:brightness(1.06)}
  .cw .p{position:relative;z-index:1;line-height:1;pointer-events:none;
      font-size:calc(var(--sq)*.78);
      font-family:"Segoe UI Symbol","Noto Sans Symbols 2","DejaVu Sans","Apple Symbols",sans-serif}
  /* Both sides use the FILLED glyph and are coloured by CSS. Using outline
     glyphs for white makes the two sides indistinguishable at a glance. */
  .cw .p.w{color:#fafafa;text-shadow:0 0 1px #000,0 0 3px rgba(0,0,0,.85),1px 1px 1px rgba(0,0,0,.9)}
  .cw .p.b{color:#141414;text-shadow:0 0 1px rgba(255,255,255,.45),0 0 2px rgba(255,255,255,.3)}
  .cw .lbl{position:absolute;font-size:9.5px;font-weight:600;opacity:.8;pointer-events:none}
  .cw .lbl.f{right:3px;bottom:1px} .cw .lbl.r{left:3px;top:2px}
  .cw .sq.l .lbl{color:#769656} .cw .sq.d .lbl{color:#eeeed2}
  .cw .panel{flex:1 1 210px;min-width:200px;max-width:270px;
      display:flex;flex-direction:column;gap:9px;align-self:stretch}
  .cw .status{font-size:14px;padding:9px 11px;border-radius:8px;
      background:var(--surface);border:1px solid var(--border)}
  .cw .status.turn{border-color:var(--accent)}
  .cw .status.over{border-color:var(--good)}
  .cw .status.err{border-color:var(--bad)}
  .cw .ttl{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--text-dim);
      margin-top:2px}
  .cw .moves{flex:1;min-height:120px;max-height:calc(var(--sq)*8 - 150px);overflow-y:auto;
      background:var(--surface);border:1px solid var(--border);border-radius:8px;
      font-family:var(--mono);font-size:13px}
  .cw .moves .empty{padding:10px;color:var(--text-dim);font-family:var(--font);font-size:13px}
  .cw .mv{display:grid;grid-template-columns:32px 1fr 1fr;padding:3px 8px;
      border-bottom:1px solid var(--border)}
  .cw .mv:last-child{border-bottom:0}
  .cw .mv .n{color:var(--text-dim)}
  .cw .mv .cur{background:rgba(109,140,255,.28);border-radius:4px;padding:0 5px;margin:0 -5px}
  .cw .ask{display:flex;gap:6px}
  .cw .ask input{flex:1;min-width:0;background:var(--surface);border:1px solid var(--border);
      border-radius:8px;padding:8px 10px;color:var(--text);font-family:inherit;font-size:13px}
  .cw .ask input:focus{outline:none;border-color:var(--accent)}
  .cw button{background:var(--surface);border:1px solid var(--border);color:var(--text);
      border-radius:8px;padding:8px 12px;font-size:13px;white-space:nowrap}
  .cw button:hover{border-color:var(--text-dim)}
  .cw button.danger:hover{border-color:var(--bad);color:var(--bad)}
  .cw button:disabled{opacity:.45;cursor:not-allowed}
  .cw .actions{display:flex;gap:6px;justify-content:space-between}
  .cw.busy .board{opacity:.88}
  .cw.busy .sq{cursor:default!important;filter:none!important}
  @media (max-width:640px){
    .cw{--sq:min(11.4vw,46px)}
    .cw .panel{max-width:none;flex-basis:100%}
    .cw .moves{max-height:160px}
  }
</style>

<div class="cw" id="cw">
  <div class="bw">
    <div class="bar">
      <span class="who"><span class="name" id="topName"></span><span class="sub" id="topSub"></span></span>
      <span class="caps" id="topCaps"></span>
    </div>
    <div class="board" id="board" aria-label="Chessboard"></div>
    <div class="bar">
      <span class="who"><span class="name" id="botName"></span><span class="sub" id="botSub"></span></span>
      <span class="caps" id="botCaps"></span>
    </div>
  </div>
  <div class="panel">
    <div class="status" id="status"></div>
    <div class="ttl">Moves</div>
    <div class="moves" id="moves"></div>
    <div class="ask">
      <input id="askIn" placeholder="Ask about the position…" autocomplete="off">
      <button id="askBtn">Ask</button>
    </div>
    <div class="actions">
      <button id="resign" class="danger">Resign</button>
      <button id="newgame">New game</button>
    </div>
  </div>
</div>

<script>
(function(){
  var G = __DATA__;
  var FILES = "abcdefgh";
  var GLYPH = {p:"♟", n:"♞", b:"♝", r:"♜", q:"♛", k:"♚"};
  var root = document.getElementById("cw");
  var boardEl = document.getElementById("board");
  var sel = null, done = false;
  var userWhite = G.user === "white";
  var flip = !userWhite;

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
  var pieces = parseFen(G.fen);

  // from-square -> to-square -> uci. Promotions default to queen; the
  // rook/bishop/knight variants are dropped so one click means one move.
  var legalFrom = {};
  G.legal.forEach(function(u){
    if (u.length === 5 && u[4] !== "q") return;
    var f = u.slice(0,2), t = u.slice(2,4);
    (legalFrom[f] = legalFrom[f] || {})[t] = u;
  });

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
          var s = document.createElement("span");
          s.className = "p " + (p.w ? "w" : "b");
          s.textContent = GLYPH[p.t];
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

  function onClick(){
    if (!G.waiting || done) return;
    var sq = this.dataset.sq, p = pieces[sq];
    if (sel && legalFrom[sel] && legalFrom[sel][sq]) { send({move: legalFrom[sel][sq]}); return; }
    if (p && p.w === userWhite && legalFrom[sq]) { sel = (sel === sq ? null : sq); render(); return; }
    sel = null; render();
  }

  function send(data){
    if (done) return;
    done = true;
    root.classList.add("busy");
    setStatus(data.move ? "Sending…" : data.ask ? "Asking Stellar…" : "Leaving the game…", "");
    document.getElementById("askBtn").disabled = true;
    document.getElementById("resign").disabled = true;
    document.getElementById("newgame").disabled = true;
    window.stellar.finish(data);
  }

  function setStatus(text, kind){
    var el = document.getElementById("status");
    el.textContent = text;
    el.className = "status" + (kind ? " " + kind : "");
  }

  function capsHtml(list, colorClass){
    var span = document.createElement("span");
    list.forEach(function(l){ var c = document.createElement("span"); c.className = "cp " + colorClass; c.textContent = GLYPH[l]; span.appendChild(c); });
    return span;
  }

  function renderBars(){
    var you = "You", bot = "Stellar";
    var youSub = userWhite ? "White" : "Black";
    var botSub = (userWhite ? "Black" : "White") + " · " + G.elo;
    var top = flip ? [you, youSub] : [bot, botSub];
    var bottom = flip ? [bot, botSub] : [you, youSub];
    document.getElementById("topName").textContent = top[0];
    document.getElementById("topSub").textContent = top[1];
    document.getElementById("botName").textContent = bottom[0];
    document.getElementById("botSub").textContent = bottom[1];

    // Pieces a side has captured sit beside that side's name.
    var whiteCaps = capsHtml(G.capW, "b"), blackCaps = capsHtml(G.capB, "w");
    var topCaps = document.getElementById("topCaps"), botCaps = document.getElementById("botCaps");
    topCaps.innerHTML = ""; botCaps.innerHTML = "";
    var topIsWhite = flip;
    (topIsWhite ? topCaps : botCaps).appendChild(whiteCaps);
    (topIsWhite ? botCaps : topCaps).appendChild(blackCaps);
    if (G.balance !== 0){
      var adv = document.createElement("span"); adv.className = "adv";
      adv.textContent = "+" + Math.abs(G.balance);
      var whiteAhead = G.balance > 0;
      ((whiteAhead === topIsWhite) ? topCaps : botCaps).appendChild(adv);
    }
  }

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

  document.getElementById("askBtn").addEventListener("click", function(){
    var v = document.getElementById("askIn").value.trim(); if (v) send({ask: v});
  });
  document.getElementById("askIn").addEventListener("keydown", function(e){
    if (e.key === "Enter"){ var v = this.value.trim(); if (v) send({ask: v}); }
  });
  document.getElementById("resign").addEventListener("click", function(){ send({exit: true, resign: !G.over}); });
  document.getElementById("newgame").addEventListener("click", function(){ send({newgame: true}); });

  render();
  renderBars();
  renderMoves();
  setStatus(G.status, G.over ? "over" : (G.waiting ? "turn" : ""));
  if (G.over){ document.getElementById("resign").textContent = "Close"; }
  if (!G.waiting && !G.over){ document.getElementById("askBtn").disabled = true; }
})();
</script>
"""
