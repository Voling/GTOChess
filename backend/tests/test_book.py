from __future__ import annotations

import chess

from fiftymoves.analysis.book import price_position, score_openings
from fiftymoves.domain.book import MoveCost
from fiftymoves.domain.games import Side
from fiftymoves.domain.graph import GraphEdge, GraphNode, RepertoireGraph
from fiftymoves.domain.models import Variant
from fiftymoves.engine.reference import ReferenceEngine


def node(digest: str, ply: int, family: str, games: int) -> GraphNode:
    return GraphNode(
        digest=digest,
        epd="8/8/8/8/8/8/8/8 w - -",
        variant=Variant.STANDARD,
        depth_ply=ply,
        games=games,
        player_to_move=True,
        san_path=(),
        pruned_children=0,
        pruned_child_games=0,
        family=family,
        family_share=1.0,
        score=0.5,
    )


def edge(parent: str, child: str, uci: str, games: int) -> GraphEdge:
    return GraphEdge(parent=parent, child=child, uci=uci, san=uci, games=games, by_player=True)


def cost(digest: str, losses: dict[str, int]) -> MoveCost:
    return MoveCost(
        digest=digest,
        epd="8/8/8/8/8/8/8/8 w - -",
        depth=20,
        best_uci="a1a2",
        best_san="a2",
        best_cp=0,
        losses=losses,
    )


def graph_of(nodes: list[GraphNode], edges: list[GraphEdge]) -> RepertoireGraph:
    return RepertoireGraph(
        root=nodes[0].digest,
        side=Side.WHITE,
        nodes=tuple(nodes),
        edges=tuple(edges),
        families=(),
        max_games=max(n.games for n in nodes),
        pruned_edges=0,
        considered_edges=len(edges),
    )


class TestBookDepth:
    def test_depth_stops_at_the_first_move_that_breaks_the_band(self) -> None:
        nodes = [node(f"n{m}", (m - 1) * 2, "Kings Pawn", 50) for m in (1, 2, 3, 4)]
        edges = [edge(f"n{m}", f"n{m + 1}", "a1a2", 50) for m in (1, 2, 3)]
        edges.append(edge("n4", "n5", "a1a2", 50))
        costs = {
            "n1": cost("n1", {"a1a2": 0}),
            "n2": cost("n2", {"a1a2": 10}),
            "n3": cost("n3", {"a1a2": 140}),
            "n4": cost("n4", {"a1a2": 0}),
        }
        phase = score_openings(graph_of(nodes, edges), costs, prior_games=0)
        assert phase.families[0].raw_depth == 2

    def test_a_gap_in_move_numbers_ends_the_book(self) -> None:
        nodes = [node("n1", 0, "Kings Pawn", 50), node("n3", 4, "Kings Pawn", 50)]
        edges = [edge("n1", "n2", "a1a2", 50), edge("n3", "n4", "a1a2", 50)]
        costs = {"n1": cost("n1", {"a1a2": 0}), "n3": cost("n3", {"a1a2": 0})}
        phase = score_openings(graph_of(nodes, edges), costs, prior_games=0)
        assert phase.families[0].raw_depth == 1

    def test_a_family_is_counted_from_where_it_first_appears(self) -> None:
        # Only the opening answered most often owns move 1; the rest become
        # identifiable later and must not be scored as if they had failed there.
        nodes = [node("n3", 4, "Caro-Kann", 60), node("n4", 6, "Caro-Kann", 60)]
        edges = [edge("n3", "n4", "a1a2", 60), edge("n4", "n5", "a1a2", 60)]
        costs = {"n3": cost("n3", {"a1a2": 0}), "n4": cost("n4", {"a1a2": 0})}
        phase = score_openings(graph_of(nodes, edges), costs, prior_games=0)
        assert phase.families[0].raw_depth == 4

    def test_a_thin_family_cannot_claim_a_deep_book_on_six_games(self) -> None:
        nodes = [
            node("a1", 0, "Busy", 400),
            node("b1", 0, "Thin", 6),
            node("b2", 2, "Thin", 6),
            node("b3", 4, "Thin", 6),
        ]
        edges = [
            edge("a1", "a2", "a1a2", 400),
            edge("b1", "b2", "a1a2", 6),
            edge("b2", "b3", "a1a2", 6),
            edge("b3", "b4", "a1a2", 6),
        ]
        costs = {d: cost(d, {"a1a2": 0}) for d in ("a1", "b1", "b2", "b3")}
        costs["a1"] = cost("a1", {"a1a2": 200})
        phase = score_openings(graph_of(nodes, edges), costs, prior_games=25, min_games=4)
        thin = next(f for f in phase.families if f.key == "Thin")
        busy = next(f for f in phase.families if f.key == "Busy")
        assert thin.raw_depth == 3
        assert busy.raw_depth == 0
        # Six games is not evidence of three moves of book, so most of the claim goes.
        assert thin.book_depth < thin.raw_depth / 2


class TestRepetition:
    def test_one_line_repeated_cannot_drown_out_a_flaw_beside_it(self) -> None:
        nodes = [node("root", 0, "Kings Pawn", 1010), node("rare", 0, "Kings Pawn", 10)]
        edges = [edge("root", "x", "a1a2", 1000), edge("rare", "y", "b1b2", 10)]
        costs = {"root": cost("root", {"a1a2": 0}), "rare": cost("rare", {"b1b2": 400})}
        phase = score_openings(graph_of(nodes, edges), costs, prior_games=0)
        # Counting games would put the flaw at 1% of the weight and hide it.
        assert phase.families[0].by_move[0].mean_loss_cp > 20

    def test_a_position_the_engine_never_priced_is_skipped_not_guessed(self) -> None:
        nodes = [node("n1", 0, "Kings Pawn", 50), node("n2", 2, "Kings Pawn", 50)]
        edges = [edge("n1", "n2", "a1a2", 50), edge("n2", "n3", "a1a2", 50)]
        phase = score_openings(graph_of(nodes, edges), {"n1": cost("n1", {"a1a2": 0})})
        assert phase.positions_scored == 1


class TestPricing:
    def test_one_search_prices_every_reply(self) -> None:
        board = chess.Board()
        replies = ["e2e4", "d2d4", "g1f3"]
        priced = price_position(ReferenceEngine(), board, replies, digest="d", depth=4)
        assert all(uci in priced.losses for uci in replies)
        assert priced.losses[priced.best_uci] == 0
        assert all(loss >= 0 for loss in priced.losses.values())
