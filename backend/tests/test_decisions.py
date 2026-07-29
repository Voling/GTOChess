from __future__ import annotations

import chess
import pytest

from fiftymoves.analysis.decisions import (
    evaluate_choice,
    reply_count_after,
    targets_opponent_zone,
)
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import EngineLine, EngineReport

START = chess.Board()


def make_report(board: chess.Board, scored_moves: list[tuple[str, int]]) -> EngineReport:
    lines = []
    for rank, (uci, score_cp) in enumerate(scored_moves, start=1):
        move = chess.Move.from_uci(uci)
        lines.append(
            EngineLine(
                rank=rank,
                move_san=board.san(move),
                move_uci=uci,
                pv_san=[board.san(move)],
                pv_uci=[uci],
                score_cp=score_cp,
                depth=10,
            )
        )
    return EngineReport(key=position_key(board), lines=lines, depth=10, engine_id="fixture")


class TestEvaluateChoice:
    def test_rejects_an_illegal_move(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30)])
        with pytest.raises(ValueError, match="not legal"):
            evaluate_choice(board, chess.Move.from_uci("e2e5"), report, game_id="g", ply=0)

    def test_eval_loss_is_zero_for_the_best_move(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25), ("g1f3", 20)])
        decision = evaluate_choice(board, chess.Move.from_uci("e2e4"), report, game_id="g", ply=0)
        assert decision.eval_loss_cp == 0
        assert decision.position_eval_cp == 30

    def test_eval_loss_uses_the_movers_point_of_view_for_black(self) -> None:
        board = chess.Board()
        board.push_san("e4")
        report = make_report(board, [("e7e5", -30), ("c7c5", -10)])
        best = evaluate_choice(board, chess.Move.from_uci("e7e5"), report, game_id="g", ply=1)
        worse = evaluate_choice(board, chess.Move.from_uci("c7c5"), report, game_id="g", ply=1)
        assert best.position_eval_cp == 30
        assert best.eval_loss_cp == 0
        assert worse.eval_loss_cp == 20

    def test_eval_loss_is_unknown_when_the_move_is_outside_the_report(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25)])
        decision = evaluate_choice(board, chess.Move.from_uci("a2a3"), report, game_id="g", ply=0)
        assert decision.eval_loss_cp is None

    def test_supplied_score_fills_in_a_move_outside_the_report(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25)])
        decision = evaluate_choice(
            board,
            chess.Move.from_uci("a2a3"),
            report,
            game_id="g",
            ply=0,
            played_score_cp=-15,
        )
        assert decision.eval_loss_cp == 45

    def test_single_playable_move_is_not_a_decision_point(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 300), ("d2d4", 10), ("g1f3", 5)])
        decision = evaluate_choice(board, chess.Move.from_uci("e2e4"), report, game_id="g", ply=0)
        assert decision.is_decision_point is False

    def test_several_close_moves_make_a_decision_point(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25), ("g1f3", 20)])
        decision = evaluate_choice(board, chess.Move.from_uci("d2d4"), report, game_id="g", ply=0)
        assert decision.is_decision_point is True
        assert len(decision.alternative_reply_counts) == 2

    def test_alternatives_exclude_the_played_move(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25)])
        decision = evaluate_choice(board, chess.Move.from_uci("e2e4"), report, game_id="g", ply=0)
        assert len(decision.alternative_reply_counts) == 1
        assert len(decision.alternative_material_gains) == 1

    def test_alternatives_outside_the_playable_band_are_dropped(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30), ("d2d4", 25), ("h2h4", -200)])
        decision = evaluate_choice(board, chess.Move.from_uci("e2e4"), report, game_id="g", ply=0)
        assert decision.alternative_reply_counts == (
            reply_count_after(board, chess.Move.from_uci("d2d4")),
        )


class TestMaterialGain:
    def test_plain_capture_is_valued(self) -> None:
        board = chess.Board("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1")
        report = make_report(board, [("e4d5", 900), ("e1d1", 0)])
        decision = evaluate_choice(board, chess.Move.from_uci("e4d5"), report, game_id="g", ply=0)
        assert decision.played_material_gain_cp == 900
        assert decision.played_is_capture is True

    def test_en_passant_counts_as_a_pawn(self) -> None:
        board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
        report = make_report(board, [("e5d6", 100), ("e1d1", 0)])
        decision = evaluate_choice(board, chess.Move.from_uci("e5d6"), report, game_id="g", ply=0)
        assert decision.played_material_gain_cp == 100
        assert decision.played_is_capture is True

    def test_quiet_move_gains_nothing(self) -> None:
        board = chess.Board()
        report = make_report(board, [("e2e4", 30)])
        decision = evaluate_choice(board, chess.Move.from_uci("e2e4"), report, game_id="g", ply=0)
        assert decision.played_material_gain_cp == 0
        assert decision.played_is_capture is False


class TestZoneTargeting:
    def test_zone_is_relative_to_the_mover(self) -> None:
        assert targets_opponent_zone(chess.Move.from_uci("d1d7"), chess.WHITE) is True
        assert targets_opponent_zone(chess.Move.from_uci("d8d2"), chess.BLACK) is True
        assert targets_opponent_zone(chess.Move.from_uci("e2e4"), chess.WHITE) is False

    def test_decision_records_both_played_and_engine_choice(self) -> None:
        board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
        report = make_report(board, [("d1d7", 500), ("d1d2", 480)])
        decision = evaluate_choice(board, chess.Move.from_uci("d1d2"), report, game_id="g", ply=0)
        assert decision.best_targets_opponent_zone is True
        assert decision.played_targets_opponent_zone is False


class TestReplyCount:
    def test_does_not_mutate_the_board(self) -> None:
        board = chess.Board()
        before = board.fen()
        reply_count_after(board, chess.Move.from_uci("e2e4"))
        assert board.fen() == before

    def test_forcing_moves_leave_fewer_replies(self) -> None:
        board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
        checking = reply_count_after(board, chess.Move.from_uci("d1d8"))
        quiet = reply_count_after(board, chess.Move.from_uci("d1a1"))
        assert checking < quiet
