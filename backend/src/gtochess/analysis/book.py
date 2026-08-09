from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

import chess

from gtochess.domain.book import FamilyBook, MoveAccuracy, OpeningPhase, PositionLosses
from gtochess.domain.graph import GraphEdge, RepertoireGraph
from gtochess.domain.models import MAX_LOSS_CP
from gtochess.engine.protocol import EngineProvider

CLEAN_BAND_CP = 50
PRIOR_GAMES = 25
BOOK_MAX_GAP = 2
BOOK_TOLERATED_LAPSES = 1

# Below this a line is not a pattern, it is a one-off. Carrying rarer edges into
# an analysis graph costs a great deal and buys nothing, because nothing under
# this floor is ever measured, scored or flagged.
MIN_VOLUME = 3

BOOK_MAX_PLY = 28
BOOK_MIN_VOLUME = MIN_VOLUME
BOOK_MAX_CHILDREN = 12


def _mover_cp(score_cp: int, white_to_move: bool) -> int:
    return score_cp if white_to_move else -score_cp


def _loss(best_cp: int, played_cp: int) -> int:
    return min(MAX_LOSS_CP, max(0, best_cp - played_cp))


def measure_losses(
    engine: EngineProvider,
    board: chess.Board,
    replies: Sequence[str],
    *,
    digest: str,
    depth: int,
) -> PositionLosses:
    multipv = max(len(replies) + 2, 6)
    report = engine.analyse(board, depth=depth, multipv=multipv)
    white = board.turn == chess.WHITE
    best = report.best
    best_cp = _mover_cp(best.score_cp, white)

    losses: dict[str, int] = {}
    sans: dict[str, str] = {}
    for line in report.lines:
        losses[line.move_uci] = _loss(best_cp, _mover_cp(line.score_cp, white))
        sans[line.move_uci] = line.move_san

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
        losses[uci] = _loss(best_cp, _mover_cp(after.score_cp, white))

    return PositionLosses(
        digest=digest,
        epd=board.epd(),
        depth=depth,
        best_uci=best.move_uci,
        best_san=best.move_san,
        best_cp=best.score_cp,
        losses=losses,
        sans=sans,
    )


def book_depth(
    accuracies: Sequence[MoveAccuracy],
    *,
    band_cp: int = CLEAN_BAND_CP,
    max_gap: int = BOOK_MAX_GAP,
    tolerated: int = BOOK_TOLERATED_LAPSES,
) -> int:
    """The last full move the player's opening still holds the band.

    One move over the band is a specific problem, not the end of what the
    player knows, so an isolated lapse is carried and only a run of them ends
    the book. Full moves nobody measured are skipped rather than treated as
    failures, since the sweep only prices positions above its volume floor.
    """
    depth = 0
    lapses = 0
    previous: int | None = None
    for entry in accuracies:
        if previous is not None and entry.full_move - previous > max_gap:
            break
        if entry.mean_loss_cp > band_cp:
            lapses += 1
            if lapses > tolerated:
                break
        else:
            lapses = 0
            depth = entry.full_move
        previous = entry.full_move
    return depth


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
    costs: dict[str, PositionLosses],
    *,
    band_cp: int = CLEAN_BAND_CP,
    prior_games: int = PRIOR_GAMES,
    min_games: int = 4,
) -> OpeningPhase:
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
        known = [(e, cost.for_move(e.uci)) for e in edges]
        known = [(e, loss) for e, loss in known if loss is not None]
        if not known:
            continue

        played = sum(e.games for e, _ in known)
        mean_loss = sum((loss or 0) * e.games for e, loss in known) / played
        clean = sum(e.games for e, loss in known if (loss or 0) <= band_cp) / played

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
        depth = book_depth(accuracies, band_cp=band_cp)
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
