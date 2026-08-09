from __future__ import annotations

from gtochess.analysis.annotations import classify
from gtochess.domain.annotations import MoveQuality
from gtochess.domain.book import PositionLosses
from gtochess.domain.graph import GraphEdge, RepertoireGraph
from gtochess.domain.outcomes import MoveOutcome, OutcomeReport, QualityOutcome

ORDER = (MoveQuality.BLUNDER, MoveQuality.MISTAKE, MoveQuality.DUBIOUS, MoveQuality.SOUND)


class _Bucket:
    def __init__(self) -> None:
        self.moves = 0
        self.games = 0
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.loss_cp = 0

    def add(self, edge: GraphEdge, loss_cp: int) -> None:
        self.moves += 1
        self.games += edge.games
        self.wins += edge.wins
        self.draws += edge.draws
        self.losses += edge.losses
        self.loss_cp += loss_cp * edge.games

    @property
    def decided(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def score(self) -> float:
        return (self.wins + self.draws / 2) / self.decided if self.decided else 0.0

    def outcome(self, quality: MoveQuality) -> QualityOutcome:
        return QualityOutcome(
            quality=quality,
            moves=self.moves,
            games=self.games,
            wins=self.wins,
            draws=self.draws,
            losses=self.losses,
            score=self.score,
            mean_loss_cp=self.loss_cp / self.games if self.games else 0.0,
        )


def measure_outcomes(
    graph: RepertoireGraph,
    losses: dict[str, PositionLosses],
    *,
    dubious_cp: int = 90,
    mistake_cp: int = 160,
    blunder_cp: int = 300,
    worst_limit: int = 20,
) -> OutcomeReport:
    paths = {n.digest: " ".join(n.san_path) for n in graph.nodes}
    buckets: dict[MoveQuality, _Bucket] = {q: _Bucket() for q in ORDER}
    priced: list[tuple[GraphEdge, int, MoveQuality, str]] = []
    unmeasured = 0

    for edge in graph.edges:
        if not edge.by_player:
            continue
        held = losses.get(edge.parent)
        loss_cp = held.for_move(edge.uci) if held else None
        if held is None or loss_cp is None:
            unmeasured += 1
            continue
        quality = classify(
            loss_cp, dubious_cp=dubious_cp, mistake_cp=mistake_cp, blunder_cp=blunder_cp
        )
        buckets[quality].add(edge, loss_cp)
        priced.append((edge, loss_cp, quality, held.best_san))

    if not priced:
        return OutcomeReport(moves_unmeasured=unmeasured)

    sound = buckets[MoveQuality.SOUND]
    flawed = _Bucket()
    for quality in ORDER:
        if quality is MoveQuality.SOUND:
            continue
        bucket = buckets[quality]
        flawed.moves += bucket.moves
        flawed.games += bucket.games
        flawed.wins += bucket.wins
        flawed.draws += bucket.draws
        flawed.losses += bucket.losses

    baseline = sound.score
    worst = [
        MoveOutcome(
            parent=edge.parent,
            child=edge.child,
            uci=edge.uci,
            san=edge.san,
            line=paths.get(edge.parent, ""),
            quality=quality,
            loss_cp=loss_cp,
            best_san=best_san,
            games=edge.games,
            wins=edge.wins,
            draws=edge.draws,
            losses=edge.losses,
            score=edge.score,
            points_lost=round(edge.decided * (baseline - edge.score), 2),
        )
        for edge, loss_cp, quality, best_san in priced
        if quality is not MoveQuality.SOUND
    ]
    worst.sort(key=lambda m: (-m.points_lost, -m.loss_cp, m.san))

    return OutcomeReport(
        by_quality=tuple(buckets[q].outcome(q) for q in ORDER if buckets[q].moves),
        worst=tuple(worst[:worst_limit]),
        moves_measured=len(priced),
        moves_unmeasured=unmeasured,
        sound_score=baseline,
        flawed_score=flawed.score,
        score_gap=round(baseline - flawed.score, 4) if flawed.decided else 0.0,
    )
