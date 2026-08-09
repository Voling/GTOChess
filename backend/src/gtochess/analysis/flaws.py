from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence

from gtochess.domain.flaws import OpeningFlaw
from gtochess.domain.profile import MoveDecision


def _plan_recurrence_counts(decisions: Sequence[MoveDecision]) -> Counter[str]:
    return Counter(d.played_plan_digest for d in decisions if d.played_plan_digest is not None)


def find_flaws(
    decisions: Sequence[MoveDecision],
    *,
    min_occurrences: int = 2,
    min_mean_loss_cp: float = 50.0,
    limit: int | None = None,
) -> list[OpeningFlaw]:
    recurrences = _plan_recurrence_counts(decisions)

    grouped: dict[tuple[str, str], list[MoveDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.eval_loss_cp is None or decision.played_uci == decision.best_uci:
            continue
        grouped[(decision.key.digest, decision.played_uci)].append(decision)

    flaws: list[OpeningFlaw] = []
    for group in grouped.values():
        if len(group) < min_occurrences:
            continue
        mean_loss = statistics.fmean([d.eval_loss_cp or 0 for d in group])
        if mean_loss < min_mean_loss_cp:
            continue

        first = group[0]
        plan_digest = first.played_plan_digest
        flaws.append(
            OpeningFlaw(
                key=first.key,
                depth_ply=first.ply,
                played_uci=first.played_uci,
                played_san=first.played_san,
                best_uci=first.best_uci,
                best_san=first.best_san,
                occurrences=len(group),
                mean_eval_loss_cp=mean_loss,
                played_plan_digest=plan_digest,
                theory_plan_digest=first.theory_plan_digest,
                plan_recurrences=recurrences[plan_digest] if plan_digest else 0,
                game_ids=tuple(sorted({d.game_id for d in group})),
            )
        )

    flaws.sort(key=lambda f: (-f.damage_cp, f.depth_ply, f.played_uci))
    return flaws if limit is None else flaws[:limit]
