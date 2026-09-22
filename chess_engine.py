"""A real chess engine, so the model never has to remember the board.

The reference project forbids this on purpose - its prompt says the model
must "evaluate the board directly using your own neural network weights".
That is why it plays illegal moves. Nothing in that system knows the rules:
the model is asked to be the board, the rulebook and the player at once, and
across twenty turns of text its mental board drifts until it moves a knight
that left the square three moves ago.

Splitting those jobs fixes it completely:

    python-chess   owns the position and the legal move list
    this module    evaluates and searches for tactics
    the model      chooses among strong legal moves and explains why

The model is still playing - it picks the move and does the talking. It just
cannot hallucinate a position or play something illegal, because the legal
list is generated, not recalled.

Strength comes from alpha-beta with quiescence, which is what stops a search
from happily "winning" a queen it is about to lose on the next ply.
"""

from __future__ import annotations

import pathlib
import time

import chess
import chess.engine

# Centipawns. The king's value is nominal - checkmate is scored separately,
# and giving it a finite value would let the search trade it.
PIECE_VALUES = {
    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0,
}

MATE_SCORE = 100_000
# Mates as seen by the reviewer: large enough to outrank any material,
# small enough that a mate-in-1 and a mate-in-5 still compare sensibly.
MATE_CP = 10_000

# Piece-square tables: positional knowledge the search would otherwise need
# far more depth to discover. Written from White's point of view and mirrored
# for Black. They encode ordinary chess principles - knights belong in the
# centre, rooks on open files and the seventh, pawns want to advance.
_PAWN = [
     0,  0,  0,  0,  0,  0,  0,  0,
    50, 50, 50, 50, 50, 50, 50, 50,
    10, 10, 20, 30, 30, 20, 10, 10,
     5,  5, 10, 25, 25, 10,  5,  5,
     0,  0,  0, 20, 20,  0,  0,  0,
     5, -5,-10,  0,  0,-10, -5,  5,
     5, 10, 10,-20,-20, 10, 10,  5,
     0,  0,  0,  0,  0,  0,  0,  0,
]
_KNIGHT = [
    -50,-40,-30,-30,-30,-30,-40,-50,
    -40,-20,  0,  0,  0,  0,-20,-40,
    -30,  0, 10, 15, 15, 10,  0,-30,
    -30,  5, 15, 20, 20, 15,  5,-30,
    -30,  0, 15, 20, 20, 15,  0,-30,
    -30,  5, 10, 15, 15, 10,  5,-30,
    -40,-20,  0,  5,  5,  0,-20,-40,
    -50,-40,-30,-30,-30,-30,-40,-50,
]
_BISHOP = [
    -20,-10,-10,-10,-10,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5, 10, 10,  5,  0,-10,
    -10,  5,  5, 10, 10,  5,  5,-10,
    -10,  0, 10, 10, 10, 10,  0,-10,
    -10, 10, 10, 10, 10, 10, 10,-10,
    -10,  5,  0,  0,  0,  0,  5,-10,
    -20,-10,-10,-10,-10,-10,-10,-20,
]
_ROOK = [
      0,  0,  0,  0,  0,  0,  0,  0,
      5, 10, 10, 10, 10, 10, 10,  5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
      0,  0,  0,  5,  5,  0,  0,  0,
]
_QUEEN = [
    -20,-10,-10, -5, -5,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5,  5,  5,  5,  0,-10,
     -5,  0,  5,  5,  5,  5,  0, -5,
      0,  0,  5,  5,  5,  5,  0, -5,
    -10,  5,  5,  5,  5,  5,  0,-10,
    -10,  0,  5,  0,  0,  0,  0,-10,
    -20,-10,-10, -5, -5,-10,-10,-20,
]
# Two king tables. In the middlegame the king wants to hide behind pawns; in
# the endgame it is a strong piece and belongs in the centre. Using only the
# first would make the engine cower in the corner in king-and-pawn endings.
_KING_MID = [
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -20,-30,-30,-40,-40,-30,-30,-20,
    -10,-20,-20,-20,-20,-20,-20,-10,
     20, 20,  0,  0,  0,  0, 20, 20,
     20, 30, 10,  0,  0, 10, 30, 20,
]
_KING_END = [
    -50,-40,-30,-20,-20,-30,-40,-50,
    -30,-20,-10,  0,  0,-10,-20,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-10, 30, 40, 40, 30,-10,-30,
    -30,-10, 30, 40, 40, 30,-10,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-30,  0,  0,  0,  0,-30,-30,
    -50,-30,-30,-30,-30,-30,-30,-50,
]

_TABLES = {
    chess.PAWN: _PAWN, chess.KNIGHT: _KNIGHT, chess.BISHOP: _BISHOP,
    chess.ROOK: _ROOK, chess.QUEEN: _QUEEN,
}


def _is_endgame(board: chess.Board) -> bool:
    """Endgame once the queens are gone or heavily reduced material remains."""
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + \
        len(board.pieces(chess.QUEEN, chess.BLACK))
    if queens == 0:
        return True
    minors = sum(len(board.pieces(pt, c))
                 for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK)
                 for c in (chess.WHITE, chess.BLACK))
    return queens <= 2 and minors <= 4


def evaluate(board: chess.Board) -> int:
    """Score the position in centipawns, from the side-to-move's point of view.

    Terminal positions first: a checkmate is worth more than any amount of
    material, and a draw is exactly zero regardless of who is "winning" on
    material - which is what lets the search find and avoid stalemate traps.
    """
    if board.is_checkmate():
        return -MATE_SCORE
    if board.is_stalemate() or board.is_insufficient_material() or \
            board.is_repetition(3) or board.is_fifty_moves():
        return 0

    endgame = _is_endgame(board)
    score = 0

    for square, piece in board.piece_map().items():
        value = PIECE_VALUES[piece.piece_type]

        if piece.piece_type == chess.KING:
            table = _KING_END if endgame else _KING_MID
        else:
            table = _TABLES[piece.piece_type]

        # Tables are written from White's perspective, so Black reads them
        # from the mirrored square.
        index = square if piece.color == chess.BLACK else chess.square_mirror(square)
        positional = table[index]

        if piece.color == chess.WHITE:
            score += value + positional
        else:
            score -= value + positional

    # Bishop pair: worth about half a pawn, and cheap to detect.
    if len(board.pieces(chess.BISHOP, chess.WHITE)) >= 2:
        score += 30
    if len(board.pieces(chess.BISHOP, chess.BLACK)) >= 2:
        score -= 30

    return score if board.turn == chess.WHITE else -score


def _move_order_key(board: chess.Board, move: chess.Move) -> int:
    """Search promising moves first so alpha-beta can prune the rest.

    Ordering is most of what makes alpha-beta fast. Searching a good move
    first sets a tight bound that refutes whole subtrees immediately;
    searching a bad move first leaves the window wide and forces the engine
    to examine everything. Captures come first, biggest victim by smallest
    attacker (MVV-LVA), then promotions and checks.
    """
    score = 0
    if board.is_capture(move):
        victim = board.piece_type_at(move.to_square)
        attacker = board.piece_type_at(move.from_square)
        # En passant leaves no piece on the target square.
        victim_value = PIECE_VALUES.get(victim, 100) if victim else 100
        attacker_value = PIECE_VALUES.get(attacker, 100) if attacker else 100
        score += 10_000 + victim_value * 10 - attacker_value
    if move.promotion:
        score += 9_000 + PIECE_VALUES.get(move.promotion, 0)
    if board.gives_check(move):
        score += 500
    return -score          # sorted() is ascending, we want best first


class _Timeout(Exception):
    """Raised to abandon a search that has run out of its time budget."""


class Engine:
    """Alpha-beta search with quiescence and iterative deepening."""

    def __init__(self, time_budget: float = 2.5, max_depth: int = 6):
        self.time_budget = time_budget
        self.max_depth = max_depth
        self.deadline = 0.0
        self.nodes = 0

    # -- search ------------------------------------------------------
    def _quiesce(self, board: chess.Board, alpha: int, beta: int) -> int:
        """Search on past the horizon until the position is quiet.

        Without this the engine has a "horizon effect": at the last ply it
        sees itself capturing a queen and stops, never noticing the recapture
        one move later. Quiescence keeps following captures until nothing is
        hanging, so evaluations are made on settled positions.
        """
        self.nodes += 1
        if self.nodes % 2048 == 0 and time.time() > self.deadline:
            raise _Timeout

        stand_pat = evaluate(board)
        if stand_pat >= beta:
            return beta
        alpha = max(alpha, stand_pat)

        for move in sorted(board.legal_moves,
                           key=lambda m: _move_order_key(board, m)):
            if not board.is_capture(move) and not move.promotion:
                continue
            board.push(move)
            try:
                score = -self._quiesce(board, -beta, -alpha)
            finally:
                # try/finally, not a bare pop: _Timeout unwinds through
                # these frames, and a skipped pop leaves the pushed move on
                # the board. The caller then analyses a position several
                # plies deep and asserts on a move that is no longer legal.
                board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    def _search(self, board: chess.Board, depth: int, alpha: int, beta: int) -> int:
        self.nodes += 1
        if self.nodes % 2048 == 0 and time.time() > self.deadline:
            raise _Timeout

        if board.is_game_over():
            if board.is_checkmate():
                # Prefer mates that arrive sooner: subtracting depth makes a
                # mate in two score higher than the same mate in four.
                return -MATE_SCORE + (self.max_depth - depth)
            return 0

        if depth <= 0:
            return self._quiesce(board, alpha, beta)

        for move in sorted(board.legal_moves,
                           key=lambda m: _move_order_key(board, m)):
            board.push(move)
            try:
                score = -self._search(board, depth - 1, -beta, -alpha)
            finally:
                board.pop()
            if score >= beta:
                return beta        # opponent would avoid this line entirely
            alpha = max(alpha, score)
        return alpha

    # -- public ------------------------------------------------------
    def best_moves(self, board: chess.Board, top_n: int = 5) -> list[dict]:
        """Rank the legal moves, best first.

        Returns candidates rather than one move, deliberately: the model
        chooses among them and explains the choice, so it is still playing
        rather than relaying. Anything within about a third of a pawn of the
        best is a genuine alternative, not a mistake.
        """
        if board.is_game_over():
            return []

        self.deadline = time.time() + self.time_budget
        self.nodes = 0

        moves = sorted(board.legal_moves,
                       key=lambda m: _move_order_key(board, m))
        best: list[tuple[int, chess.Move]] = [(0, m) for m in moves]

        # Iterative deepening: each depth is cheap relative to the next, and
        # it guarantees a usable answer whenever the clock runs out.
        completed_depth = 0
        for depth in range(1, self.max_depth + 1):
            try:
                scored = []
                for move in [m for _, m in best]:
                    board.push(move)
                    try:
                        score = -self._search(board, depth - 1,
                                              -MATE_SCORE, MATE_SCORE)
                    finally:
                        board.pop()
                    scored.append((score, move))
                scored.sort(key=lambda t: t[0], reverse=True)
                best = scored
                completed_depth = depth
            except _Timeout:
                break
            if time.time() > self.deadline:
                break

        out = []
        for score, move in best[:top_n]:
            out.append({
                "uci": move.uci(),
                "san": board.san(move),
                "score": score,
                # Centipawns are engine units; pawns are how humans talk.
                "eval": round(score / 100, 2),
                "mate": abs(score) > MATE_SCORE - 100,
            })
        if out:
            out[0]["depth"] = completed_depth
            out[0]["nodes"] = self.nodes
        return out




# ---------------------------------------------------------------------
# Stockfish
# ---------------------------------------------------------------------
# The Python search above is honest but slow - interpreted move generation
# caps it around 15,000 nodes a second, which is roughly depth 4 in three
# seconds. Stockfish does two million nodes a second in C++, so it is
# stronger at a tenth of a second than this is at ten.
#
# It also has UCI_Elo, which turns "play at about 2000" from an estimate
# into a setting. That is the real reason to prefer it: strength becomes
# something the user chooses rather than something they get.
#
# Optional throughout. The binary is ~103MB and downloaded by
# setup_engine.py, not committed; when it is absent everything falls back to
# the Python search and chess still works.

STOCKFISH_MIN_ELO = 1320      # the engine's own floor
STOCKFISH_MAX_ELO = 3190
DEFAULT_ELO = 2000


def find_stockfish() -> str | None:
    """Locate the engine binary, or None if it was never downloaded."""
    import shutil

    here = pathlib.Path(__file__).parent / "engines"
    if here.exists():
        for exe in here.rglob("stockfish*"):
            if exe.is_file() and exe.suffix.lower() in (".exe", ""):
                return str(exe)

    # A system-wide install is equally good.
    return shutil.which("stockfish")


class EngineSession:
    """One Stockfish process kept open across many moves.

    popen_uci costs 200-300ms - the process starts, loads its network, and
    negotiates UCI - which is more than the search itself at club strength.
    Per-call spawning was fine for a single analysis; for a game it is the
    dominant cost of every move. A game holds one of these for its duration
    and closes it on the way out.

    Falls back silently: if the binary is missing, best_move() uses the
    Python search, so a game never fails for lack of an engine.
    """

    def __init__(self, elo: int | None = DEFAULT_ELO):
        self.elo = elo
        self.engine = None

    def __enter__(self):
        path = find_stockfish()
        if path:
            try:
                self.engine = chess.engine.SimpleEngine.popen_uci(path)
                if self.elo:
                    bounded = max(STOCKFISH_MIN_ELO, min(int(self.elo), STOCKFISH_MAX_ELO))
                    self.engine.configure({"UCI_LimitStrength": True,
                                           "UCI_Elo": bounded})
            except Exception:
                # Quit before dropping the reference, or the process
                # is orphaned: __exit__ only kills self.engine, and a
                # hung Stockfish then lives as long as the worker.
                try:
                    self.engine.quit()
                except Exception:
                    pass
                self.engine = None
        return self

    def __exit__(self, *exc):
        if self.engine is not None:
            try:
                self.engine.quit()
            except Exception:
                pass
        return False

    def evaluate(self, board: chess.Board, time_budget: float = 0.1,
                 multipv: int = 2) -> dict:
        """Judge a position: white's-eye eval and the best two moves.

        This is what move grading is built on, so it runs on a session with
        no elo limit - the reviewer must see more than the player. Scores
        are from White's point of view in centipawns, with mates mapped to
        +-MATE_CP so arithmetic on them still orders correctly.
        """
        if board.is_game_over(claim_draw=True):
            if board.is_checkmate():
                cp = -MATE_CP if board.turn == chess.WHITE else MATE_CP
                return {"cp": cp, "mate": 0, "best": None, "second_cp": None}
            return {"cp": 0, "mate": None, "best": None, "second_cp": None}

        if self.engine is not None:
            try:
                infos = self.engine.analyse(
                    board, chess.engine.Limit(time=time_budget),
                    multipv=max(1, multipv))
                top = infos[0]
                score = top["score"].white()
                out = {
                    "cp": score.score(mate_score=MATE_CP),
                    "mate": score.mate(),
                    "best": top["pv"][0].uci() if top.get("pv") else None,
                    "second_cp": None,
                }
                if len(infos) > 1 and infos[1].get("pv"):
                    out["second_cp"] = infos[1]["score"].white().score(mate_score=MATE_CP)
                return out
            except Exception:
                # Quit before dropping the reference, or the process
                # is orphaned: __exit__ only kills self.engine, and a
                # hung Stockfish then lives as long as the worker.
                try:
                    self.engine.quit()
                except Exception:
                    pass
                self.engine = None

        cands = Engine(time_budget=max(time_budget, 0.6)).best_moves(board, top_n=2)
        sign = 1 if board.turn == chess.WHITE else -1
        if not cands:
            return {"cp": 0, "mate": None, "best": None, "second_cp": None}

        def rescale(score):
            """The Python search scores mates on MATE_SCORE; the reviewer's
            arithmetic is all in MATE_CP. Mixing the two scales made a
            game-losing move come out with a negative loss, which reads as
            'Best move'. Convert, and report the mate as a mate."""
            if abs(score) > MATE_SCORE - 1000:
                return (MATE_CP if score > 0 else -MATE_CP), \
                       int((MATE_SCORE - abs(score)) // 2 + 1) * (1 if score > 0 else -1)
            return score, None

        cp, mate = rescale(cands[0]["score"] * sign)
        second = rescale(cands[1]["score"] * sign)[0] if len(cands) > 1 else None
        return {
            "cp": cp,
            "mate": mate,
            "best": cands[0]["uci"],
            "second_cp": second,
        }

    def best_move(self, board: chess.Board, time_budget: float = 0.25) -> str | None:
        """UCI of the move to play, or None if the position is terminal."""
        if board.is_game_over():
            return None
        if self.engine is not None:
            try:
                r = self.engine.play(board, chess.engine.Limit(time=time_budget))
                if r.move:
                    return r.move.uci()
            except Exception:
                # Quit before dropping the reference, or the process
                # is orphaned: __exit__ only kills self.engine, and a
                # hung Stockfish then lives as long as the worker.
                try:
                    self.engine.quit()
                except Exception:
                    pass
                self.engine = None      # drop to the fallback for the rest
        cands = Engine(time_budget=max(time_budget, 1.0)).best_moves(board, top_n=1)
        return cands[0]["uci"] if cands else None


def _analyse_stockfish(fen: str, top_n: int, time_budget: float,
                       elo: int | None) -> dict | None:
    """Analyse with Stockfish. None if it is unavailable or misbehaves."""

    path = find_stockfish()
    if not path:
        return None

    board = chess.Board(fen)
    try:
        # A fresh process per call. Keeping one alive would save ~200ms, but
        # a subprocess shared across request threads in a web app is a
        # lifecycle problem nobody enjoys debugging, and moves are seconds
        # apart.
        with chess.engine.SimpleEngine.popen_uci(path) as engine:
            if elo:
                bounded = max(STOCKFISH_MIN_ELO, min(int(elo), STOCKFISH_MAX_ELO))
                # Both options are required. UCI_Elo alone does nothing
                # unless UCI_LimitStrength is on - the engine simply plays
                # full strength and ignores the number.
                engine.configure({"UCI_LimitStrength": True,
                                  "UCI_Elo": bounded})

            infos = engine.analyse(
                board,
                chess.engine.Limit(time=time_budget),
                multipv=max(1, min(top_n, 10)),
            )

        candidates = []
        for info in infos:
            pv = info.get("pv") or []
            if not pv:
                continue
            score = info["score"].relative
            mate_in = score.mate()
            candidates.append({
                "uci": pv[0].uci(),
                "san": board.san(pv[0]),
                "score": score.score(mate_score=MATE_SCORE),
                "eval": (round(score.score() / 100, 2)
                         if score.score() is not None else None),
                "mate": mate_in is not None,
                "mate_in": mate_in,
                # The line it expects to follow: useful for the model to
                # explain a plan rather than just name a move.
                "line": _safe_line(board, pv[:4]),
            })

        if candidates:
            candidates[0]["engine"] = "stockfish"
            candidates[0]["elo"] = elo or "full"
        return {"candidates": candidates}

    except Exception:
        # Any engine trouble falls through to the Python search rather than
        # failing the move.
        return None


def _safe_line(board: "chess.Board", moves: list) -> list[str]:
    """SAN for a principal variation, stopping if it stops being legal.

    SAN is computed as the line advances, not against the starting position.
    Naming every move from the root produces plausible-looking nonsense -
    the second move of a line rendered against the first position came out
    as "Kxd8" for a king that cannot reach d8.
    """
    out, temp = [], board.copy()
    for m in moves:
        if m not in temp.legal_moves:
            break
        out.append(temp.san(m))     # SAN first, then advance
        temp.push(m)
    return out


def analyse(fen: str, top_n: int = 5, time_budget: float = 2.5,
            elo: int | None = DEFAULT_ELO) -> dict:
    """Analyse a position. The one function the tool layer needs.

    Prefers Stockfish when the binary is present and falls back to the
    Python search when it is not, so chess works either way.
    """
    board = chess.Board(fen)

    sf = _analyse_stockfish(fen, top_n, time_budget, elo)         if not board.is_game_over() else None

    if sf and sf["candidates"]:
        candidates = sf["candidates"]
    else:
        engine = Engine(time_budget=time_budget)
        candidates = engine.best_moves(board, top_n=top_n)
        if candidates:
            candidates[0]["engine"] = "builtin"

    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "legal_moves": [m.uci() for m in board.legal_moves],
        "legal_san": [board.san(m) for m in board.legal_moves],
        "in_check": board.is_check(),
        "game_over": board.is_game_over(),
        "result": board.result() if board.is_game_over() else None,
        "candidates": candidates,
    }


# ---------------------------------------------------------------------
# Move quality
# ---------------------------------------------------------------------
# The grading every serious chess site shows after a game, computed live.
# A move's cost is the gap between the position's value before it and the
# value after it, seen from the mover's side. The grades are thresholds on
# that gap; the two special ones on top - Brilliant and Great - need the
# engine's first choice and a look at what else was on offer.

QUALITY = {
    #  name           symbol  colour     shown as
    "brilliant":  ("!!", "#1baca6", "Brilliant"),
    "great":      ("!",  "#5c8bb0", "Great move"),
    "best":       ("\u2605", "#96bc4b", "Best move"),
    "excellent":  ("\u2713", "#96bc4b", "Excellent"),
    "good":       ("\u2713", "#96af8b", "Good"),
    "inaccuracy": ("?!", "#f7c631", "Inaccuracy"),
    "mistake":    ("?",  "#e58f2a", "Mistake"),
    "blunder":    ("??", "#ca3431", "Blunder"),
    "forced":     ("\u25a1", "#8b91a1", "Forced"),
}

_ATTACKER_VALUE = dict(PIECE_VALUES)
_ATTACKER_VALUE[chess.KING] = 20_000     # a king "attacks" but rarely captures


def is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """Does this move deliberately leave material en prise?

    A piece lands where the opponent can take it, either undefended or with
    something cheaper, and whatever it captured on the way does not cover
    the cost. Pawns and kings are excluded - a pawn push is not a sacrifice
    and a king walk is a different kind of decision.
    """
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
        return False

    captured = board.piece_at(move.to_square)
    captured_value = PIECE_VALUES[captured.piece_type] if captured else 0
    value = PIECE_VALUES[piece.piece_type]
    if value - captured_value < 200:
        return False            # took as much as it risked

    after = board.copy()
    after.push(move)
    attackers = after.attackers(not piece.color, move.to_square)
    if not attackers:
        return False
    defenders = after.attackers(piece.color, move.to_square)
    cheapest = min(_ATTACKER_VALUE[after.piece_at(sq).piece_type] for sq in attackers)
    if not defenders:
        return True
    return cheapest < value


def grade_move(board_before: chess.Board, move: chess.Move,
               before: dict, after: dict) -> dict:
    """Grade one move given the reviewer's view before and after it."""
    mover_white = board_before.turn == chess.WHITE
    sign = 1 if mover_white else -1

    legal = list(board_before.legal_moves)
    if len(legal) == 1:
        return {"cls": "forced", "loss": 0, "best_uci": move.uci(),
                "best_san": board_before.san(move)}

    best_eval = before["cp"] * sign
    after_eval = after["cp"] * sign
    loss = max(0, best_eval - after_eval)

    best_uci = before.get("best")
    try:
        best_san = board_before.san(chess.Move.from_uci(best_uci)) if best_uci else None
    except Exception:
        best_san = None

    is_best = (best_uci == move.uci()) or loss == 0

    if is_best:
        if is_sacrifice(board_before, move) and after_eval > -150:
            cls = "brilliant"
        elif (before.get("second_cp") is not None
              and best_eval - before["second_cp"] * sign >= 120):
            cls = "great"           # the only good move here
        else:
            cls = "best"
    elif loss <= 25:
        cls = "excellent"
    elif loss <= 60:
        cls = "good"
    elif loss <= 110:
        cls = "inaccuracy"
    elif loss <= 250:
        cls = "mistake"
    else:
        cls = "blunder"

    return {"cls": cls, "loss": int(min(loss, 1500)),
            "wp_loss": round(max(0.0, win_pct(best_eval) - win_pct(after_eval)), 2),
            "best_uci": best_uci, "best_san": best_san}


def win_pct(cp: float) -> float:
    """Centipawns to a win percentage, on lichess's curve.

    A pawn is worth far more in a level position than in a won one, which
    is why accuracy is measured in this space rather than in centipawns.
    """
    import math
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def accuracy(wp_losses: list[float]) -> float:
    """Lichess's accuracy curve over WIN-PERCENTAGE loss, not centipawns.

    The curve's input is the drop in win percentage (0-100). Feeding it
    centipawn loss made it collapse: an average loss of 80 centipawns - an
    ordinary casual game - came out as 0.0% accuracy for both players, and
    those numbers were shown on the end card and narrated by the model.
    """
    import math
    if not wp_losses:
        return 100.0
    mean = sum(wp_losses) / len(wp_losses)
    acc = 103.1668 * math.exp(-0.04354 * mean) - 3.1669
    return round(max(0.0, min(100.0, acc)), 1)


def review(quality: list[dict], moves_san: list[str], user_color: str) -> dict:
    """Per-side summary of a whole game, for the end card and the model."""
    sides = {"white": [], "black": []}
    for i, q in enumerate(quality):
        sides["white" if i % 2 == 0 else "black"].append((i, q))

    out = {}
    for color, entries in sides.items():
        # Win-percentage loss for accuracy; centipawn loss still ranks
        # the worst moves, where "how much material" is the useful unit.
        losses = [q.get("wp_loss", 0.0) for _, q in entries if q["cls"] != "forced"]
        counts = {name: 0 for name in QUALITY}
        for _, q in entries:
            counts[q["cls"]] += 1
        worst = sorted(
            ((i, q) for i, q in entries if q["cls"] in ("mistake", "blunder", "inaccuracy")),
            key=lambda t: -t[1]["loss"])[:3]
        highlights = [(i, q) for i, q in entries if q["cls"] in ("brilliant", "great")]
        out[color] = {
            "accuracy": accuracy(losses),
            "counts": counts,
            "worst": [{"move_no": i // 2 + 1, "san": moves_san[i], "cls": q["cls"],
                       "loss": q["loss"], "better": q["best_san"]} for i, q in worst],
            "highlights": [{"move_no": i // 2 + 1, "san": moves_san[i], "cls": q["cls"]}
                           for i, q in highlights],
        }
    out["user"] = user_color
    return out


def eval_text(analysis: dict | None) -> str:
    """'+1.3', '-0.4', 'M3' or '-M2', from White's point of view."""
    if not analysis:
        return "0.0"
    mate = analysis.get("mate")
    if mate is not None:
        if mate == 0:
            return "#"
        return f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}"
    cp = analysis.get("cp", 0)
    return f"{cp / 100:+.1f}"
