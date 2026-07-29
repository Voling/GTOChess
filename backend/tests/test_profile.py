from __future__ import annotations

import chess

from fiftymoves.analysis.profile import DECISION_TRAITS, build_profile
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.profile import MoveDecision, Trait, TraitUnit

KEY = position_key(chess.Board())


def decision(
    *,
    game_id: str = "g1",
    ply: int = 0,
    eval_loss_cp: int | None = 0,
    position_eval_cp: int = 0,
    is_decision_point: bool = True,
    played_reply_count: int = 20,
    played_material_gain_cp: int = 0,
    played_targets_opponent_zone: bool = False,
    best_targets_opponent_zone: bool = False,
    alternative_reply_counts: tuple[int, ...] = (30,),
    alternative_material_gains: tuple[int, ...] = (0,),
) -> MoveDecision:
    return MoveDecision(
        key=KEY,
        game_id=game_id,
        ply=ply,
        played_uci="e2e4",
        played_san="e4",
        best_uci="e2e4",
        best_san="e4",
        mover_is_white=True,
        position_eval_cp=position_eval_cp,
        eval_loss_cp=eval_loss_cp,
        is_decision_point=is_decision_point,
        played_reply_count=played_reply_count,
        played_material_gain_cp=played_material_gain_cp,
        played_is_capture=played_material_gain_cp > 0,
        played_is_check=False,
        played_targets_opponent_zone=played_targets_opponent_zone,
        best_targets_opponent_zone=best_targets_opponent_zone,
        alternative_reply_counts=alternative_reply_counts,
        alternative_material_gains=alternative_material_gains,
    )


def batch(count: int, **kwargs: object) -> list[MoveDecision]:
    return [decision(ply=i, **kwargs) for i in range(count)]  # type: ignore[arg-type]


def game(game_id: str, losses: list[int]) -> list[MoveDecision]:
    return [
        decision(game_id=game_id, ply=ply, eval_loss_cp=loss) for ply, loss in enumerate(losses)
    ]


class TestDecisionPointFilter:
    def test_forced_moves_are_excluded(self) -> None:
        profile = build_profile(batch(25) + batch(25, is_decision_point=False), min_sample=5)
        assert profile.decisions_considered == 25

    def test_counts_distinct_games(self) -> None:
        decisions = batch(10, game_id="a") + batch(10, game_id="b")
        assert build_profile(decisions, min_sample=5).games_considered == 2


class TestForcingPreference:
    def test_choosing_the_sharper_move_scores_high(self) -> None:
        profile = build_profile(
            batch(30, played_reply_count=10, alternative_reply_counts=(30, 40)),
            min_sample=5,
        )
        score = profile.get(Trait.FORCING_PREFERENCE)
        assert score is not None
        assert score.value == 1.0
        assert score.unit is TraitUnit.SHARE

    def test_choosing_the_quieter_move_scores_low(self) -> None:
        profile = build_profile(
            batch(30, played_reply_count=40, alternative_reply_counts=(10, 12)),
            min_sample=5,
        )
        score = profile.get(Trait.FORCING_PREFERENCE)
        assert score is not None
        assert score.value == 0.0

    def test_decisions_without_alternatives_are_skipped(self) -> None:
        profile = build_profile(
            batch(30, alternative_reply_counts=(), alternative_material_gains=()),
            min_sample=5,
        )
        assert Trait.FORCING_PREFERENCE in profile.omitted


class TestMaterialGreed:
    def test_only_counts_decisions_offering_both_a_capture_and_a_quiet_move(self) -> None:
        greedy = batch(
            20,
            played_material_gain_cp=100,
            alternative_material_gains=(0,),
        )
        no_capture_anywhere = batch(
            20,
            game_id="g2",
            played_material_gain_cp=0,
            alternative_material_gains=(0,),
        )
        profile = build_profile(greedy + no_capture_anywhere, min_sample=5)
        score = profile.get(Trait.MATERIAL_GREED)
        assert score is not None
        assert score.sample_size == 20
        assert score.value == 1.0

    def test_declining_available_material_scores_low(self) -> None:
        profile = build_profile(
            batch(20, played_material_gain_cp=0, alternative_material_gains=(0, 500)),
            min_sample=5,
        )
        score = profile.get(Trait.MATERIAL_GREED)
        assert score is not None
        assert score.value == 0.0


class TestAggression:
    def test_measured_against_the_engine_baseline(self) -> None:
        profile = build_profile(
            batch(20, played_targets_opponent_zone=True, best_targets_opponent_zone=True),
            min_sample=5,
        )
        score = profile.get(Trait.AGGRESSION)
        assert score is not None
        assert score.value == 0.0

    def test_positive_when_the_player_pushes_further_than_the_engine(self) -> None:
        profile = build_profile(
            batch(20, played_targets_opponent_zone=True, best_targets_opponent_zone=False),
            min_sample=5,
        )
        score = profile.get(Trait.AGGRESSION)
        assert score is not None
        assert score.value == 1.0

    def test_negative_when_the_player_holds_back(self) -> None:
        profile = build_profile(
            batch(20, played_targets_opponent_zone=False, best_targets_opponent_zone=True),
            min_sample=5,
        )
        score = profile.get(Trait.AGGRESSION)
        assert score is not None
        assert score.value == -1.0


class TestAccuracyBuckets:
    def test_accuracy_is_mean_eval_loss(self) -> None:
        profile = build_profile(
            batch(10, eval_loss_cp=40) + batch(10, eval_loss_cp=60), min_sample=5
        )
        score = profile.get(Trait.ACCURACY)
        assert score is not None
        assert score.value == 50.0
        assert score.unit is TraitUnit.CENTIPAWNS

    def test_resilience_only_uses_losing_positions(self) -> None:
        profile = build_profile(
            batch(20, position_eval_cp=-400, eval_loss_cp=90)
            + batch(20, game_id="g2", position_eval_cp=0, eval_loss_cp=10),
            min_sample=5,
        )
        score = profile.get(Trait.RESILIENCE)
        assert score is not None
        assert score.sample_size == 20
        assert score.value == 90.0

    def test_conversion_only_uses_winning_positions(self) -> None:
        profile = build_profile(
            batch(20, position_eval_cp=500, eval_loss_cp=70)
            + batch(20, game_id="g2", position_eval_cp=0, eval_loss_cp=10),
            min_sample=5,
        )
        score = profile.get(Trait.CONVERSION)
        assert score is not None
        assert score.sample_size == 20
        assert score.value == 70.0

    def test_unscored_decisions_are_ignored(self) -> None:
        profile = build_profile(
            batch(20, eval_loss_cp=50) + batch(20, game_id="g2", eval_loss_cp=None),
            min_sample=5,
        )
        score = profile.get(Trait.ACCURACY)
        assert score is not None
        assert score.sample_size == 20


class TestTilt:
    def test_elevated_loss_after_a_blunder_is_positive(self) -> None:
        decisions: list[MoveDecision] = []
        for index in range(10):
            decisions += game(f"g{index}", [250, 80, 10, 10, 10, 10])
        score = build_profile(decisions, min_sample=5).get(Trait.TILT)
        assert score is not None
        assert score.value == 70.0
        assert score.sample_size == 10
        assert score.unit is TraitUnit.CENTIPAWNS

    def test_steady_play_after_a_blunder_scores_zero(self) -> None:
        decisions: list[MoveDecision] = []
        for index in range(10):
            decisions += game(f"g{index}", [250, 10, 10, 10, 10, 10])
        score = build_profile(decisions, min_sample=5).get(Trait.TILT)
        assert score is not None
        assert score.value == 0.0

    def test_compares_against_moves_following_clean_play_not_all_moves(self) -> None:
        decisions: list[MoveDecision] = []
        for index in range(10):
            decisions += game(f"g{index}", [250, 80, 10, 10, 10, 10])
        score = build_profile(decisions, min_sample=5).get(Trait.TILT)
        assert score is not None
        assert score.sample_size == 10

    def test_a_blunder_ending_a_game_does_not_taint_the_next_game(self) -> None:
        decisions = game("g1", [10, 10, 400]) + game("g2", [50, 10, 10])
        profile = build_profile(decisions, min_sample=1)
        assert Trait.TILT in profile.omitted


class TestSampleSize:
    def test_traits_below_the_threshold_are_omitted(self) -> None:
        profile = build_profile(batch(3), min_sample=20)
        assert profile.traits == ()
        assert Trait.ACCURACY in profile.omitted

    def test_empty_input_omits_every_decision_trait(self) -> None:
        profile = build_profile([])
        assert profile.traits == ()
        assert profile.decisions_considered == 0
        assert set(profile.omitted) == set(DECISION_TRAITS)
