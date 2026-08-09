from __future__ import annotations

import chess

from gtochess.analysis.fingerprint import fingerprint_from_line
from gtochess.domain.knowledge import PositionKnowledge
from gtochess.domain.models import (
    EngineReport,
    EvalLandscape,
    SensitivityReport,
    plan_token_string,
)


def learn_position(
    board: chess.Board,
    digest: str,
    report: EngineReport,
    sensitivity: SensitivityReport,
    landscape: EvalLandscape,
    *,
    plan_steps: int = 6,
    load_bearing: int = 3,
) -> PositionKnowledge:
    plan = fingerprint_from_line(board, report.best, max_steps=plan_steps)
    squares = tuple(
        item.square for item in sensitivity.top(load_bearing) if item.square is not None
    )
    return PositionKnowledge(
        digest=digest,
        epd=board.epd(),
        variant=report.key.variant,
        depth=report.depth,
        best_san=report.best.move_san,
        best_cp=report.score_cp,
        delta_to_second_cp=report.delta_to_second(),
        is_single_answer=landscape.is_single_answer,
        playable_moves=landscape.playable_move_count,
        legal_moves=landscape.legal_move_count,
        plan_digest=plan.digest,
        plan_tokens=plan_token_string(plan.steps),
        load_bearing=squares,
    )
