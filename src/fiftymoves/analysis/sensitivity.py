"""Ablation sensitivity.

The point of this module is that nothing here required us to anticipate a
category of position. We do not ask "is this a mate-in-one?" -- we perturb the
board and measure what the evaluation cared about. A loud but irrelevant passed
pawn ranks near zero because deleting it changes nothing; the mating piece ranks
first because deleting it changes everything.

Three perturbations:

* piece removal  -- how much material/structural weight does this piece carry?
* move-space     -- how load-bearing is it to the *plan*? (forbid its moves)
* tempo          -- how urgent is the position? (give the opponent a free move)

Numbers here are a ranking signal, not a truth claim. Evaluation is not linear
and ablation deltas do not sum.
"""

from __future__ import annotations

import chess

from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import (
    AblationKind,
    EngineReport,
    SensitivityItem,
    SensitivityReport,
)
from fiftymoves.engine.protocol import EngineProvider


def _score_or_none(engine: EngineProvider, board: chess.Board, depth: int) -> int | None:
    """Evaluate a perturbed board, or None if the perturbation broke it."""
    if not board.is_valid():
        return None
    if board.is_game_over(claim_draw=False):
        # Terminal after ablation. Legal, but the delta is not comparable to a
        # searched score, so we exclude rather than fabricate a number.
        return None
    try:
        return engine.analyse(board, depth=depth, multipv=1).score_cp
    except Exception:  # noqa: BLE001 - a broken perturbation must not kill the batch
        return None


def _ablate_pieces(
    engine: EngineProvider, board: chess.Board, baseline_cp: int, depth: int
) -> list[SensitivityItem]:
    items: list[SensitivityItem] = []
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue  # removing a king is not a position, it is a crash
        probe = board.copy(stack=False)
        probe.remove_piece_at(square)
        perturbed = _score_or_none(engine, probe, depth)
        if perturbed is None:
            continue
        items.append(
            SensitivityItem(
                kind=AblationKind.PIECE_REMOVAL,
                square=chess.square_name(square),
                piece_symbol=piece.symbol(),
                delta_cp=perturbed - baseline_cp,
                baseline_cp=baseline_cp,
                perturbed_cp=perturbed,
            )
        )
    return items


def _ablate_move_space(
    engine: EngineProvider, board: chess.Board, baseline_cp: int, depth: int
) -> list[SensitivityItem]:
    """Forbid each of the mover's pieces from moving, and re-search.

    This separates "this piece is worth material" from "this piece is why the
    plan works" -- a knight that wins nothing but paralyses every reply shows up
    here and nowhere else.
    """
    items: list[SensitivityItem] = []
    mover = board.turn
    for square, piece in board.piece_map().items():
        if piece.color != mover or piece.piece_type == chess.KING:
            continue
        allowed = [m for m in board.legal_moves if m.from_square != square]
        if not allowed:
            continue
        probe = board.copy(stack=False)
        try:
            report = engine.analyse(probe, depth=depth, multipv=len(allowed))
        except Exception:  # noqa: BLE001
            continue
        restricted = [
            line for line in report.lines if not line.move_uci.startswith(chess.square_name(square))
        ]
        if not restricted:
            continue
        perturbed = restricted[0].score_cp
        items.append(
            SensitivityItem(
                kind=AblationKind.MOVE_SPACE,
                square=chess.square_name(square),
                piece_symbol=piece.symbol(),
                delta_cp=perturbed - baseline_cp,
                baseline_cp=baseline_cp,
                perturbed_cp=perturbed,
                note="best evaluation available when this piece may not move",
            )
        )
    return items


def _ablate_tempo(
    engine: EngineProvider, board: chess.Board, baseline_cp: int, depth: int
) -> SensitivityItem | None:
    """Hand the opponent a free move. Large delta means the position is urgent."""
    if board.is_check():
        return None  # a null move is illegal out of check
    probe = board.copy(stack=False)
    probe.push(chess.Move.null())
    # Drop the null move from the history before searching. python-chess refuses
    # to transmit a history containing null moves and warns on every call; the
    # resulting position is what we want, the provenance is not.
    probe.clear_stack()
    perturbed = _score_or_none(engine, probe, depth)
    if perturbed is None:
        return None
    return SensitivityItem(
        kind=AblationKind.TEMPO,
        delta_cp=perturbed - baseline_cp,
        baseline_cp=baseline_cp,
        perturbed_cp=perturbed,
        note="evaluation if the opponent were handed a free move",
    )


def compute_sensitivity(
    engine: EngineProvider,
    board: chess.Board,
    *,
    baseline: EngineReport,
    depth: int,
    floor_cp: int = 40,
) -> SensitivityReport:
    """Rank the elements of a position by how much the evaluation depends on them.

    ``floor_cp`` drops noise: below it, the delta is indistinguishable from
    search jitter and must not reach the dossier.
    """
    baseline_cp = baseline.score_cp

    items = _ablate_pieces(engine, board, baseline_cp, depth)
    items += _ablate_move_space(engine, board, baseline_cp, depth)
    tempo = _ablate_tempo(engine, board, baseline_cp, depth)
    if tempo is not None:
        items.append(tempo)

    significant = [i for i in items if i.magnitude >= floor_cp]
    # Stable ordering: magnitude, then square, then kind -- so identical inputs
    # always yield an identical report and the cache key stays honest.
    significant.sort(key=lambda i: (-i.magnitude, i.square or "", i.kind.value))

    return SensitivityReport(key=position_key(board), items=significant, ablation_depth=depth)
