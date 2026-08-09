from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence

from gtochess.domain.flaws import OpeningRecord
from gtochess.domain.profile import (
    MoveDecision,
    PlayerProfile,
    Trait,
    TraitScore,
    TraitUnit,
)
from gtochess.domain.repertoire import RepertoireNode

DEFAULT_BLUNDER_CP = 100
DEFAULT_WINNING_CP = 200
DEFAULT_LOSING_CP = -200

DECISION_TRAITS: tuple[Trait, ...] = (
    Trait.FORCING_PREFERENCE,
    Trait.MATERIAL_GREED,
    Trait.AGGRESSION,
    Trait.ACCURACY,
    Trait.RESILIENCE,
    Trait.CONVERSION,
    Trait.TILT,
)


def _evidence(decisions: Sequence[MoveDecision], limit: int) -> tuple[str, ...]:
    return tuple(d.key.digest for d in decisions[:limit])


def _share(
    trait: Trait,
    matched: Sequence[MoveDecision],
    considered: Sequence[MoveDecision],
    *,
    evidence_limit: int,
) -> TraitScore:
    return TraitScore(
        trait=trait,
        value=len(matched) / len(considered),
        unit=TraitUnit.SHARE,
        sample_size=len(considered),
        evidence=_evidence(matched, evidence_limit),
    )


def _mean_loss(
    trait: Trait, decisions: Sequence[MoveDecision], *, evidence_limit: int
) -> TraitScore:
    losses = [d.eval_loss_cp for d in decisions if d.eval_loss_cp is not None]
    worst = sorted(
        (d for d in decisions if d.eval_loss_cp is not None),
        key=lambda d: d.eval_loss_cp or 0,
        reverse=True,
    )
    return TraitScore(
        trait=trait,
        value=statistics.fmean(losses),
        unit=TraitUnit.CENTIPAWNS,
        sample_size=len(losses),
        evidence=_evidence(worst, evidence_limit),
    )


def opening_edge(
    records: Sequence[OpeningRecord],
    *,
    min_games: int = 10,
    prior_games: int = 30,
    evidence_limit: int = 5,
) -> TraitScore | None:
    pool = [r for r in records if r.games >= min_games]
    if not pool:
        return None

    scored: list[tuple[float, OpeningRecord]] = []
    for record in pool:
        weighted = record.score * record.games + record.population_score * prior_games
        adjusted = weighted / (record.games + prior_games)
        scored.append((adjusted - record.population_score, record))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return TraitScore(
        trait=Trait.OPENING_EDGE,
        value=statistics.fmean([edge for edge, _ in scored]),
        unit=TraitUnit.SHARE,
        sample_size=sum(r.games for r in pool),
        evidence=tuple(record.opening_id for _, record in scored[:evidence_limit]),
    )


def repertoire_consistency(
    nodes: Sequence[RepertoireNode], *, min_games: int = 3, evidence_limit: int = 5
) -> TraitScore | None:
    pool = [n for n in nodes if n.game_count >= min_games and n.reply_total >= 2]
    if not pool:
        return None
    least_consistent = sorted(pool, key=lambda n: n.top_reply_share)
    return TraitScore(
        trait=Trait.CONSISTENCY,
        value=statistics.fmean([n.top_reply_share for n in pool]),
        unit=TraitUnit.SHARE,
        sample_size=len(pool),
        evidence=tuple(n.key.digest for n in least_consistent[:evidence_limit]),
    )


def _chose_more_forcing(decision: MoveDecision) -> bool:
    return decision.played_reply_count < statistics.median(decision.alternative_reply_counts)


def _tilt(
    decisions: Sequence[MoveDecision],
    *,
    min_sample: int,
    evidence_limit: int,
    blunder_cp: int,
) -> TraitScore | None:
    by_game: dict[str, list[MoveDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.eval_loss_cp is not None:
            by_game[decision.game_id].append(decision)

    after_blunder: list[MoveDecision] = []
    after_clean: list[MoveDecision] = []
    for game in by_game.values():
        ordered = sorted(game, key=lambda d: d.ply)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if (previous.eval_loss_cp or 0) >= blunder_cp:
                after_blunder.append(current)
            else:
                after_clean.append(current)

    if len(after_blunder) < min_sample or not after_clean:
        return None

    elevated = statistics.fmean([d.eval_loss_cp or 0 for d in after_blunder])
    normal = statistics.fmean([d.eval_loss_cp or 0 for d in after_clean])
    worst = sorted(after_blunder, key=lambda d: d.eval_loss_cp or 0, reverse=True)

    return TraitScore(
        trait=Trait.TILT,
        value=elevated - normal,
        unit=TraitUnit.CENTIPAWNS,
        sample_size=len(after_blunder),
        evidence=_evidence(worst, evidence_limit),
    )


def build_profile(
    decisions: Sequence[MoveDecision],
    *,
    min_sample: int = 20,
    evidence_limit: int = 5,
    blunder_cp: int = DEFAULT_BLUNDER_CP,
    winning_cp: int = DEFAULT_WINNING_CP,
    losing_cp: int = DEFAULT_LOSING_CP,
) -> PlayerProfile:
    points = [d for d in decisions if d.is_decision_point]
    scored = [d for d in points if d.eval_loss_cp is not None]

    traits: list[TraitScore] = []
    omitted: list[Trait] = []

    def record(trait: Trait, score: TraitScore | None) -> None:
        if score is None or score.sample_size < min_sample:
            omitted.append(trait)
        else:
            traits.append(score)

    forcing_pool = [d for d in points if d.had_alternatives]
    record(
        Trait.FORCING_PREFERENCE,
        _share(
            Trait.FORCING_PREFERENCE,
            [d for d in forcing_pool if _chose_more_forcing(d)],
            forcing_pool,
            evidence_limit=evidence_limit,
        )
        if forcing_pool
        else None,
    )

    greed_pool = [d for d in points if d.capture_available and d.quiet_alternative_available]
    record(
        Trait.MATERIAL_GREED,
        _share(
            Trait.MATERIAL_GREED,
            [d for d in greed_pool if d.played_material_gain_cp > 0],
            greed_pool,
            evidence_limit=evidence_limit,
        )
        if greed_pool
        else None,
    )

    if points:
        played_share = sum(1 for d in points if d.played_targets_opponent_zone) / len(points)
        engine_share = sum(1 for d in points if d.best_targets_opponent_zone) / len(points)
        record(
            Trait.AGGRESSION,
            TraitScore(
                trait=Trait.AGGRESSION,
                value=played_share - engine_share,
                unit=TraitUnit.SHARE,
                sample_size=len(points),
                evidence=_evidence(
                    [d for d in points if d.played_targets_opponent_zone], evidence_limit
                ),
            ),
        )
    else:
        omitted.append(Trait.AGGRESSION)

    record(
        Trait.ACCURACY,
        _mean_loss(Trait.ACCURACY, scored, evidence_limit=evidence_limit) if scored else None,
    )

    losing = [d for d in scored if d.position_eval_cp <= losing_cp]
    record(
        Trait.RESILIENCE,
        _mean_loss(Trait.RESILIENCE, losing, evidence_limit=evidence_limit) if losing else None,
    )

    winning = [d for d in scored if d.position_eval_cp >= winning_cp]
    record(
        Trait.CONVERSION,
        _mean_loss(Trait.CONVERSION, winning, evidence_limit=evidence_limit) if winning else None,
    )

    record(
        Trait.TILT,
        _tilt(
            scored,
            min_sample=min_sample,
            evidence_limit=evidence_limit,
            blunder_cp=blunder_cp,
        ),
    )

    return PlayerProfile(
        traits=tuple(traits),
        omitted=tuple(omitted),
        decisions_considered=len(points),
        games_considered=len({d.game_id for d in decisions}),
    )
