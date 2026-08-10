from __future__ import annotations

from collections.abc import Sequence

import chess

from gtochess.analysis.structure import compute_structure
from gtochess.domain.explanations import Evidence, EvidenceKind
from gtochess.domain.graph import GraphEdge, GraphNode
from gtochess.domain.knowledge import PositionKnowledge
from gtochess.domain.models import (
    AblationKind,
    EngineReport,
    EvalLandscape,
    PositionAttribution,
    SensitivityItem,
    SensitivityReport,
)
from gtochess.domain.openings import OpeningFamily
from gtochess.domain.structure import PawnShape, Structure

PAWN = 100.0


def pawns(score_cp: int) -> str:
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
        gap = pawns(abs(item.residual_cp))
        if item.residual_cp * item.expected_cp > 0:
            tail = f"{gap} more than its material value alone, so it is doing extra work here"
        else:
            tail = (
                f"{gap} less than its material value alone, so it is doing less "
                "than a piece of its kind usually does here"
            )
        return (
            f"Removing the {piece} on {item.square} {direction} the evaluation by "
            f"{pawns(swing)}, which is {tail}."
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


def _attribution_statement(attribution: PositionAttribution) -> str | None:
    outliers = attribution.outliers(3)
    if not outliers:
        return None
    parts = [f"the {p.piece_symbol} on {p.square} at {abs(p.value_pawns):.1f}" for p in outliers]
    split = ""
    if attribution.material_pawns is not None and attribution.positional_pawns is not None:
        split = (
            f" Material accounts for {attribution.material_pawns:+.2f} and placement for "
            f"{attribution.positional_pawns:+.2f}."
        )
    return (
        f"The engine's own piece values are furthest from the usual for {', '.join(parts)}, "
        f"measured in pawns.{split}"
    )


def _shape_statement(landscape: EvalLandscape) -> str | None:
    if not landscape.is_single_answer:
        return None
    return (
        f"Only one move holds the position: the second best is "
        f"{pawns(landscape.delta_to_second_cp or 0)} worse across "
        f"{landscape.legal_move_count} legal moves."
    )


def _and_list(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _record_phrase(edge: GraphEdge) -> str:
    if not edge.decided:
        return f"{edge.san} in {edge.games} games"
    return (
        f"{edge.san} in {edge.games} games scoring {edge.score:.0%}, "
        f"{edge.wins} wins {edge.draws} draws {edge.losses} losses"
    )


def _coverage_statement(landscape: EvalLandscape, continuations: Sequence[GraphEdge]) -> str | None:
    allowed = landscape.playable
    if not allowed:
        return None
    if not continuations:
        return (
            f"The engine allows {len(allowed)} moves here: {_and_list([m.san for m in allowed])}."
        )

    played = {e.uci: e for e in continuations}
    covered = [m for m in allowed if m.uci in played]
    never = [m.san for m in allowed if m.uci not in played]
    allowed_ucis = {m.uci for m in allowed}
    outside = [e for e in continuations if e.uci not in allowed_ucis]

    parts = [f"You play {len(covered)} of the {len(allowed)} moves the engine allows here."]
    parts.extend(f"{_record_phrase(played[m.uci])}." for m in covered)
    if never:
        parts.append(f"You have never played {_and_list(never)}.")
    if outside:
        parts.append(
            f"Outside that set you play {_and_list([_record_phrase(e) for e in outside[:3]])}."
        )
    return " ".join(parts)


def _pawns_are(squares: tuple[str, ...]) -> str:
    return f"{_and_list(list(squares))} {'are' if len(squares) > 1 else 'is'}"


def _shape_phrases(shape: PawnShape, side: str) -> list[str]:
    parts = []
    if shape.doubled:
        parts.append(f"{side} has doubled pawns on {_and_list(list(shape.doubled))}")
    if shape.isolated:
        parts.append(f"{side}'s {_pawns_are(shape.isolated)} isolated")
    if shape.backward:
        parts.append(f"{side}'s {_pawns_are(shape.backward)} backward")
    if shape.passed:
        plural = "passed pawns" if len(shape.passed) > 1 else "a passed pawn"
        parts.append(f"{side} has {plural} on {_and_list(list(shape.passed))}")
    if shape.outposts:
        parts.append(f"{side} holds {_and_list(list(shape.outposts)[:3])} with a pawn behind it")
    return parts


def _structure_statement(structure: Structure) -> str | None:
    parts = _shape_phrases(structure.white, "White") + _shape_phrases(structure.black, "Black")
    if structure.open_files:
        parts.append(f"the {_and_list(list(structure.open_files))} file is open")
    if not parts:
        return None
    return f"{'. '.join(part[0].upper() + part[1:] for part in parts)}."


def _weakness_statement(structure: Structure) -> str | None:
    parts = []
    if structure.white.weak_squares:
        held = _and_list(list(structure.white.weak_squares)[:4])
        parts.append(f"White's pawns can no longer cover {held}")
    if structure.black.weak_squares:
        held = _and_list(list(structure.black.weak_squares)[:4])
        parts.append(f"Black's pawns can no longer cover {held}")
    if not parts:
        return None
    return f"{'. '.join(parts)}."


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
    attribution: PositionAttribution | None = None,
    principles: Sequence[Evidence] = (),
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

    shape = _shape_statement(landscape)
    if shape:
        evidence.append(Evidence(id="shape", kind=EvidenceKind.LANDSCAPE, statement=shape))

    # Computed from the board, never asked of the model, so a motif can only be
    # discussed when it is actually there.
    structure = compute_structure(board)
    pawns = _structure_statement(structure)
    if pawns:
        evidence.append(Evidence(id="pawns", kind=EvidenceKind.LANDSCAPE, statement=pawns))
    holes = _weakness_statement(structure)
    if holes:
        evidence.append(Evidence(id="holes", kind=EvidenceKind.LANDSCAPE, statement=holes))

    coverage = _coverage_statement(landscape, continuations)
    if coverage:
        evidence.append(Evidence(id="covers", kind=EvidenceKind.LANDSCAPE, statement=coverage))

    if attribution is not None:
        statement = _attribution_statement(attribution)
        if statement:
            evidence.append(
                Evidence(id="pieces", kind=EvidenceKind.SENSITIVITY, statement=statement)
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

    evidence.extend(principles)
    return evidence


PRINCIPLE_LIMIT = 4


def plan_principles(
    held: PositionKnowledge,
    steps: int,
    neighbours: Sequence[PositionKnowledge],
    *,
    limit: int = PRINCIPLE_LIMIT,
) -> list[Evidence]:
    """What transfers from other positions that reach for the same idea.

    Theory, but measured rather than quoted: every statement here comes from an
    engine search on a real position, so a claim citing one is still falsifiable.
    The ids are their own prefix so a reader can tell a general idea from a
    reading of the board in front of them.
    """
    if steps <= 0 or not neighbours:
        return []

    prefix = held.plan_prefix(steps)
    if not prefix:
        return []

    # Every count below is of `kept`, never of `neighbours`. Stating a figure
    # drawn from twelve positions beside moves drawn from four produces evidence
    # that is literally false, and a model citing it still passes grounding.
    kept = sorted(neighbours, key=lambda r: (-r.depth, r.digest))[:limit]
    moves = _and_list(sorted({r.best_san for r in kept}))
    others = len(kept)
    positions = "position" if others == 1 else "positions"
    more = len(neighbours) - others

    evidence = [
        Evidence(
            id="prin1",
            kind=EvidenceKind.PRINCIPLE,
            statement=(
                f"{others} other studied {positions} reach for the same idea, {prefix}"
                + (f", of {len(neighbours)} that do" if more else "")
                + f". There the engine's move was {moves}."
            ),
        )
    ]

    # Squares that carry the evaluation in every one of them are the idea's real
    # subject, and the thing worth naming in an explanation.
    shared = set(held.load_bearing)
    for record in kept:
        shared &= set(record.load_bearing)
    if shared:
        evidence.append(
            Evidence(
                id="prin2",
                kind=EvidenceKind.PRINCIPLE,
                statement=(
                    f"Across those positions the evaluation turns on "
                    f"{_and_list(sorted(shared))} every time."
                ),
            )
        )

    single = [r for r in kept if r.is_single_answer]
    if single:
        evidence.append(
            Evidence(
                id="prin3",
                kind=EvidenceKind.PRINCIPLE,
                statement=(
                    f"In {len(single)} of them the idea has only one move that keeps it, "
                    "so the position is more forcing than it looks."
                ),
            )
        )
    return evidence
