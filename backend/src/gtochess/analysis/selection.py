from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from gtochess.domain.repertoire import (
    RepertoireNode,
    SelectionPolicy,
    SelectionResult,
    SkipReason,
)


def _priority(node: RepertoireNode) -> tuple[int, int, int]:
    return (int(node.has_choice), node.game_count, -node.depth_ply)


def select_for_analysis(
    nodes: Sequence[RepertoireNode], policy: SelectionPolicy | None = None
) -> SelectionResult:
    policy = policy or SelectionPolicy()
    skipped: dict[SkipReason, int] = defaultdict(int)

    within_depth: list[RepertoireNode] = []
    for node in nodes:
        if node.depth_ply < policy.min_ply:
            skipped[SkipReason.TOO_SHALLOW] += 1
        elif node.depth_ply > policy.max_ply:
            skipped[SkipReason.TOO_DEEP] += 1
        else:
            within_depth.append(node)

    core: list[RepertoireNode] = []
    frontier: list[RepertoireNode] = []
    for node in within_depth:
        if node.game_count >= policy.min_games:
            core.append(node)
        elif node.has_choice and node.game_count >= policy.min_divergence_games:
            frontier.append(node)
        else:
            skipped[SkipReason.LOW_VOLUME] += 1

    frontier.sort(key=_priority, reverse=True)
    by_parent_depth: dict[int, int] = defaultdict(int)
    sampled: list[RepertoireNode] = []
    for node in frontier:
        if by_parent_depth[node.depth_ply] >= policy.frontier_sample:
            skipped[SkipReason.LOW_VOLUME] += 1
            continue
        by_parent_depth[node.depth_ply] += 1
        sampled.append(node)

    ranked = sorted(core + sampled, key=_priority, reverse=True)
    selected = ranked[: policy.budget]
    dropped = len(ranked) - len(selected)
    if dropped:
        skipped[SkipReason.OVER_BUDGET] += dropped

    return SelectionResult(
        selected=tuple(selected),
        skipped=dict(skipped),
        considered=len(nodes),
    )
