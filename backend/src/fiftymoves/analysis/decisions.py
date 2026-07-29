from __future__ import annotations

import chess

from fiftymoves.analysis.fingerprint import zone_of
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.material import capture_value_cp
from fiftymoves.domain.models import EngineLine, EngineReport
from fiftymoves.domain.profile import MoveDecision

_OPPONENT_ZONE_PREFIX = "opp_"


def targets_opponent_zone(move: chess.Move, mover: chess.Color) -> bool:
    return zone_of(move.to_square, mover).value.startswith(_OPPONENT_ZONE_PREFIX)


def reply_count_after(board: chess.Board, move: chess.Move) -> int:
    board.push(move)
    try:
        return board.legal_moves.count()
    finally:
        board.pop()


def _mover_score(line: EngineLine, mover_is_white: bool) -> int:
    return line.score_cp if mover_is_white else -line.score_cp


def evaluate_choice(
    board: chess.Board,
    played: chess.Move,
    report: EngineReport,
    *,
    game_id: str,
    ply: int,
    played_score_cp: int | None = None,
    playable_band_cp: int = 30,
) -> MoveDecision:
    if played not in board.legal_moves:
        raise ValueError(f"{played.uci()} is not legal in {board.fen()}")
    if not report.lines:
        raise ValueError("engine report has no lines")

    mover = board.turn
    mover_is_white = mover == chess.WHITE
    scores = {line.move_uci: _mover_score(line, mover_is_white) for line in report.lines}
    best_score = max(scores.values())

    played_uci = played.uci()
    played_score = scores.get(played_uci, played_score_cp)
    eval_loss = None if played_score is None else max(0, best_score - played_score)

    playable = [
        line for line in report.lines if best_score - scores[line.move_uci] <= playable_band_cp
    ]
    alternatives = [line for line in playable if line.move_uci != played_uci]

    reply_counts: list[int] = []
    material_gains: list[int] = []
    for line in alternatives:
        move = chess.Move.from_uci(line.move_uci)
        if move not in board.legal_moves:
            continue
        reply_counts.append(reply_count_after(board, move))
        material_gains.append(capture_value_cp(board, move))

    best_move = chess.Move.from_uci(report.best.move_uci)

    return MoveDecision(
        best_uci=report.best.move_uci,
        best_san=report.best.move_san,
        key=position_key(board),
        game_id=game_id,
        ply=ply,
        played_uci=played_uci,
        played_san=board.san(played),
        mover_is_white=mover_is_white,
        position_eval_cp=best_score,
        eval_loss_cp=eval_loss,
        is_decision_point=len(playable) > 1,
        played_reply_count=reply_count_after(board, played),
        played_material_gain_cp=capture_value_cp(board, played),
        played_is_capture=board.is_capture(played),
        played_is_check=board.gives_check(played),
        played_targets_opponent_zone=targets_opponent_zone(played, mover),
        best_targets_opponent_zone=targets_opponent_zone(best_move, mover),
        alternative_reply_counts=tuple(reply_counts),
        alternative_material_gains=tuple(material_gains),
    )
