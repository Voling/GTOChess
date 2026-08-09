from __future__ import annotations

from pathlib import Path

from gtochess.analysis.sweep import depth_for, plan_sweep
from gtochess.domain.book import PositionLosses
from gtochess.domain.games import Side
from gtochess.domain.graph import GraphEdge, GraphNode, RepertoireGraph
from gtochess.domain.models import Variant
from gtochess.ingest.loss_store import LossStore

EPD = "8/8/8/8/8/8/8/8 w - -"


def node(digest: str, games: int) -> GraphNode:
    return GraphNode(
        digest=digest,
        epd=EPD,
        variant=Variant.STANDARD,
        depth_ply=0,
        games=games,
        player_to_move=True,
        san_path=(),
        pruned_children=0,
        pruned_child_games=0,
        family=None,
        family_share=0.0,
        score=0.5,
    )


def edge(parent: str, uci: str, *, by_player: bool = True) -> GraphEdge:
    return GraphEdge(
        parent=parent, child=f"{parent}c", uci=uci, san=uci, games=5, by_player=by_player
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


def held(digest: str, depth: int) -> PositionLosses:
    return PositionLosses(
        digest=digest,
        epd=EPD,
        depth=depth,
        best_uci="a1a2",
        best_san="a2",
        best_cp=0,
        losses={"a1a2": 0},
    )


class TestTiers:
    def test_every_position_gets_the_same_depth_by_default(self) -> None:
        assert depth_for(400) == 20
        assert depth_for(40) == 20
        assert depth_for(4) == 20

    def test_a_position_seen_twice_is_not_worth_an_engine(self) -> None:
        assert depth_for(2) is None

    def test_raising_the_ceiling_lets_volume_buy_depth_again(self) -> None:
        assert depth_for(40, 28) == 28
        assert depth_for(12, 28) == 24
        assert depth_for(4, 28) == 20

    def test_the_ceiling_never_raises_a_tier(self) -> None:
        assert depth_for(4, 28) == 20


class TestPlanning:
    def test_it_skips_what_is_already_measured_deep_enough(self, tmp_path: Path) -> None:
        store = LossStore(tmp_path)
        store.extend([held("busy", 20)])
        graph = graph_of([node("busy", 40)], [edge("busy", "a1a2")])
        assert plan_sweep(graph, store) == []

    def test_work_from_a_deeper_sweep_is_not_thrown_away(self, tmp_path: Path) -> None:
        store = LossStore(tmp_path)
        store.extend([held("busy", 28)])
        graph = graph_of([node("busy", 40)], [edge("busy", "a1a2")])
        assert plan_sweep(graph, store) == []

    def test_a_position_held_too_shallow_comes_back(self, tmp_path: Path) -> None:
        store = LossStore(tmp_path)
        store.extend([held("busy", 14)])
        graph = graph_of([node("busy", 40)], [edge("busy", "a1a2")])
        assert [i.depth for i in plan_sweep(graph, store)] == [20]

    def test_the_busiest_positions_are_measured_first(self, tmp_path: Path) -> None:
        nodes = [node("a", 5), node("b", 90), node("c", 30)]
        edges = [edge("a", "a1a2"), edge("b", "a1a2"), edge("c", "a1a2")]
        plan = plan_sweep(graph_of(nodes, edges), LossStore(tmp_path))
        assert [i.digest for i in plan] == ["b", "c", "a"]

    def test_moves_the_opponent_played_are_not_the_players_to_answer_for(
        self, tmp_path: Path
    ) -> None:
        nodes = [node("a", 40)]
        edges = [edge("a", "a1a2", by_player=False)]
        assert plan_sweep(graph_of(nodes, edges), LossStore(tmp_path)) == []

    def test_every_reply_from_a_position_is_carried_together(self, tmp_path: Path) -> None:
        nodes = [node("a", 40)]
        edges = [edge("a", "a1a2"), edge("a", "b1b2")]
        plan = plan_sweep(graph_of(nodes, edges), LossStore(tmp_path))
        assert len(plan) == 1
        assert set(plan[0].replies) == {"a1a2", "b1b2"}

    def test_the_limit_keeps_the_busiest_rather_than_the_first_seen(self, tmp_path: Path) -> None:
        nodes = [node("a", 5), node("b", 90), node("c", 30)]
        edges = [edge("a", "a1a2"), edge("b", "a1a2"), edge("c", "a1a2")]
        plan = plan_sweep(graph_of(nodes, edges), LossStore(tmp_path), limit=2)
        assert [i.digest for i in plan] == ["b", "c"]
