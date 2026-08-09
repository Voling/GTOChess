from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

import chess

from gtochess.domain.annotations import AnnotationSet, MoveAnnotation, MoveQuality
from gtochess.domain.graph import GraphEdge, RepertoireGraph
from gtochess.domain.models import EngineReport
from gtochess.engine.protocol import EngineProvider


def classify(
    loss_cp: int, *, dubious_cp: int = 90, mistake_cp: int = 160, blunder_cp: int = 300
) -> MoveQuality:
    if loss_cp >= blunder_cp:
        return MoveQuality.BLUNDER
    if loss_cp >= mistake_cp:
        return MoveQuality.MISTAKE
    if loss_cp >= dubious_cp:
        return MoveQuality.DUBIOUS
    return MoveQuality.SOUND


def _mover_cp(score_cp: int, white_to_move: bool) -> int:
    return score_cp if white_to_move else -score_cp


def _scores_by_move(report: EngineReport) -> dict[str, int]:
    return {line.move_uci: line.score_cp for line in report.lines}


def annotate_position(
    board: chess.Board,
    edges: Sequence[GraphEdge],
    report: EngineReport,
    *,
    evaluate_child: Callable[[chess.Board], int] | None = None,
    dubious_cp: int = 90,
    mistake_cp: int = 160,
    blunder_cp: int = 300,
) -> list[MoveAnnotation]:
    if not report.lines:
        return []

    white_to_move = board.turn == chess.WHITE
    best = report.best
    best_cp = _mover_cp(best.score_cp, white_to_move)
    known = _scores_by_move(report)

    out: list[MoveAnnotation] = []
    for edge in edges:
        played_cp = known.get(edge.uci)
        if played_cp is None:
            if evaluate_child is None:
                continue
            try:
                move = chess.Move.from_uci(edge.uci)
            except ValueError:
                continue
            if move not in board.legal_moves:
                continue
            probe = board.copy(stack=False)
            probe.push(move)
            played_cp = evaluate_child(probe)

        loss = best_cp - _mover_cp(played_cp, white_to_move)
        out.append(
            MoveAnnotation(
                parent=edge.parent,
                child=edge.child,
                san=edge.san,
                quality=classify(
                    loss, dubious_cp=dubious_cp, mistake_cp=mistake_cp, blunder_cp=blunder_cp
                ),
                loss_cp=max(0, loss),
                best_san=best.move_san,
                games=edge.games,
                by_player=edge.by_player,
            )
        )
    return out


def annotate_graph(
    engine: EngineProvider,
    graph: RepertoireGraph,
    *,
    username: str,
    shape: str,
    depth: int = 14,
    dubious_cp: int = 90,
    mistake_cp: int = 160,
    blunder_cp: int = 300,
    player_only: bool = True,
    min_games: int = 1,
    budget: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> AnnotationSet:
    wanted = [e for e in graph.edges if e.games >= min_games and (e.by_player or not player_only)]
    by_parent: dict[str, list[GraphEdge]] = defaultdict(list)
    for edge in wanted:
        by_parent[edge.parent].append(edge)

    epds = {node.digest: node.epd for node in graph.nodes}
    # Busiest positions first, so a budget cut keeps what the player plays most.
    parents = sorted(by_parent, key=lambda d: -sum(e.games for e in by_parent[d]))
    truncated = budget is not None and len(parents) > budget
    if budget is not None:
        parents = parents[:budget]

    annotations: list[MoveAnnotation] = []
    searched = 0
    for index, parent in enumerate(parents, start=1):
        epd = epds.get(parent)
        if epd is None:
            continue
        board = chess.Board(f"{epd} 0 1")
        edges = by_parent[parent]
        multipv = max(len(edges) + 2, 4)
        report = engine.analyse(board, depth=depth, multipv=multipv)
        searched += 1

        def evaluate_child(child: chess.Board) -> int:
            nonlocal searched
            searched += 1
            return engine.analyse(child, depth=depth, multipv=1).score_cp

        annotations.extend(
            annotate_position(
                board,
                edges,
                report,
                evaluate_child=evaluate_child,
                dubious_cp=dubious_cp,
                mistake_cp=mistake_cp,
                blunder_cp=blunder_cp,
            )
        )
        if on_progress is not None:
            on_progress(index, len(parents))

    annotations.sort(key=lambda a: (-a.loss_cp, -a.games, a.san))
    return AnnotationSet(
        username=username,
        shape=shape,
        annotations=tuple(annotations),
        positions_searched=searched,
        edges_considered=len(wanted),
        depth=depth,
        truncated=truncated,
    )
