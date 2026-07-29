from __future__ import annotations

from collections.abc import Sequence

import chess

from fiftymoves.domain.explanations import Evidence, EvidenceKind
from fiftymoves.domain.graph import GraphEdge, GraphNode
from fiftymoves.domain.models import (
    AblationKind,
    EngineReport,
    EvalLandscape,
    SensitivityItem,
    SensitivityReport,
)
from fiftymoves.domain.openings import OpeningFamily

PAWN = 100.0


def pawns(score_cp: int) -> str:
    """Evaluations read in pawns, the way players quote them."""
    return f"{score_cp / PAWN:+.2f}"


def mover_cp(score_cp: int, board: chess.Board) -> int:
    return score_cp if board.turn == chess.WHITE else -score_cp


def _advantage(cp: int) -> str:
    size = abs(cp) / PAWN
    if size < 0.3:
        return "level"
    side = "the side to move" if cp > 0 else "the opponent"
    if size < 0.8:
        return f"a slight edge for {side}"
    if size < 1.8:
        return f"a clear edge for {side}"
    return f"a winning advantage for {side}"


def _eval_statement(report: EngineReport, board: chess.Board) -> str:
    best = report.best
    if best.mate_in is not None:
        who = "the side to move" if best.mate_in > 0 else "the opponent"
        return f"The engine sees mate in {abs(best.mate_in)} for {who} at depth {report.depth}."
    cp = mover_cp(report.score_cp, board)
    return (
        f"The engine evaluates the position at {pawns(cp)} for the side to move "
        f"at depth {report.depth}, which is {_advantage(cp)}."
    )


def _line_statement(report: EngineReport, board: chess.Board, rank: int) -> str | None:
    line = next((line for line in report.lines if line.rank == rank), None)
    if line is None:
        return None
    pv = " ".join(line.pv_san[:6])
    cp = mover_cp(line.score_cp, board)
    label = "The engine's first choice" if rank == 1 else f"The engine's option {rank}"
    return f"{label} is {line.move_san} ({pawns(cp)}), continuing {pv}."


def _sensitivity_statement(item: SensitivityItem) -> str:
    direction = "collapses" if item.delta_cp < 0 else "improves"
    swing = abs(item.delta_cp)
    if item.kind is AblationKind.PIECE_REMOVAL and item.square:
        piece = item.piece_symbol or "piece"
        worth = "more" if item.residual_cp * item.expected_cp > 0 else "less"
        return (
            f"Removing the {piece} on {item.square} {direction} the evaluation by "
            f"{pawns(swing)}, which is {pawns(abs(item.residual_cp))} {worth} than "
            f"its material value alone, so it is doing that much extra work here."
        )
    if item.kind is AblationKind.MOVE_SPACE:
        return (
            f"Restricting the side to move to its remaining options {direction} the "
            f"evaluation by {pawns(swing)}, so the specific move matters more than the plan."
        )
    return (
        f"Handing the opponent a free move {direction} the evaluation by {pawns(swing)}, "
        f"which measures how much the initiative is worth."
    )


def _landscape_statement(landscape: EvalLandscape) -> str:
    if landscape.is_single_answer:
        return (
            f"Only one move holds the position: the second best is "
            f"{pawns(landscape.delta_to_second_cp or 0)} worse across "
            f"{landscape.legal_move_count} legal moves."
        )
    return (
        f"{landscape.playable_move_count} of {landscape.legal_move_count} legal moves stay "
        f"within the playable band, so the position tolerates more than one idea."
    )


def _repertoire_statement(node: GraphNode) -> str:
    line = " ".join(node.san_path) or "the starting position"
    return (
        f"You reached this position {node.games} times via {line}, scoring "
        f"{node.score:.0%} from it."
    )


def _continuation_statement(node: GraphNode, edges: Sequence[GraphEdge]) -> str | None:
    if not edges:
        return None
    parts = [f"{e.san} in {e.games}" for e in edges[:4]]
    hidden = node.pruned_children
    tail = (
        f", with {hidden} rarer {'reply' if hidden == 1 else 'replies'} pruned." if hidden else "."
    )
    return f"From here you played {', '.join(parts)} of {node.games} games{tail}"


def _opening_statement(family: OpeningFamily) -> str:
    return (
        f"This sits in the {family.name} ({family.eco_range or 'no ECO'}), which you have "
        f"played {family.games} times scoring {family.score:.0%}, with a forcing move rate of "
        f"{family.forcing_rate:.0%} in the opening."
    )


def build_evidence(
    board: chess.Board,
    report: EngineReport,
    sensitivity: SensitivityReport,
    landscape: EvalLandscape,
    *,
    node: GraphNode | None = None,
    family: OpeningFamily | None = None,
    continuations: Sequence[GraphEdge] = (),
    line_limit: int = 3,
    sensitivity_limit: int = 3,
) -> list[Evidence]:
    evidence = [
        Evidence(id="eval", kind=EvidenceKind.EVAL, statement=_eval_statement(report, board))
    ]

    for rank in range(1, line_limit + 1):
        statement = _line_statement(report, board, rank)
        if statement:
            evidence.append(
                Evidence(id=f"line{rank}", kind=EvidenceKind.ENGINE_LINE, statement=statement)
            )

    for index, item in enumerate(sensitivity.top(sensitivity_limit), start=1):
        evidence.append(
            Evidence(
                id=f"sens{index}",
                kind=EvidenceKind.SENSITIVITY,
                statement=_sensitivity_statement(item),
            )
        )

    evidence.append(
        Evidence(id="shape", kind=EvidenceKind.LANDSCAPE, statement=_landscape_statement(landscape))
    )

    if node is not None:
        evidence.append(
            Evidence(id="rep", kind=EvidenceKind.REPERTOIRE, statement=_repertoire_statement(node))
        )
        continuation = _continuation_statement(node, continuations)
        if continuation:
            evidence.append(
                Evidence(id="cont", kind=EvidenceKind.REPERTOIRE, statement=continuation)
            )

    if family is not None:
        evidence.append(
            Evidence(id="opening", kind=EvidenceKind.OPENING, statement=_opening_statement(family))
        )

    return evidence
