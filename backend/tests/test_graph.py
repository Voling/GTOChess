from __future__ import annotations

from typing import Any

import chess

from fiftymoves.domain.games import GameRecord, GameSource
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import Variant
from fiftymoves.ingest.graph import build_graph

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
