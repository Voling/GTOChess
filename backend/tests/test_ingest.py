from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from gtochess.config import Settings
from gtochess.domain.games import GameRecord, GameSource
from gtochess.domain.models import MATE_SCORE_CP, Variant
from gtochess.ingest.parse import UnusableGame, parse_lichess_game
from gtochess.ingest.pipeline import export_windows
from gtochess.ingest.repertoire import build_decision_nodes, build_opening_records

ITALIAN = "e4 e5 Nf3 Nc6 Bc4 Bc5"


def raw_game(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc123",
        "rated": True,
        "variant": "standard",
        "speed": "blitz",
        "perf": "blitz",
        "createdAt": 1700000000000,
        "status": "mate",
        "winner": "white",
        "moves": ITALIAN,
        "clocks": [18000, 18000, 17500, 17400],
        "opening": {"eco": "C50", "name": "Italian Game", "ply": 6},
        "clock": {"initial": 180, "increment": 2, "totalTime": 240},
        "players": {
            "white": {"user": {"id": "dylanette", "name": "dylanette"}, "rating": 1800},
            "black": {"user": {"id": "opponent", "name": "opponent"}, "rating": 1750},
        },
    }
    base.update(overrides)
    return base


def game_record(**overrides: Any) -> GameRecord:
    base: dict[str, Any] = {
        "source": GameSource.LICHESS,
        "game_id": "g1",
        "played_at_ms": 1700000000000,
        "variant": Variant.STANDARD,
        "speed": "blitz",
        "rated": True,
        "player_is_white": True,
        "player_rating": 1800,
        "opponent_rating": 1750,
        "score": 1.0,
        "eco": "C50",
        "opening_name": "Italian Game",
        "opening_ply": 6,
        "initial_fen": None,
        "moves_san": tuple(ITALIAN.split()),
        "clocks_cs": (),
        "evals_cp": (),
        "initial_seconds": 180,
        "increment_seconds": 2,
    }
    base.update(overrides)
    return GameRecord(**base)


class TestColourAndScore:
    def test_identifies_the_player_as_white(self) -> None:
        assert parse_lichess_game(raw_game(), "dylanette").player_is_white is True

    def test_identifies_the_player_as_black(self) -> None:
        raw = raw_game(
            players={
                "white": {"user": {"id": "opponent"}, "rating": 1750},
                "black": {"user": {"id": "dylanette"}, "rating": 1800},
            }
        )
        record = parse_lichess_game(raw, "dylanette")
        assert record.player_is_white is False
        assert record.player_rating == 1800
        assert record.opponent_rating == 1750

    def test_username_match_is_case_insensitive(self) -> None:
        assert parse_lichess_game(raw_game(), "DyLaNeTtE").player_is_white is True

    def test_rejects_a_game_the_player_did_not_play(self) -> None:
        with pytest.raises(UnusableGame, match="did not play"):
            parse_lichess_game(raw_game(), "someone-else")

    def test_win_draw_and_loss_score(self) -> None:
        assert parse_lichess_game(raw_game(winner="white"), "dylanette").score == 1.0
        assert parse_lichess_game(raw_game(winner="black"), "dylanette").score == 0.0
        raw = raw_game()
        raw.pop("winner")
        assert parse_lichess_game(raw, "dylanette").score == 0.5


class TestUnusableGames:
    @pytest.mark.parametrize("status", ["aborted", "noStart", "created", "started"])
    def test_unplayed_statuses_are_rejected(self, status: str) -> None:
        with pytest.raises(UnusableGame):
            parse_lichess_game(raw_game(status=status), "dylanette")

    def test_unsupported_variants_are_rejected(self) -> None:
        with pytest.raises(UnusableGame, match="variant"):
            parse_lichess_game(raw_game(variant="crazyhouse"), "dylanette")

    def test_empty_move_list_is_rejected(self) -> None:
        with pytest.raises(UnusableGame, match="no moves"):
            parse_lichess_game(raw_game(moves=""), "dylanette")


class TestEvals:
    def test_centipawn_evals_are_read_per_ply(self) -> None:
        raw = raw_game(analysis=[{"eval": 18}, {"eval": -40}, {"eval": 12}])
        assert parse_lichess_game(raw, "dylanette").evals_cp == (18, -40, 12)

    def test_mate_scores_are_folded(self) -> None:
        raw = raw_game(analysis=[{"mate": 3}, {"mate": -2}])
        record = parse_lichess_game(raw, "dylanette")
        assert record.evals_cp == (MATE_SCORE_CP - 3, -(MATE_SCORE_CP - 2))

    def test_entries_without_a_score_become_none(self) -> None:
        raw = raw_game(analysis=[{"judgment": {"name": "Blunder"}}])
        assert parse_lichess_game(raw, "dylanette").evals_cp == (None,)

    def test_absent_analysis_yields_no_evals(self) -> None:
        assert parse_lichess_game(raw_game(), "dylanette").evals_cp == ()


class TestChess960:
    def test_variant_and_initial_fen_are_carried(self) -> None:
        raw = raw_game(
            variant="chess960",
            initialFen="bqnbnrkr/pppppppp/8/8/8/8/PPPPPPPP/BQNBNRKR w KQkq - 0 1",
            moves="f4 f5",
            opening={},
        )
        record = parse_lichess_game(raw, "dylanette")
        assert record.variant is Variant.CHESS960
        assert record.initial_fen is not None
        assert record.eco is None


class TestDecisionNodes:
    def test_only_positions_where_the_player_moves_are_recorded(self) -> None:
        nodes = build_decision_nodes([game_record()])
        assert len(nodes) == 3
        assert [n.depth_ply for n in nodes] == [0, 2, 4]

    def test_black_gets_the_odd_plies(self) -> None:
        nodes = build_decision_nodes([game_record(player_is_white=False)])
        assert [n.depth_ply for n in nodes] == [1, 3, 5]

    def test_repeated_lines_collapse_to_one_node_per_position(self) -> None:
        games = [game_record(game_id=f"g{i}") for i in range(5)]
        nodes = build_decision_nodes(games)
        assert len(nodes) == 3
        assert all(n.game_count == 5 for n in nodes)

    def test_replies_are_counted_per_move(self) -> None:
        same = [game_record(game_id=f"a{i}") for i in range(3)]
        different = [
            game_record(game_id="b1", moves_san=tuple(["d4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]))
        ]
        nodes = build_decision_nodes(same + different)
        root = min(nodes, key=lambda n: n.depth_ply)
        assert root.game_count == 4
        assert root.replies == {"e2e4": 3, "d2d4": 1}
        assert root.distinct_replies == 2

    def test_transpositions_share_a_node(self) -> None:
        direct = game_record(game_id="a", moves_san=("d4", "Nf6", "c4", "e6", "Nc3"))
        transposed = game_record(game_id="b", moves_san=("c4", "Nf6", "d4", "e6", "Nc3"))
        nodes = build_decision_nodes([direct, transposed])
        merged = [n for n in nodes if n.depth_ply == 4]
        assert len(merged) == 1
        assert merged[0].game_count == 2
        assert merged[0].replies == {"b1c3": 2}

    def test_positions_before_the_transposition_stay_distinct(self) -> None:
        direct = game_record(game_id="a", moves_san=("d4", "Nf6", "c4", "e6", "Nc3"))
        transposed = game_record(game_id="b", moves_san=("c4", "Nf6", "d4", "e6", "Nc3"))
        nodes = build_decision_nodes([direct, transposed])
        assert len([n for n in nodes if n.depth_ply == 2]) == 2

    def test_max_ply_truncates_the_replay(self) -> None:
        nodes = build_decision_nodes([game_record()], max_ply=3)
        assert [n.depth_ply for n in nodes] == [0, 2]

    def test_illegal_notation_truncates_instead_of_raising(self) -> None:
        broken = game_record(moves_san=("e4", "e5", "Qxz9", "Nc6"))
        nodes = build_decision_nodes([broken])
        assert [n.depth_ply for n in nodes] == [0]

    def test_a_position_is_only_recorded_with_the_move_that_followed(self) -> None:
        trailing = game_record(moves_san=("e4", "e5", "Nf3"))
        nodes = build_decision_nodes([trailing])
        assert [n.depth_ply for n in nodes] == [0, 2]
        assert all(node.reply_total == 1 for node in nodes)

    def test_chess960_games_replay_from_their_own_start(self) -> None:
        record = game_record(
            variant=Variant.CHESS960,
            initial_fen="bqnbnrkr/pppppppp/8/8/8/8/PPPPPPPP/BQNBNRKR w KQkq - 0 1",
            moves_san=("f4", "f5", "Nf3"),
        )
        nodes = build_decision_nodes([record])
        assert len(nodes) == 2
        assert all(n.key.variant is Variant.CHESS960 for n in nodes)


class TestOpeningRecords:
    def test_grouped_by_eco_and_colour(self) -> None:
        games = [
            game_record(game_id="a", eco="C50", player_is_white=True),
            game_record(game_id="b", eco="C50", player_is_white=False),
        ]
        records = build_opening_records(games)
        assert {r.opening_id for r in records} == {"C50:white", "C50:black"}

    def test_score_is_the_mean_over_the_group(self) -> None:
        games = [
            game_record(game_id="a", score=1.0),
            game_record(game_id="b", score=0.0),
            game_record(game_id="c", score=0.5),
        ]
        record = build_opening_records(games)[0]
        assert record.games == 3
        assert record.score == pytest.approx(0.5)

    def test_population_score_defaults_to_even(self) -> None:
        assert build_opening_records([game_record()])[0].population_score == 0.5

    def test_supplied_population_scores_are_used(self) -> None:
        records = build_opening_records([game_record()], population_scores={"C50:white": 0.62})
        assert records[0].population_score == 0.62

    def test_games_without_an_opening_are_skipped(self) -> None:
        assert build_opening_records([game_record(eco=None)]) == []


class TestSettingsWiring:
    def test_selection_policy_reads_its_fields_from_settings(self) -> None:
        settings = Settings(
            selection_max_ply=12,
            selection_min_games=7,
            selection_min_divergence_games=4,
            selection_budget=99,
            selection_frontier_sample=5,
        )
        policy = settings.selection_policy()
        assert policy.max_ply == 12
        assert policy.min_games == 7
        assert policy.min_divergence_games == 4
        assert policy.budget == 99
        assert policy.frontier_sample == 5

    def test_perf_types_parse_into_a_tuple(self) -> None:
        assert Settings(ingest_perf_types="bullet, blitz ,rapid").perf_type_list() == (
            "bullet",
            "blitz",
            "rapid",
        )

    def test_empty_perf_types_means_every_speed(self) -> None:
        assert Settings(ingest_perf_types=None).perf_type_list() == ()
        assert Settings(ingest_perf_types="").perf_type_list() == ()


class _WindowedExport:
    def __init__(self, games: list[dict[str, Any]], cap: int) -> None:
        self.games = sorted(games, key=lambda g: -g["createdAt"])
        self.cap = cap
        self.calls: list[int | None] = []

    def export_user_games(
        self, username: str, *, until_ms: int | None = None, max_games: int | None = None, **_: Any
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(until_ms)
        pool = [g for g in self.games if until_ms is None or g["createdAt"] <= until_ms]
        yield from pool[: min(self.cap, max_games or self.cap)]


def stamped(index: int) -> dict[str, Any]:
    return {"id": f"g{index}", "createdAt": 1_700_000_000_000 - index * 1000}


class TestWindowedExport:
    def test_it_pages_past_the_provider_cap(self) -> None:
        source = _WindowedExport([stamped(i) for i in range(25)], cap=10)
        got = list(export_windows(cast(Any, source), "u", limit=None, rated=None, perf_types=()))
        assert len(got) == 25
        assert [g["id"] for g in got] == [f"g{i}" for i in range(25)]

    def test_the_overlapping_game_is_not_yielded_twice(self) -> None:
        source = _WindowedExport([stamped(i) for i in range(25)], cap=10)
        got = list(export_windows(cast(Any, source), "u", limit=None, rated=None, perf_types=()))
        assert len(got) == len({g["id"] for g in got})
        assert source.calls[0] is None
        assert source.calls[1] == stamped(9)["createdAt"]

    def test_a_limit_stops_the_walk_early(self) -> None:
        source = _WindowedExport([stamped(i) for i in range(25)], cap=10)
        got = list(export_windows(cast(Any, source), "u", limit=12, rated=None, perf_types=()))
        assert len(got) == 12

    def test_an_empty_history_asks_once(self) -> None:
        source = _WindowedExport([], cap=10)
        assert (
            list(export_windows(cast(Any, source), "u", limit=None, rated=None, perf_types=()))
            == []
        )
        assert len(source.calls) == 1

    def test_a_window_of_only_repeats_ends_the_walk(self) -> None:
        source = _WindowedExport([stamped(0)], cap=10)
        got = list(export_windows(cast(Any, source), "u", limit=None, rated=None, perf_types=()))
        assert len(got) == 1
        assert len(source.calls) == 2
