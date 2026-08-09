from __future__ import annotations

from typing import Any

import chess

from gtochess.domain.games import GameRecord, GameSource, Side
from gtochess.domain.graph import GraphEdge
from gtochess.domain.identity import position_key
from gtochess.domain.models import Variant
from gtochess.ingest.graph import build_graph, family_floors

ROOT = position_key(chess.Board()).digest


def game(game_id: str, moves: str, *, player_is_white: bool = True, **overrides: Any) -> GameRecord:
    base: dict[str, Any] = {
        "source": GameSource.LICHESS,
        "game_id": game_id,
        "played_at_ms": 0,
        "variant": Variant.STANDARD,
        "speed": "blitz",
        "rated": True,
        "player_is_white": player_is_white,
        "player_rating": 1800,
        "opponent_rating": 1800,
        "score": 0.5,
        "eco": "C50",
        "opening_name": "Italian Game",
        "opening_ply": 6,
        "initial_fen": None,
        "moves_san": tuple(moves.split()),
        "clocks_cs": (),
        "evals_cp": (),
        "initial_seconds": 180,
        "increment_seconds": 0,
    }
    base.update(overrides)
    return GameRecord(**base)


class TestStructure:
    def test_root_carries_every_game(self) -> None:
        graph = build_graph([game("a", "e4 e5"), game("b", "d4 d5")])
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.games == 2
        assert root.depth_ply == 0

    def test_shared_prefix_collapses_to_one_path(self) -> None:
        graph = build_graph([game(f"g{i}", "e4 e5 Nf3 Nc6") for i in range(5)])
        assert graph.node_count == 5
        assert all(edge.games == 5 for edge in graph.edges)

    def test_divergence_creates_siblings(self) -> None:
        graph = build_graph([game("a", "e4 e5 Nf3"), game("b", "e4 e5 Bc4")])
        after_e5 = next(n for n in graph.nodes if n.san_path == ("e4", "e5"))
        children = [e for e in graph.edges if e.parent == after_e5.digest]
        assert len(children) == 2
        assert {e.san for e in children} == {"Nf3", "Bc4"}

    def test_transpositions_reuse_a_node(self) -> None:
        graph = build_graph([game("a", "d4 Nf6 c4 e6"), game("b", "c4 Nf6 d4 e6")])
        converged = [n for n in graph.nodes if n.depth_ply == 4]
        assert len(converged) == 1
        assert converged[0].games == 2

    def test_max_ply_bounds_the_depth(self) -> None:
        graph = build_graph([game("a", "e4 e5 Nf3 Nc6 Bc4 Bc5")], max_ply=3)
        assert max(n.depth_ply for n in graph.nodes) == 3

    def test_edges_record_whose_move_it_was(self) -> None:
        graph = build_graph([game("a", "e4 e5", player_is_white=True)])
        first = next(e for e in graph.edges if e.san == "e4")
        second = next(e for e in graph.edges if e.san == "e5")
        assert first.by_player is True
        assert second.by_player is False

    def test_chess960_games_are_excluded(self) -> None:
        graph = build_graph(
            [
                game("a", "e4 e5"),
                game(
                    "b",
                    "f4 f5",
                    variant=Variant.CHESS960,
                    initial_fen="bqnbnrkr/pppppppp/8/8/8/8/PPPPPPPP/BQNBNRKR w KQkq - 0 1",
                ),
            ]
        )
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.games == 1


class TestSide:
    def test_white_only_keeps_the_games_played_as_white(self) -> None:
        games = [game("w", "e4 e5"), game("b", "d4 d5", player_is_white=False)]
        graph = build_graph(games, side=Side.WHITE)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.games == 1
        assert {e.san for e in graph.edges if e.parent == graph.root} == {"e4"}

    def test_black_only_keeps_the_games_played_as_black(self) -> None:
        games = [game("w", "e4 e5"), game("b", "d4 d5", player_is_white=False)]
        graph = build_graph(games, side=Side.BLACK)
        assert {e.san for e in graph.edges if e.parent == graph.root} == {"d4"}

    def test_both_keeps_everything(self) -> None:
        games = [game("w", "e4 e5"), game("b", "d4 d5", player_is_white=False)]
        graph = build_graph(games, side=Side.BOTH)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.games == 2

    def test_the_side_is_reported_back(self) -> None:
        assert build_graph([game("a", "e4")], side=Side.BLACK).side is Side.BLACK

    def test_as_white_the_player_moves_on_white_turns(self) -> None:
        graph = build_graph([game("a", "e4 e5")], side=Side.WHITE)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        after_e4 = next(n for n in graph.nodes if n.san_path == ("e4",))
        assert root.player_to_move is True
        assert after_e4.player_to_move is False

    def test_as_black_the_player_moves_on_black_turns(self) -> None:
        games = [game("a", "e4 e5", player_is_white=False)]
        graph = build_graph(games, side=Side.BLACK)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        after_e4 = next(n for n in graph.nodes if n.san_path == ("e4",))
        assert root.player_to_move is False
        assert after_e4.player_to_move is True

    def test_families_are_measured_within_the_selected_side(self) -> None:
        white = [game(f"w{i}", "e4 e5") for i in range(6)]
        black = [
            game(
                f"b{i}",
                "d4 d5",
                player_is_white=False,
                opening_name="Queen's Gambit Declined: Ragozin",
                eco="D38",
            )
            for i in range(6)
        ]
        graph = build_graph(white + black, side=Side.WHITE, family_min_games=4)
        assert [f.name for f in graph.families] == ["Italian Game"]


class TestPruning:
    def test_breadth_is_capped_per_node(self) -> None:
        games = [
            game("a", "e4"),
            game("b", "d4"),
            game("c", "c4"),
            game("d", "Nf3"),
            game("e", "g3"),
        ]
        graph = build_graph(games, max_children=2)
        assert len([e for e in graph.edges if e.parent == graph.root]) == 2
        assert graph.pruned_edges == 3

    def test_the_busiest_branches_survive_the_cap(self) -> None:
        games = [game(f"e{i}", "e4") for i in range(9)] + [game("d", "d4")]
        graph = build_graph(games, max_children=1)
        assert [e.san for e in graph.edges if e.parent == graph.root] == ["e4"]

    def test_min_volume_drops_one_off_lines(self) -> None:
        games = [game(f"e{i}", "e4 e5") for i in range(4)] + [game("odd", "e4 c5")]
        graph = build_graph(games, min_volume=2)
        assert {n.san_path for n in graph.nodes if n.depth_ply == 2} == {("e4", "e5")}

    def test_pruned_counts_are_reported_on_the_parent(self) -> None:
        games = [game("a", "e4"), game("b", "d4"), game("c", "c4")]
        graph = build_graph(games, max_children=1)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.pruned_children == 2
        assert root.pruned_child_games == 2

    def test_nothing_downstream_of_a_pruned_edge_survives(self) -> None:
        games = [game(f"e{i}", "e4 e5 Nf3") for i in range(4)] + [game("odd", "d4 d5 c4")]
        graph = build_graph(games, min_volume=2)
        assert all(not n.san_path or n.san_path[0] == "e4" for n in graph.nodes)

    def test_considered_edges_counts_before_pruning(self) -> None:
        games = [game("a", "e4"), game("b", "d4"), game("c", "c4")]
        graph = build_graph(games, max_children=1)
        assert graph.considered_edges == 3
        assert len(graph.edges) + graph.pruned_edges == graph.considered_edges


class TestIntensity:
    def test_max_games_is_the_busiest_node(self) -> None:
        graph = build_graph([game(f"g{i}", "e4 e5") for i in range(7)])
        assert graph.max_games == 7

    def test_empty_input_yields_a_lone_root(self) -> None:
        graph = build_graph([])
        assert graph.node_count == 1
        assert graph.edges == ()
        assert graph.max_games == 0


def named(game_id: str, moves: str, name: str, ply: int, **overrides: Any) -> GameRecord:
    return game(game_id, moves, opening_name=name, opening_ply=ply, **overrides)


def family_at(graph: Any, path: tuple[str, ...]) -> str | None:
    return next(n.family for n in graph.nodes if n.san_path == path)


class TestOpeningLabels:
    def sicilian_and_kings_pawn(self) -> list[GameRecord]:
        games = [named(f"s{i}", "e4 c5 Nf3 d6", "Sicilian Defense", 2) for i in range(9)]
        games += [named(f"k{i}", "e4 e5 Nf3 Nc6", "King's Pawn Game", 2) for i in range(4)]
        return games

    def test_the_empty_board_carries_no_opening(self) -> None:
        graph = build_graph(self.sicilian_and_kings_pawn(), family_min_games=2)
        root = next(n for n in graph.nodes if n.digest == graph.root)
        assert root.family is None
        assert root.family_share == 0.0

    def test_a_first_move_is_not_named_after_the_reply_it_usually_meets(self) -> None:
        graph = build_graph(self.sicilian_and_kings_pawn(), family_min_games=2)
        assert family_at(graph, ("e4",)) is None

    def test_the_family_lands_on_the_move_that_establishes_it(self) -> None:
        graph = build_graph(self.sicilian_and_kings_pawn(), family_min_games=2)
        assert family_at(graph, ("e4", "c5")) == "sicilian-defense"
        assert family_at(graph, ("e4", "e5")) == "king-s-pawn-game"

    def test_a_family_named_late_cannot_backdate_onto_earlier_moves(self) -> None:
        games = [named(f"r{i}", "e4 e5 Nf3 Nc6 Bb5 a6", "Ruy Lopez", 5) for i in range(9)]
        games += [named(f"i{i}", "e4 e5 Nf3 Nc6 Bc4 Bc5", "Italian Game", 5) for i in range(4)]
        graph = build_graph(games, max_ply=6, family_min_games=2)
        assert family_at(graph, ("e4", "e5")) is None
        assert family_at(graph, ("e4", "e5", "Nf3", "Nc6")) is None
        assert family_at(graph, ("e4", "e5", "Nf3", "Nc6", "Bb5")) == "ruy-lopez"

    def test_an_opening_settled_on_the_first_move_is_named_there(self) -> None:
        games = [named(f"b{i}", "b3 e5 Bb2", "Nimzo-Larsen Attack", 1) for i in range(6)]
        graph = build_graph(games, family_min_games=2)
        assert family_at(graph, ("b3",)) == "nimzo-larsen-attack"

    def test_the_floor_is_the_earliest_ply_the_family_is_ever_named(self) -> None:
        floors = family_floors(
            [
                named("a", "e4 c5", "Sicilian Defense: Najdorf", 8),
                named("b", "e4 c5", "Sicilian Defense", 2),
                named("c", "e4 e5 Nf3 Nc6 Bb5", "Ruy Lopez", 5),
            ]
        )
        assert floors["sicilian-defense"] == 2
        assert floors["ruy-lopez"] == 5

    def test_a_game_with_no_opening_ply_does_not_set_a_floor(self) -> None:
        assert family_floors([named("a", "e4", "Sicilian Defense", None)]) == {}


class TestResults:
    def test_an_edge_carries_the_result_of_every_game_through_it(self) -> None:
        graph = build_graph(
            [
                game("a", "e4 e5", score=1.0),
                game("b", "e4 e5", score=0.0),
                game("c", "e4 e5", score=0.5),
                game("d", "e4 e5", score=1.0),
            ]
        )
        first = next(e for e in graph.edges if e.san == "e4")
        assert (first.wins, first.draws, first.losses) == (2, 1, 1)
        assert first.score == 0.625

    def test_the_result_follows_the_player_not_white(self) -> None:
        graph = build_graph([game("a", "e4 e5 Nf3", score=1.0, player_is_white=False)])
        reply = next(e for e in graph.edges if e.san == "e5")
        assert reply.by_player
        assert (reply.wins, reply.draws, reply.losses) == (1, 0, 0)

    def test_an_edge_with_no_games_scores_level_rather_than_zero(self) -> None:
        blank = GraphEdge(parent="p", child="c", uci="e2e4", san="e4", games=0, by_player=True)
        assert blank.decided == 0
        assert blank.score == 0.5
