from __future__ import annotations

import chess

from gtochess.analysis.fingerprint import fingerprint_from_pv
from gtochess.domain.book import PositionLosses
from gtochess.domain.explanations import Evidence, EvidenceKind
from gtochess.domain.models import PlanStep
from gtochess.engine.protocol import EngineProvider
from gtochess.llm.facts import mover_cp, pawns

PLAYABLE_CP = 50
ZONE_WORDS = {
    "own_qs": "their own queenside",
    "own_ctr": "their own centre",
    "own_ks": "their own kingside",
    "mid_qs": "the queenside",
    "mid_ctr": "the centre",
    "mid_ks": "the kingside",
    "opp_qs": "the far queenside",
    "opp_ctr": "the far centre",
    "opp_ks": "the far kingside",
}
PIECE_WORDS = {
    "P": "a pawn",
    "N": "a knight",
    "B": "a bishop",
    "R": "a rook",
    "Q": "the queen",
    "K": "the king",
}


class Mistake:
    def __init__(self, board: chess.Board, played_uci: str, cost: PositionLosses) -> None:
        self.board = board
        self.played = chess.Move.from_uci(played_uci)
        self.cost = cost
        self.loss_cp = cost.for_move(played_uci) or 0
        self.played_san = cost.sans.get(played_uci) or board.san(self.played)
        self.best = chess.Move.from_uci(cost.best_uci)
        self.best_san = cost.best_san


def _step_phrase(step: PlanStep) -> str:
    piece = PIECE_WORDS.get(step.piece, "a piece")
    where = ZONE_WORDS.get(step.to_zone.value, "the board")
    if step.is_capture:
        return f"{piece} taking on {where}"
    return f"{piece} going to {where}"


def _divergence(punish: list[PlanStep], intent: list[PlanStep]) -> str | None:
    for left, right in zip(punish, intent, strict=False):
        if left.token() == right.token():
            continue
        return (
            f"The line after the move played has {_step_phrase(left)}, where the engine's "
            f"move leads to {_step_phrase(right)} instead."
        )
    if len(intent) > len(punish):
        return f"The engine's move keeps going with {_step_phrase(intent[len(punish)])}."
    return None


def build_mistake_evidence(
    engine: EngineProvider,
    mistake: Mistake,
    *,
    depth: int | None = None,
    pv_moves: int = 6,
) -> list[Evidence]:
    board = mistake.board
    best_cp = mover_cp(mistake.cost.best_cp, board)
    depth = depth or mistake.cost.depth

    evidence = [
        Evidence(
            id="cost",
            kind=EvidenceKind.EVAL,
            statement=(
                f"{mistake.played_san} gives up {pawns(mistake.loss_cp)} against "
                f"{mistake.best_san}, taking the position from {pawns(best_cp)} to "
                f"{pawns(best_cp - mistake.loss_cp)} at depth {mistake.cost.depth}."
                + (
                    " That is inside the band where the choice is playable."
                    if mistake.loss_cp < PLAYABLE_CP
                    else ""
                )
            ),
        )
    ]

    after_played = board.copy(stack=False)
    after_played.push(mistake.played)
    punishment = engine.analyse(after_played, depth=depth, multipv=1).best

    after_best = board.copy(stack=False)
    after_best.push(mistake.best)
    intent = engine.analyse(after_best, depth=depth, multipv=1).best

    evidence.append(
        Evidence(
            id="punish",
            kind=EvidenceKind.ENGINE_LINE,
            statement=(
                f"After {mistake.played_san} the engine answers "
                f"{' '.join(punishment.pv_san[:pv_moves])}, holding "
                f"{pawns(-mover_cp(punishment.score_cp, after_played))} for the player."
            ),
        )
    )
    evidence.append(
        Evidence(
            id="intent",
            kind=EvidenceKind.ENGINE_LINE,
            statement=(
                f"After {mistake.best_san} the engine continues "
                f"{' '.join(intent.pv_san[:pv_moves])}, holding "
                f"{pawns(-mover_cp(intent.score_cp, after_best))} instead."
            ),
        )
    )

    punish_plan = fingerprint_from_pv(after_played, punishment.pv_uci, max_steps=pv_moves)
    intent_plan = fingerprint_from_pv(after_best, intent.pv_uci, max_steps=pv_moves)
    parted = _divergence(punish_plan.steps, intent_plan.steps)
    if parted:
        evidence.append(Evidence(id="plans", kind=EvidenceKind.PLAN, statement=parted))

    return evidence
