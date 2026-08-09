from __future__ import annotations

import chess

from gtochess.analysis.profile import repertoire_consistency
from gtochess.analysis.selection import select_for_analysis
from gtochess.domain.identity import position_key
from gtochess.domain.models import Variant
from gtochess.domain.profile import Trait, TraitUnit
from gtochess.domain.repertoire import (
    RepertoireNode,
    SelectionPolicy,
    SkipReason,
)


def node(
    seed: int,
    *,
    depth_ply: int = 6,
    game_count: int = 10,
    replies: dict[str, int] | None = None,
) -> RepertoireNode:
    return RepertoireNode(
        key=position_key(chess.Board()).model_copy(
            update={"digest": f"{seed:032x}", "variant": Variant.STANDARD}
        ),
        depth_ply=depth_ply,
        game_count=game_count,
        replies=replies if replies is not None else {"e2e4": game_count},
    )


class TestDepthCap:
    def test_nodes_beyond_the_cap_are_skipped(self) -> None:
        nodes = [node(1, depth_ply=10), node(2, depth_ply=40)]
        result = select_for_analysis(nodes, SelectionPolicy(max_ply=24))
        assert result.selected_count == 1
        assert result.skipped[SkipReason.TOO_DEEP] == 1

    def test_the_cap_is_inclusive(self) -> None:
        result = select_for_analysis([node(1, depth_ply=24)], SelectionPolicy(max_ply=24))
        assert result.selected_count == 1


class TestShallowFloor:
    def test_the_opening_moves_are_skipped(self) -> None:
        nodes = [node(1, depth_ply=0), node(2, depth_ply=2), node(3, depth_ply=8)]
        result = select_for_analysis(nodes, SelectionPolicy(min_ply=6))
        assert result.selected_count == 1
        assert result.skipped[SkipReason.TOO_SHALLOW] == 2

    def test_the_floor_is_inclusive(self) -> None:
        result = select_for_analysis([node(1, depth_ply=6)], SelectionPolicy(min_ply=6))
        assert result.selected_count == 1

    def test_a_busy_shallow_node_is_still_skipped(self) -> None:
        crowded = node(1, depth_ply=1, game_count=400, replies={"e2e4": 200, "d2d4": 200})
        result = select_for_analysis([crowded], SelectionPolicy(min_ply=6))
        assert result.selected_count == 0
        assert result.skipped[SkipReason.TOO_SHALLOW] == 1

    def test_shallow_skips_are_accounted_for(self) -> None:
        nodes = [node(i, depth_ply=i % 10) for i in range(30)]
        result = select_for_analysis(nodes, SelectionPolicy(min_ply=6, budget=5))
        assert result.considered == 30
        assert result.fully_accounted_for


class TestVolume:
    def test_sparse_nodes_without_a_choice_are_skipped(self) -> None:
        nodes = [node(1, game_count=1, replies={"e2e4": 1})]
        result = select_for_analysis(nodes, SelectionPolicy(min_games=3))
        assert result.selected_count == 0
        assert result.skipped[SkipReason.LOW_VOLUME] == 1

    def test_sparse_nodes_where_the_player_diverged_are_kept(self) -> None:
        nodes = [node(1, game_count=2, replies={"e2e4": 1, "d2d4": 1})]
        result = select_for_analysis(nodes, SelectionPolicy(min_games=3, min_divergence_games=2))
        assert result.selected_count == 1

    def test_high_volume_nodes_are_kept_even_without_a_choice(self) -> None:
        nodes = [node(1, game_count=30, replies={"e2e4": 30})]
        result = select_for_analysis(nodes, SelectionPolicy(min_games=3))
        assert result.selected_count == 1


class TestFrontierSampling:
    def test_sampling_caps_divergence_nodes_per_depth(self) -> None:
        nodes = [
            node(i, depth_ply=14, game_count=2, replies={"e2e4": 1, "d2d4": 1}) for i in range(10)
        ]
        result = select_for_analysis(
            nodes, SelectionPolicy(min_games=5, min_divergence_games=2, frontier_sample=3)
        )
        assert result.selected_count == 3
        assert result.skipped[SkipReason.LOW_VOLUME] == 7

    def test_sampling_is_per_depth_not_global(self) -> None:
        nodes = [
            node(i, depth_ply=depth, game_count=2, replies={"e2e4": 1, "d2d4": 1})
            for depth in (12, 14)
            for i in range(4)
        ]
        result = select_for_analysis(
            nodes, SelectionPolicy(min_games=5, min_divergence_games=2, frontier_sample=2)
        )
        assert result.selected_count == 4


class TestBudget:
    def test_budget_truncates_and_reports_the_drop(self) -> None:
        nodes = [node(i, game_count=10) for i in range(50)]
        result = select_for_analysis(nodes, SelectionPolicy(budget=20))
        assert result.selected_count == 20
        assert result.skipped[SkipReason.OVER_BUDGET] == 30

    def test_budget_keeps_the_highest_priority_nodes(self) -> None:
        nodes = [
            node(1, game_count=5, replies={"e2e4": 5}),
            node(2, game_count=40, replies={"e2e4": 40}),
            node(3, game_count=8, replies={"e2e4": 4, "d2d4": 4}),
        ]
        result = select_for_analysis(nodes, SelectionPolicy(budget=1))
        assert result.selected[0].has_choice is True


class TestAccounting:
    def test_nothing_is_dropped_silently(self) -> None:
        nodes = (
            [node(i, depth_ply=40) for i in range(3)]
            + [node(10 + i, game_count=1, replies={"e2e4": 1}) for i in range(4)]
            + [node(20 + i, game_count=10) for i in range(30)]
        )
        result = select_for_analysis(nodes, SelectionPolicy(budget=10))
        assert result.considered == len(nodes)
        assert result.fully_accounted_for

    def test_empty_input_is_accounted_for(self) -> None:
        result = select_for_analysis([])
        assert result.selected_count == 0
        assert result.fully_accounted_for


class TestRepertoireConsistency:
    def test_always_playing_the_same_move_scores_one(self) -> None:
        nodes = [node(i, game_count=10, replies={"e2e4": 10}) for i in range(5)]
        score = repertoire_consistency(nodes)
        assert score is not None
        assert score.value == 1.0
        assert score.unit is TraitUnit.SHARE
        assert score.trait is Trait.CONSISTENCY

    def test_splitting_evenly_scores_low(self) -> None:
        nodes = [node(i, game_count=10, replies={"e2e4": 5, "d2d4": 5}) for i in range(5)]
        score = repertoire_consistency(nodes)
        assert score is not None
        assert score.value == 0.5

    def test_nodes_below_the_volume_floor_are_excluded(self) -> None:
        nodes = [node(1, game_count=1, replies={"e2e4": 1})]
        assert repertoire_consistency(nodes, min_games=3) is None

    def test_evidence_points_at_the_least_consistent_nodes(self) -> None:
        steady = node(1, game_count=10, replies={"e2e4": 10})
        mixed = node(2, game_count=10, replies={"e2e4": 5, "d2d4": 5})
        score = repertoire_consistency([steady, mixed], evidence_limit=1)
        assert score is not None
        assert score.evidence == (mixed.key.digest,)

    def test_needs_no_engine(self) -> None:
        nodes = [node(i, game_count=4, replies={"e2e4": 3, "d2d4": 1}) for i in range(3)]
        score = repertoire_consistency(nodes)
        assert score is not None
        assert score.sample_size == 3
