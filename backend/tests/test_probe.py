from __future__ import annotations

import chess
import pytest

from fiftymoves.engine.reference import ReferenceEngine
from fiftymoves.llm.tools import BEST_REPLIES, EVALUATE_LINE, EngineProbe, ProbeLimit

MATE_IN_ONE = "7k/6pp/8/8/8/8/1p6/R5K1 w - - 0 1"


def probe(fen: str | None = None, **kwargs: int) -> EngineProbe:
    board = chess.Board(fen) if fen else chess.Board()
    return EngineProbe(ReferenceEngine(), board, depth=2, **kwargs)


class TestEvaluateLine:
    def test_it_answers_about_the_position_reached(self) -> None:
        result = probe().evaluate_line(["e4", "e5"])
        assert "e4 e5" in result["summary"]
        assert result["evidence_id"] == "probe1"

    def test_an_empty_line_evaluates_the_position_itself(self) -> None:
        assert probe().evaluate_line([])["evidence_id"] == "probe1"

    def test_it_reports_the_score_from_the_movers_side(self) -> None:
        result = probe().evaluate_line(["e4"])
        assert isinstance(result["score_cp_for_mover"], int)

    def test_a_finished_game_is_reported_not_searched(self) -> None:
        result = probe(MATE_IN_ONE).evaluate_line(["Ra8"])
        assert "game is over" in result["summary"]


class TestBestReplies:
    def test_it_lists_moves_with_evaluations(self) -> None:
        result = probe().best_replies([], 3)
        assert len(result["moves"]) <= 3
        assert all("san" in m for m in result["moves"])

    def test_the_count_is_clamped(self) -> None:
        assert len(probe().best_replies([], 99)["moves"]) <= 5

    def test_a_count_below_one_still_returns_something(self) -> None:
        assert len(probe().best_replies([], 0)["moves"]) >= 1


class TestBounds:
    def test_an_illegal_move_is_refused_with_a_reason(self) -> None:
        with pytest.raises(ProbeLimit, match="not legal"):
            probe().evaluate_line(["e4", "e4"])

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(ProbeLimit):
            probe().evaluate_line(["banana"])

    def test_a_line_beyond_the_limit_is_refused(self) -> None:
        deep = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]
        with pytest.raises(ProbeLimit, match="at most"):
            probe(max_moves=6).evaluate_line(deep)

    def test_the_engine_stops_answering_after_the_budget(self) -> None:
        p = probe(max_calls=2)
        p.evaluate_line(["e4"])
        p.evaluate_line(["d4"])
        with pytest.raises(ProbeLimit, match="already answered"):
            p.evaluate_line(["c4"])

    def test_a_refused_call_does_not_spend_the_budget_twice(self) -> None:
        p = probe(max_calls=3)
        with pytest.raises(ProbeLimit):
            p.evaluate_line(["banana"])
        assert p.calls == 1

    def test_an_unknown_tool_is_refused(self) -> None:
        with pytest.raises(ProbeLimit, match="no such tool"):
            probe().dispatch("drop_tables", {})


class TestEvidence:
    def test_every_answer_becomes_citable_evidence(self) -> None:
        p = probe()
        first = p.evaluate_line(["e4"])["evidence_id"]
        second = p.best_replies(["e4"], 2)["evidence_id"]
        assert [e.id for e in p.evidence] == [first, second] == ["probe1", "probe2"]

    def test_the_evidence_repeats_what_the_model_was_told(self) -> None:
        p = probe()
        result = p.evaluate_line(["e4"])
        assert p.evidence[0].statement == result["summary"]

    def test_a_refused_call_records_no_evidence(self) -> None:
        p = probe()
        with pytest.raises(ProbeLimit):
            p.evaluate_line(["banana"])
        assert p.evidence == []


class TestDispatch:
    def test_it_routes_both_tools(self) -> None:
        p = probe()
        assert "summary" in p.dispatch(EVALUATE_LINE, {"moves_san": ["e4"]})
        assert "moves" in p.dispatch(BEST_REPLIES, {"moves_san": [], "count": 2})

    def test_a_missing_move_list_is_treated_as_empty(self) -> None:
        assert "summary" in probe().dispatch(EVALUATE_LINE, {})
