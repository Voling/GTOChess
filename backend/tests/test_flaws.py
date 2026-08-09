from __future__ import annotations

import chess

from gtochess.analysis.flaws import find_flaws
from gtochess.analysis.profile import opening_edge
from gtochess.domain.flaws import IntentConfidence, OpeningRecord
from gtochess.domain.identity import position_key
from gtochess.domain.profile import MoveDecision, Trait

BASE_KEY = position_key(chess.Board())


def decision(
    *,
    digest: str = "aaaa",
    game_id: str = "g1",
    ply: int = 8,
    played_uci: str = "g1f3",
    best_uci: str = "e2e4",
    eval_loss_cp: int | None = 120,
    played_plan_digest: str | None = None,
    theory_plan_digest: str | None = None,
) -> MoveDecision:
    return MoveDecision(
        key=BASE_KEY.model_copy(update={"digest": digest}),
        game_id=game_id,
        ply=ply,
        played_uci=played_uci,
        played_san=played_uci,
        best_uci=best_uci,
        best_san=best_uci,
        mover_is_white=True,
        position_eval_cp=20,
        eval_loss_cp=eval_loss_cp,
        is_decision_point=True,
        played_reply_count=20,
        played_material_gain_cp=0,
        played_is_capture=False,
        played_is_check=False,
        played_targets_opponent_zone=False,
        best_targets_opponent_zone=False,
        played_plan_digest=played_plan_digest,
        theory_plan_digest=theory_plan_digest,
    )


class TestFlawDetection:
    def test_repeated_costly_move_is_reported(self) -> None:
        decisions = [decision(game_id=f"g{i}") for i in range(4)]
        flaws = find_flaws(decisions, min_occurrences=2, min_mean_loss_cp=50)
        assert len(flaws) == 1
        assert flaws[0].occurrences == 4
        assert flaws[0].played_san == "g1f3"
        assert flaws[0].best_san == "e2e4"

    def test_one_off_mistakes_are_ignored(self) -> None:
        flaws = find_flaws([decision()], min_occurrences=2)
        assert flaws == []

    def test_cheap_mistakes_are_ignored(self) -> None:
        decisions = [decision(game_id=f"g{i}", eval_loss_cp=10) for i in range(5)]
        assert find_flaws(decisions, min_mean_loss_cp=50) == []

    def test_playing_the_best_move_is_not_a_flaw(self) -> None:
        decisions = [
            decision(game_id=f"g{i}", played_uci="e2e4", best_uci="e2e4") for i in range(5)
        ]
        assert find_flaws(decisions) == []

    def test_unscored_decisions_are_ignored(self) -> None:
        decisions = [decision(game_id=f"g{i}", eval_loss_cp=None) for i in range(5)]
        assert find_flaws(decisions) == []

    def test_the_same_move_in_different_positions_is_grouped_separately(self) -> None:
        decisions = [decision(digest="aaaa", game_id=f"a{i}") for i in range(3)] + [
            decision(digest="bbbb", game_id=f"b{i}") for i in range(3)
        ]
        flaws = find_flaws(decisions)
        assert len(flaws) == 2

    def test_different_moves_in_one_position_are_grouped_separately(self) -> None:
        decisions = [decision(played_uci="g1f3", game_id=f"a{i}") for i in range(3)] + [
            decision(played_uci="b1c3", game_id=f"b{i}") for i in range(3)
        ]
        flaws = find_flaws(decisions)
        assert len(flaws) == 2

    def test_game_ids_are_deduplicated(self) -> None:
        decisions = [decision(game_id="g1"), decision(game_id="g1"), decision(game_id="g2")]
        flaws = find_flaws(decisions, min_occurrences=2)
        assert flaws[0].occurrences == 3
        assert flaws[0].game_ids == ("g1", "g2")


class TestDamageRanking:
    def test_frequent_small_errors_outrank_a_rare_large_one(self) -> None:
        frequent = [decision(digest="freq", game_id=f"f{i}", eval_loss_cp=60) for i in range(10)]
        rare = [
            decision(digest="rare", game_id=f"r{i}", played_uci="b1c3", eval_loss_cp=250)
            for i in range(2)
        ]
        flaws = find_flaws(frequent + rare)
        assert flaws[0].key.digest == "freq"
        assert flaws[0].damage_cp == 600
        assert flaws[1].damage_cp == 500

    def test_limit_truncates_the_list(self) -> None:
        decisions = []
        for index in range(5):
            decisions += [decision(digest=f"d{index}", game_id=f"g{index}-{i}") for i in range(3)]
        assert len(find_flaws(decisions, limit=2)) == 2


class TestIntent:
    def test_a_plan_seen_once_is_only_suggested(self) -> None:
        decisions = [
            decision(game_id=f"g{i}", played_plan_digest="plan-a" if i == 0 else None)
            for i in range(3)
        ]
        flaws = find_flaws(decisions)
        assert flaws[0].intent_confidence is IntentConfidence.SINGLE

    def test_a_plan_the_player_repeats_is_asserted(self) -> None:
        decisions = [decision(game_id=f"g{i}", played_plan_digest="plan-a") for i in range(4)]
        flaws = find_flaws(decisions)
        assert flaws[0].plan_recurrences == 4
        assert flaws[0].intent_confidence is IntentConfidence.REPEATED

    def test_recurrence_counts_across_the_whole_repertoire(self) -> None:
        here = [
            decision(digest="here", game_id=f"h{i}", played_plan_digest="plan-a") for i in range(2)
        ]
        elsewhere = [
            decision(digest="elsewhere", game_id=f"e{i}", played_plan_digest="plan-a")
            for i in range(6)
        ]
        flaws = find_flaws(here + elsewhere)
        assert all(f.plan_recurrences == 8 for f in flaws)

    def test_missing_plan_data_reports_unknown(self) -> None:
        decisions = [decision(game_id=f"g{i}") for i in range(3)]
        assert find_flaws(decisions)[0].intent_confidence is IntentConfidence.UNKNOWN

    def test_off_theme_needs_both_plans(self) -> None:
        matching = find_flaws(
            [
                decision(game_id=f"g{i}", played_plan_digest="x", theory_plan_digest="x")
                for i in range(3)
            ]
        )
        assert matching[0].is_off_theme is False

        diverging = find_flaws(
            [
                decision(game_id=f"g{i}", played_plan_digest="x", theory_plan_digest="y")
                for i in range(3)
            ]
        )
        assert diverging[0].is_off_theme is True

    def test_off_theme_is_false_when_theory_plan_is_unknown(self) -> None:
        flaws = find_flaws([decision(game_id=f"g{i}", played_plan_digest="x") for i in range(3)])
        assert flaws[0].is_off_theme is False


class TestOpeningEdge:
    def test_small_samples_are_excluded(self) -> None:
        records = [OpeningRecord(opening_id="a", games=3, score=1.0, population_score=0.5)]
        assert opening_edge(records, min_games=10) is None

    def test_a_hot_streak_is_pulled_toward_the_population(self) -> None:
        records = [OpeningRecord(opening_id="a", games=10, score=1.0, population_score=0.5)]
        score = opening_edge(records, min_games=10, prior_games=30)
        assert score is not None
        assert 0.1 < score.value < 0.15

    def test_a_large_sample_keeps_more_of_its_edge(self) -> None:
        small = opening_edge(
            [OpeningRecord(opening_id="a", games=10, score=0.8, population_score=0.5)],
            min_games=10,
        )
        large = opening_edge(
            [OpeningRecord(opening_id="a", games=300, score=0.8, population_score=0.5)],
            min_games=10,
        )
        assert small is not None
        assert large is not None
        assert large.value > small.value

    def test_matching_the_population_gives_no_edge(self) -> None:
        records = [OpeningRecord(opening_id="a", games=100, score=0.5, population_score=0.5)]
        score = opening_edge(records)
        assert score is not None
        assert score.value == 0.0

    def test_evidence_lists_the_strongest_openings_first(self) -> None:
        records = [
            OpeningRecord(opening_id="weak", games=50, score=0.3, population_score=0.5),
            OpeningRecord(opening_id="strong", games=50, score=0.7, population_score=0.5),
        ]
        score = opening_edge(records, evidence_limit=1)
        assert score is not None
        assert score.evidence == ("strong",)
        assert score.trait is Trait.OPENING_EDGE

    def test_sample_size_totals_the_games(self) -> None:
        records = [
            OpeningRecord(opening_id="a", games=40, score=0.6, population_score=0.5),
            OpeningRecord(opening_id="b", games=60, score=0.4, population_score=0.5),
        ]
        score = opening_edge(records)
        assert score is not None
        assert score.sample_size == 100
