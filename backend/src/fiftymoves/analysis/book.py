from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

import chess

from fiftymoves.domain.book import FamilyBook, MoveAccuracy, MoveCost, OpeningPhase
from fiftymoves.domain.graph import GraphEdge, RepertoireGraph
from fiftymoves.engine.protocol import EngineProvider

CLEAN_BAND_CP = 50
PRIOR_GAMES = 25


def _mover_cp(score_cp: int, white_to_move: bool) -> int:
    return score_cp if white_to_move else -score_cp


def price_position(
    engine: EngineProvider,
    board: chess.Board,
    replies: Sequence[str],
    *,
    digest: str,
    depth: int,
) -> MoveCost:
    """One search prices every reply, because multi-PV already ranks them all."""
    multipv = max(len(replies) + 2, 6)
    report = engine.analyse(board, depth=depth, multipv=multipv)
    white = board.turn == chess.WHITE
    best = report.best
    best_cp = _mover_cp(best.score_cp, white)

    losses: dict[str, int] = {}
    sans: dict[str, str] = {}
    for line in report.lines:
        losses[line.move_uci] = max(0, best_cp - _mover_cp(line.score_cp, white))
        sans[line.move_uci] = line.move_san

    # A reply the engine never listed is rare and always bad. It still needs a
    # number rather than a guess, so it gets its own search.
    for uci in replies:
        if uci in losses:
            continue
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue
        if move not in board.legal_moves:
            continue
        sans[uci] = board.san(move)
        child = board.copy(stack=False)
        child.push(move)
        after = engine.analyse(child, depth=depth, multipv=1)
        losses[uci] = max(0, best_cp - _mover_cp(after.score_cp, white))

    return MoveCost(
        digest=digest,
        epd=board.epd(),
        depth=depth,
        best_uci=best.move_uci,
        best_san=best.move_san,
        best_cp=best.score_cp,
        losses=losses,
        sans=sans,
    )


def _shrink(value: float, weight: float, population: float, prior: float) -> float:
    return (value * weight + population * prior) / (weight + prior)


class _Bucket:
    def __init__(self) -> None:
        self.weight = 0.0
        self.loss = 0.0
        self.clean = 0.0
        self.played = 0
        self.positions = 0

    def add(self, weight: float, loss: float, clean: float, played: int) -> None:
        self.weight += weight
        self.loss += weight * loss
        self.clean += weight * clean
        self.played += played
        self.positions += 1

    def accuracy(self, full_move: int) -> MoveAccuracy:
        return MoveAccuracy(
            full_move=full_move,
            positions=self.positions,
            played=self.played,
            mean_loss_cp=self.loss / self.weight if self.weight else 0.0,
            clean_share=self.clean / self.weight if self.weight else 0.0,
        )


def score_openings(
    graph: RepertoireGraph,
    costs: dict[str, MoveCost],
    *,
    band_cp: int = CLEAN_BAND_CP,
    prior_games: int = PRIOR_GAMES,
    min_games: int = 4,
) -> OpeningPhase:
    """Score how far the player's opening holds, per family and overall.

    Two things would otherwise make this meaningless. Repeating one line 1400
    times is a single piece of knowledge, not 1400, so a position counts by the
    square root of its volume. And a family seen a dozen times cannot claim a
    deep book, so its depth is pulled toward the player's own average.
    """
    nodes = {n.digest: n for n in graph.nodes}
    by_parent: dict[str, list[GraphEdge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.by_player:
            by_parent[edge.parent].append(edge)

    buckets: dict[str, dict[int, _Bucket]] = defaultdict(lambda: defaultdict(_Bucket))
    family_games: dict[str, int] = defaultdict(int)
    scored = 0
    moves = 0

    for digest, edges in by_parent.items():
        node = nodes.get(digest)
        cost = costs.get(digest)
        if node is None or cost is None:
            continue
        priced = [(e, cost.loss(e.uci)) for e in edges]
        priced = [(e, loss) for e, loss in priced if loss is not None]
        if not priced:
            continue

        played = sum(e.games for e, _ in priced)
        mean_loss = sum((loss or 0) * e.games for e, loss in priced) / played
        clean = sum(e.games for e, loss in priced if (loss or 0) <= band_cp) / played

        family = node.family or "unclassified"
        full_move = node.depth_ply // 2 + 1
        buckets[family][full_move].add(math.sqrt(played), mean_loss, clean, played)
        family_games[family] += played
        scored += 1
        moves += played

    if not buckets:
        return OpeningPhase(
            book_depth=0.0,
            clean_share=0.0,
            mean_loss_cp=0.0,
            positions_scored=0,
            moves_scored=0,
            band_cp=band_cp,
        )

    drafts: dict[str, tuple[tuple[MoveAccuracy, ...], int, float, float]] = {}
    for family, per_move in buckets.items():
        accuracies = tuple(per_move[m].accuracy(m) for m in sorted(per_move))
        # Counted from where the family first appears, not from move 1: only the
        # opening the player answers most owns the root, and every other family
        # becomes identifiable a move or two later.
        depth = 0
        expected = accuracies[0].full_move if accuracies else 0
        for entry in accuracies:
            if entry.full_move != expected or entry.mean_loss_cp > band_cp:
                break
            depth = entry.full_move
            expected += 1
        weight = sum(per_move[m].weight for m in per_move)
        loss = sum(per_move[m].loss for m in per_move) / weight if weight else 0.0
        clean = sum(per_move[m].clean for m in per_move) / weight if weight else 0.0
        drafts[family] = (accuracies, depth, clean, loss)

    total_games = sum(family_games.values())
    population_depth = (
        sum(d * family_games[f] for f, (_, d, _, _) in drafts.items()) / total_games
        if total_games
        else 0.0
    )

    books: list[FamilyBook] = []
    for family, (accuracies, depth, clean, loss) in drafts.items():
        games = family_games[family]
        if games < min_games:
            continue
        books.append(
            FamilyBook(
                key=family,
                name=family,
                games=games,
                positions=sum(a.positions for a in accuracies),
                by_move=accuracies,
                raw_depth=depth,
                book_depth=round(_shrink(depth, games, population_depth, prior_games), 1),
                clean_share=clean,
                mean_loss_cp=loss,
            )
        )
    books.sort(key=lambda b: -b.games)

    kept = sum(b.games for b in books) or 1
    return OpeningPhase(
        families=tuple(books),
        book_depth=round(sum(b.book_depth * b.games for b in books) / kept, 1),
        clean_share=sum(b.clean_share * b.games for b in books) / kept,
        mean_loss_cp=sum(b.mean_loss_cp * b.games for b in books) / kept,
        positions_scored=scored,
        moves_scored=moves,
        band_cp=band_cp,
    )
