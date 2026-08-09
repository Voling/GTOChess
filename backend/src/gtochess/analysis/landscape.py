"""Evaluation-landscape descriptors.

Continuous shape measurements, not category labels. They route cost (does this
node need Opus, Haiku, or no model at all?) and they colour prose -- but they
never gate what may be discussed. Content comes from the sensitivity ranking, which
is open-ended; keeping these two jobs separate is what stops the taxonomy from
quietly becoming a ceiling on the output again.
"""

from __future__ import annotations

import math
import statistics

import chess

from gtochess.domain.models import EngineReport, EvalLandscape, PlayableMove


def _softmax_entropy(scores: list[int], *, temperature_cp: float = 50.0) -> float:
    """Entropy over top-move scores. 0 means one move stands alone."""
    if len(scores) < 2:
        return 0.0
    best = max(scores)
    weights = [math.exp((s - best) / temperature_cp) for s in scores]
    total = sum(weights)
    probabilities = [w / total for w in weights]
    return -sum(p * math.log(p) for p in probabilities if p > 0)


def compute_landscape(
    board: chess.Board,
    report: EngineReport,
    *,
    playable_band_cp: int = 30,
    only_move_threshold_cp: int = 300,
    depth_reports: list[EngineReport] | None = None,
) -> EvalLandscape:
    """Describe the shape of the evaluation surface around this position.

    ``depth_reports`` is the same position searched at increasing depths. When
    supplied it yields volatility and mind-changing counts, which are the two
    signals that separate "quietly better" from "sharp and about to explode".
    """
    mover_is_white = board.turn == chess.WHITE
    # Rank from the mover's side so "best" means best for whoever is to play.
    mover_scores = [line.score_cp if mover_is_white else -line.score_cp for line in report.lines]
    best_mover_cp = max(mover_scores) if mover_scores else 0

    playable = [
        PlayableMove(uci=line.move_uci, san=line.move_san, score_cp=line.score_cp)
        for line, score in zip(report.lines, mover_scores, strict=True)
        if best_mover_cp - score <= playable_band_cp
    ]

    volatility = 0.0
    mind_changes = 0
    if depth_reports and len(depth_reports) > 1:
        series = [r.score_cp for r in depth_reports]
        volatility = statistics.pstdev(series)
        best_moves = [r.best.move_uci for r in depth_reports]
        mind_changes = sum(1 for a, b in zip(best_moves, best_moves[1:], strict=True) if a != b)

    return EvalLandscape(
        best_cp=report.score_cp,
        legal_move_count=board.legal_moves.count(),
        playable_move_count=len(playable),
        playable=tuple(playable),
        delta_to_second_cp=report.delta_to_second(),
        top_move_entropy=_softmax_entropy(mover_scores),
        eval_volatility_cp=volatility,
        best_move_changes=mind_changes,
        forced_mate_in=report.best.mate_in,
        only_move_threshold_cp=only_move_threshold_cp,
    )
