"""Integration tests against the real, pinned Stockfish.

The golden set runs on ReferenceEngine so CI stays fast, but that engine cannot
catch anything about the UCI protocol. These can -- the Chess960 test below
exists because a hand-rolled ``UCI_Chess960`` configure looked correct, passed
every ReferenceEngine test, and failed the moment a real engine was attached.

Marked ``needs_engine``; skipped when nothing is provisioned.
"""

from __future__ import annotations

from collections.abc import Iterator

import chess
import pytest

from gtochess.analysis.fingerprint import fingerprint_from_line
from gtochess.analysis.landscape import compute_landscape
from gtochess.analysis.sensitivity import compute_sensitivity
from gtochess.config import Settings
from gtochess.domain.models import Variant
from gtochess.engine.stockfish import StockfishEngine

pytestmark = pytest.mark.needs_engine

MATE_WITH_LOUD_PASSER = "7k/6pp/8/8/8/8/1p6/R5K1 w - - 0 1"
DEPTH = 12
ABLATION_DEPTH = 8


@pytest.fixture(scope="module")
def engine() -> Iterator[StockfishEngine]:
    settings = Settings()
    try:
        path = settings.resolve_engine_path()
    except Exception as exc:  # noqa: BLE001 - any resolution failure means skip
        pytest.skip(f"no provisioned engine: {exc}")
    with StockfishEngine(str(path)) as running:
        yield running


def test_engine_identifies_itself(engine: StockfishEngine) -> None:
    assert "stockfish" in engine.engine_id.lower()


def test_finds_the_mate_and_folds_the_score(engine: StockfishEngine) -> None:
    board = chess.Board(MATE_WITH_LOUD_PASSER)
    report = engine.analyse(board, depth=DEPTH, multipv=4)
    assert report.best.move_san == "Ra8#"
    assert report.best.mate_in == 1
    assert report.best.is_mate
    # Folded mate scores must outrank any plausible material advantage so that
    # ranking across mixed reports stays total.
    assert report.best.score_cp > 50_000


def test_landscape_sees_a_single_answer(engine: StockfishEngine) -> None:
    board = chess.Board(MATE_WITH_LOUD_PASSER)
    report = engine.analyse(board, depth=DEPTH, multipv=4)
    assert compute_landscape(board, report).is_single_answer


def test_loud_passed_pawn_is_not_salient_to_a_real_engine(engine: StockfishEngine) -> None:
    """The golden-set assertion, re-run against Stockfish rather than a material
    counter. This is the one that proves the design, not the arithmetic."""
    board = chess.Board(MATE_WITH_LOUD_PASSER)
    baseline = engine.analyse(board, depth=DEPTH, multipv=4)
    sensitivity = compute_sensitivity(engine, board, baseline=baseline, depth=ABLATION_DEPTH)

    assert "b2" not in sensitivity.squares_in_top(3), [
        (i.square, i.kind.value, i.delta_cp) for i in sensitivity.top(5)
    ]
    assert "a1" in sensitivity.squares_in_top(3), [
        (i.square, i.kind.value, i.delta_cp) for i in sensitivity.top(5)
    ]


class TestChess960OverUci:
    """Regression cover for UCI_Chess960 handling.

    python-chess sets that option itself from ``board.chess960``; configuring it
    by hand raises. Any future attempt to manage it manually fails here.
    """

    @pytest.mark.parametrize("scharnagl_id", [0, 356, 959])
    def test_analyses_960_start_positions(self, engine: StockfishEngine, scharnagl_id: int) -> None:
        from gtochess.domain.identity import chess960_board

        board = chess960_board(scharnagl_id)
        report = engine.analyse(board, depth=8, multipv=3)
        assert report.lines
        assert report.key.variant is Variant.CHESS960
        assert chess.Move.from_uci(report.best.move_uci) in board.legal_moves

    def test_switching_between_variants_on_one_engine(self, engine: StockfishEngine) -> None:
        """The same process must serve both variants -- workers are pooled and do
        not get a fresh engine per position."""
        from gtochess.domain.identity import chess960_board

        standard = engine.analyse(chess.Board(), depth=8, multipv=2)
        castling = chess960_board(356)
        shuffled = engine.analyse(castling, depth=8, multipv=2)
        again = engine.analyse(chess.Board(), depth=8, multipv=2)

        assert standard.key.variant is Variant.STANDARD
        assert shuffled.key.variant is Variant.CHESS960
        assert again.best.move_uci

    def test_plan_fingerprint_works_without_any_theory(self, engine: StockfishEngine) -> None:
        from gtochess.domain.identity import chess960_board

        board = chess960_board(959)
        report = engine.analyse(board, depth=8, multipv=1)
        fingerprint = fingerprint_from_line(board, report.best)
        assert fingerprint.steps
        assert fingerprint.digest
