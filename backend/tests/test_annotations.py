from __future__ import annotations

import chess

from fiftymoves.analysis.annotations import annotate_graph, annotate_position, classify
from fiftymoves.domain.annotations import MoveQuality
from fiftymoves.domain.games import Side
from fiftymoves.domain.graph import GraphEdge, GraphNode, RepertoireGraph
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import EngineLine, EngineReport, Variant
from fiftymoves.engine.reference import ReferenceEngine


def line(rank: int, san: str, uci: str, score_cp: int) -> EngineLine:
    return EngineLine(
        rank=rank,
        move_san=san,
        move_uci=uci,
        pv_san=[san],
        pv_uci=[uci],
        score_cp=score_cp,
        depth=12,
    )


def report_for(board: chess.Board, lines: list[EngineLine]) -> EngineReport:
    return EngineReport(key=position_key(board), lines=lines, depth=12, engine_id="stub")


def edge(parent: str, san: str, uci: str, *, games: int = 5, by_player: bool = True) -> GraphEdge:
    return GraphEdge(parent=parent, child="c", uci=uci, san=san, games=games, by_player=by_player)


class TestClassify:
    def test_a_large_loss_is_a_blunder(self) -> None:
        assert classify(400) is MoveQuality.BLUNDER

    def test_a_moderate_loss_is_a_mistake(self) -> None:
        assert classify(200) is MoveQuality.MISTAKE

    def test_a_small_loss_is_dubious(self) -> None:
        assert classify(100) is MoveQuality.DUBIOUS

    def test_a_negligible_loss_is_sound(self) -> None:
        assert classify(10) is MoveQuality.SOUND

    def test_the_boundaries_are_inclusive(self) -> None:
        assert classify(90) is MoveQuality.DUBIOUS
        assert classify(160) is MoveQuality.MISTAKE
        assert classify(300) is MoveQuality.BLUNDER

    def test_a_sound_opening_choice_is_not_flagged(self) -> None:
        # Engine taste between playable opening moves must not read as an error.
        assert classify(53) is MoveQuality.SOUND
        assert classify(80) is MoveQuality.SOUND

    def test_thresholds_are_configurable(self) -> None:
        assert classify(100, dubious_cp=150) is MoveQuality.SOUND

    def test_sound_renders_as_no_symbol(self) -> None:
        assert MoveQuality.SOUND.symbol == ""
        assert MoveQuality.MISTAKE.symbol == "?"
        assert MoveQuality.BLUNDER.symbol == "??"


class TestWhiteToMove:
    def test_a_worse_move_loses_centipawns(self) -> None:
        board = chess.Board()
        report = report_for(board, [line(1, "e4", "e2e4", 30), line(2, "a3", "a2a3", -150)])
        found = annotate_position(board, [edge("p", "a3", "a2a3")], report)
        assert found[0].loss_cp == 180
        assert found[0].quality is MoveQuality.MISTAKE
        assert found[0].best_san == "e4"

    def test_the_engines_own_choice_costs_nothing(self) -> None:
        board = chess.Board()
        report = report_for(board, [line(1, "e4", "e2e4", 30), line(2, "d4", "d2d4", 25)])
        found = annotate_position(board, [edge("p", "e4", "e2e4")], report)
        assert found[0].loss_cp == 0
        assert found[0].quality is MoveQuality.SOUND


class TestBlackToMove:
    def test_loss_is_read_from_blacks_side(self) -> None:
        board = chess.Board()
        board.push_san("e4")
        # White's point of view: -20 is good for Black, +160 is terrible for Black.
        report = report_for(board, [line(1, "c5", "c7c5", -20), line(2, "f6", "f7f6", 160)])
        found = annotate_position(board, [edge("p", "f6", "f7f6")], report)
        assert found[0].loss_cp == 180
        assert found[0].quality is MoveQuality.MISTAKE

    def test_blacks_best_move_costs_nothing(self) -> None:
        board = chess.Board()
        board.push_san("e4")
        report = report_for(board, [line(1, "c5", "c7c5", -20), line(2, "e5", "e7e5", 10)])
        found = annotate_position(board, [edge("p", "c5", "c7c5")], report)
        assert found[0].loss_cp == 0


class TestMovesOutsideTheSearch:
    def test_an_unlisted_move_is_evaluated_directly(self) -> None:
        board = chess.Board()
        report = report_for(board, [line(1, "e4", "e2e4", 30)])
        found = annotate_position(
            board,
            [edge("p", "a3", "a2a3")],
            report,
            evaluate_child=lambda _: -400,
        )
        assert found[0].loss_cp == 430
        assert found[0].quality is MoveQuality.BLUNDER

    def test_without_a_probe_an_unlisted_move_is_skipped(self) -> None:
        board = chess.Board()
        report = report_for(board, [line(1, "e4", "e2e4", 30)])
        assert annotate_position(board, [edge("p", "a3", "a2a3")], report) == []

    def test_an_illegal_move_is_skipped(self) -> None:
        board = chess.Board()
        report = report_for(board, [line(1, "e4", "e2e4", 30)])
        found = annotate_position(
            board, [edge("p", "Qh5", "d1h5")], report, evaluate_child=lambda _: 0
        )
        assert found == []

    def test_an_empty_report_yields_nothing(self) -> None:
        board = chess.Board()
        assert annotate_position(board, [edge("p", "e4", "e2e4")], report_for(board, [])) == []


def graph_with_one_move() -> RepertoireGraph:
    start = chess.Board()
    root = position_key(start)
    child_board = start.copy()
    child_board.push_san("a3")
    child = position_key(child_board)

    def node(key: object, depth: int, path: tuple[str, ...]) -> GraphNode:
        return GraphNode(
            digest=key.digest,  # type: ignore[attr-defined]
            epd=key.epd,  # type: ignore[attr-defined]
            variant=Variant.STANDARD,
            depth_ply=depth,
            games=6,
            player_to_move=depth == 0,
            san_path=path,
            pruned_children=0,
            pruned_child_games=0,
            family=None,
            family_share=0.0,
            score=0.5,
        )

    return RepertoireGraph(
        root=root.digest,
        side=Side.WHITE,
        nodes=(node(root, 0, ()), node(child, 1, ("a3",))),
        edges=(
            GraphEdge(
                parent=root.digest,
                child=child.digest,
                uci="a2a3",
                san="a3",
                games=6,
                by_player=True,
            ),
        ),
        families=(),
        max_games=6,
        pruned_edges=0,
        considered_edges=1,
    )


class TestAnnotateGraph:
    def test_it_annotates_the_players_moves(self) -> None:
        result = annotate_graph(
            ReferenceEngine(), graph_with_one_move(), username="d", shape="s", depth=2
        )
        assert len(result.annotations) == 1
        assert result.annotations[0].san == "a3"
        assert result.positions_searched >= 1

    def test_opponent_moves_are_left_alone_by_default(self) -> None:
        graph = graph_with_one_move()
        theirs = graph.model_copy(
            update={"edges": (graph.edges[0].model_copy(update={"by_player": False}),)}
        )
        result = annotate_graph(ReferenceEngine(), theirs, username="d", shape="s", depth=2)
        assert result.annotations == ()

    def test_the_volume_floor_skips_rare_moves(self) -> None:
        result = annotate_graph(
            ReferenceEngine(), graph_with_one_move(), username="d", shape="s", depth=2, min_games=50
        )
        assert result.edges_considered == 0

    def test_a_budget_truncates_and_says_so(self) -> None:
        result = annotate_graph(
            ReferenceEngine(), graph_with_one_move(), username="d", shape="s", depth=2, budget=0
        )
        assert result.truncated is True
        assert result.annotations == ()

    def test_findings_are_ranked_by_cost(self) -> None:
        result = annotate_graph(
            ReferenceEngine(), graph_with_one_move(), username="d", shape="s", depth=2
        )
        losses = [a.loss_cp for a in result.annotations]
        assert losses == sorted(losses, reverse=True)
