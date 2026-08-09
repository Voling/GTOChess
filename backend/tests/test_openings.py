from __future__ import annotations

from typing import Any

from gtochess.analysis.openings import (
    build_families,
    dominant,
    family_key,
    family_name,
    forcing_share,
)
from gtochess.domain.games import GameRecord, GameSource
from gtochess.domain.models import Variant


def game(game_id: str, *, moves: str = "e4 e6 d4 d5", **overrides: Any) -> GameRecord:
    base: dict[str, Any] = {
        "source": GameSource.LICHESS,
        "game_id": game_id,
        "played_at_ms": 0,
        "variant": Variant.STANDARD,
        "speed": "blitz",
        "rated": True,
        "player_is_white": True,
        "player_rating": 1800,
        "opponent_rating": 1800,
        "score": 0.5,
        "eco": "C00",
        "opening_name": "French Defense: Steinitz Variation",
        "opening_ply": 4,
        "initial_fen": None,
        "moves_san": tuple(moves.split()),
        "clocks_cs": (),
        "evals_cp": (),
        "initial_seconds": 180,
        "increment_seconds": 0,
    }
    base.update(overrides)
    return GameRecord(**base)


class TestFamilyNaming:
    def test_the_part_before_the_colon_is_the_family(self) -> None:
        assert family_name("Queen's Gambit Declined: Ragozin Defense") == "Queen's Gambit Declined"

    def test_variations_of_one_opening_share_a_key(self) -> None:
        ragozin = family_key("Queen's Gambit Declined: Ragozin Defense")
        orthodox = family_key("Queen's Gambit Declined: Orthodox Defense")
        assert ragozin == orthodox == "queen-s-gambit-declined"

    def test_an_opening_without_a_variation_keeps_its_whole_name(self) -> None:
        assert family_name("Italian Game") == "Italian Game"

    def test_a_missing_name_is_unclassified(self) -> None:
        assert family_key(None) == "unclassified"


class TestForcingShare:
    def test_captures_and_checks_count_as_forcing(self) -> None:
        assert forcing_share(["e4", "exd5", "Bb5+", "Nf3"], 4) == 0.5

    def test_the_window_bounds_the_measurement(self) -> None:
        assert forcing_share(["e4", "e5", "Nf3", "Nxe5"], 2) == 0.0

    def test_an_empty_game_is_not_forcing(self) -> None:
        assert forcing_share([], 16) == 0.0


class TestFamilies:
    def test_families_below_the_floor_are_dropped(self) -> None:
        families = build_families([game(f"g{i}") for i in range(3)], min_games=4)
        assert families == []

    def test_slots_go_to_the_busiest_families(self) -> None:
        games = [game(f"f{i}") for i in range(10)] + [
            game(f"s{i}", opening_name="Sicilian Defense: Najdorf", eco="B90") for i in range(5)
        ]
        families = build_families(games, min_games=4, slots=1)
        assert [f.slot for f in families] == [0, -1]
        assert families[0].name == "French Defense"

    def test_slot_assignment_ignores_families_past_the_cap(self) -> None:
        games = [game(f"f{i}") for i in range(10)] + [
            game(f"s{i}", opening_name="Sicilian Defense", eco="B20") for i in range(5)
        ]
        families = build_families(games, min_games=4, slots=3)
        assert [f.slot for f in families] == [0, 1]

    def test_a_forcing_repertoire_scores_sharper(self) -> None:
        quiet = [game(f"q{i}", moves="d4 d5 Nf3 Nf6 e3 e6 Be2 Be7") for i in range(8)]
        sharp = [
            game(
                f"s{i}",
                moves="e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6",
                opening_name="Sicilian Defense",
                eco="B50",
            )
            for i in range(8)
        ]
        families = {f.name: f for f in build_families(quiet + sharp, min_games=4)}
        assert families["Sicilian Defense"].sharpness > families["French Defense"].sharpness

    def test_eco_range_spans_the_observed_codes(self) -> None:
        games = [game(f"g{i}", eco="C00") for i in range(4)] + [
            game(f"h{i}", eco="C19") for i in range(4)
        ]
        family = build_families(games, min_games=4)[0]
        assert family.eco_range == "C00-C19"

    def test_score_is_shrunk_toward_the_population(self) -> None:
        winners = [game(f"w{i}", score=1.0) for i in range(5)]
        losers = [
            game(f"l{i}", score=0.0, opening_name="Sicilian Defense", eco="B20") for i in range(5)
        ]
        built = build_families(winners + losers, min_games=4, prior_games=10)
        families = {f.name: f for f in built}
        assert 0.5 < families["French Defense"].score < 1.0
        assert 0.0 < families["Sicilian Defense"].score < 0.5

    def test_colour_split_is_recorded(self) -> None:
        games = [game(f"w{i}") for i in range(4)] + [
            game(f"b{i}", player_is_white=False) for i in range(4)
        ]
        assert build_families(games, min_games=4)[0].as_white == 4


class TestDominant:
    def test_the_most_common_key_wins(self) -> None:
        assert dominant({"french": 7, "sicilian": 3}) == ("french", 0.7)

    def test_no_counts_means_no_family(self) -> None:
        assert dominant({}) == (None, 0.0)
