"""A tiny deterministic engine.

Not a Stockfish substitute -- it is a fixed-depth material search. Its job is to
let the golden-set tests run in CI without a Stockfish binary, and to give the
pipeline a reproducible engine when we need to assert on exact numbers.

It is strong enough for the properties the golden set actually asserts: it finds
forced mates and it ranks material correctly. It is deliberately *weak*
positionally, which is a feature -- if a test passes only because the engine is
strong, the test was measuring the wrong thing.
"""

from __future__ import annotations

import chess

from gtochess.domain.identity import position_key
from gtochess.domain.material import material_cp
from gtochess.domain.models import MATE_SCORE_CP, EngineLine, EngineReport

_NEAR_MATE = MATE_SCORE_CP - 1000


def _search(board: chess.Board, depth: int, ply: int, alpha: int, beta: int) -> int:
    """Negamax. Returns the score from the side-to-move's point of view."""
    if board.is_checkmate():
        return -(MATE_SCORE_CP - ply)
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    if depth <= 0:
        sign = 1 if board.turn == chess.WHITE else -1
        return material_cp(board) * sign

    best = -MATE_SCORE_CP * 2
    for move in board.legal_moves:
        board.push(move)
        score = -_search(board, depth - 1, ply + 1, -beta, -alpha)
        board.pop()
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _mate_distance(mover_pov_score: int) -> int | None:
    """Convert a folded mate score back to a move count, or None."""
    if abs(mover_pov_score) < _NEAR_MATE:
        return None
    plies_to_mate = MATE_SCORE_CP - abs(mover_pov_score)
    moves = (plies_to_mate + 1) // 2
    return moves if mover_pov_score > 0 else -moves


class ReferenceEngine:
    """Fixed-depth material search implementing ``EngineProvider``.

    Chess960 needs no special handling here: python-chess already generates
    correct 960 castling moves, and material evaluation is variant-agnostic.
    """

    def __init__(self, *, max_depth: int = 3) -> None:
        self._max_depth = max_depth

    @property
    def engine_id(self) -> str:
        return f"reference-material-d{self._max_depth}"

    def analyse(self, board: chess.Board, *, depth: int, multipv: int = 3) -> EngineReport:
        effective_depth = min(depth, self._max_depth)
        mover_is_white = board.turn == chess.WHITE

        scored: list[tuple[int, chess.Move]] = []
        for move in board.legal_moves:
            board.push(move)
            score = -_search(board, effective_depth - 1, 1, -MATE_SCORE_CP * 2, MATE_SCORE_CP * 2)
            board.pop()
            scored.append((score, move))

        # Deterministic ordering: score first, then UCI as a stable tiebreak so
        # repeated runs produce byte-identical reports (cache keys depend on it).
        scored.sort(key=lambda pair: (-pair[0], pair[1].uci()))

        lines: list[EngineLine] = []
        for rank, (mover_score, move) in enumerate(scored[: max(1, multipv)], start=1):
            mate_in = _mate_distance(mover_score)
            white_score = mover_score if mover_is_white else -mover_score
            white_mate = None if mate_in is None else (mate_in if mover_is_white else -mate_in)
            lines.append(
                EngineLine(
                    rank=rank,
                    move_san=board.san(move),
                    move_uci=move.uci(),
                    pv_san=[board.san(move)],
                    pv_uci=[move.uci()],
                    score_cp=white_score,
                    mate_in=white_mate,
                    depth=effective_depth,
                )
            )

        return EngineReport(
            key=position_key(board),
            lines=lines,
            depth=effective_depth,
            engine_id=self.engine_id,
        )

    def close(self) -> None:
        return None
