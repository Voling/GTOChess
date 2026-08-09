"""Golden set.

These are regression tests for *sensitivity*, not for chess strength. Each fixture
pairs something that genuinely decides the position with something loud and
irrelevant, and asserts the pipeline ranks them correctly. When a prompt
version, an engine build, or a threshold changes, this is the file that catches
"the model started talking about the passed pawn" as a failure rather than as a
support ticket.
"""

from __future__ import annotations

import chess
import pytest

from gtochess.analysis.fingerprint import fingerprint_from_pv, zone_of
from gtochess.analysis.landscape import compute_landscape
from gtochess.analysis.sensitivity import compute_sensitivity
from gtochess.domain.identity import canonical_epd, chess960_board, position_key
from gtochess.domain.models import KnowledgeAvailability, KnowledgeTier, Variant
from gtochess.engine.reference import ReferenceEngine

# White mates in one with Ra8#. Black has a pawn on b2 one square from
# promotion that is also attacking the mating rook -- maximally loud, entirely
# irrelevant. This is the fixture the whole sensitivity design exists to pass.
MATE_WITH_LOUD_PASSER = "7k/6pp/8/8/8/8/1p6/R5K1 w - - 0 1"

DEPTH = 3


@pytest.fixture(scope="module")
def engine() -> ReferenceEngine:
    return ReferenceEngine(max_depth=DEPTH)


class TestMateInOneTrap:
    def test_engine_finds_the_mate(self, engine: ReferenceEngine) -> None:
        board = chess.Board(MATE_WITH_LOUD_PASSER)
        report = engine.analyse(board, depth=DEPTH, multipv=4)
        assert report.best.move_san == "Ra8#"
        assert report.best.mate_in == 1

    def test_landscape_reports_a_single_answer(self, engine: ReferenceEngine) -> None:
        board = chess.Board(MATE_WITH_LOUD_PASSER)
        report = engine.analyse(board, depth=DEPTH, multipv=4)
        landscape = compute_landscape(board, report)
        assert landscape.is_single_answer
        assert landscape.forced_mate_in == 1

    def test_loud_passed_pawn_is_not_salient(self, engine: ReferenceEngine) -> None:
        """The core assertion. Deleting b2 leaves the mate intact, so its
        ablation delta is ~0 and it must not reach the dossier."""
        board = chess.Board(MATE_WITH_LOUD_PASSER)
        baseline = engine.analyse(board, depth=DEPTH, multipv=4)
        sensitivity = compute_sensitivity(engine, board, baseline=baseline, depth=DEPTH)

        top_squares = sensitivity.squares_in_top(3)
        assert "b2" not in top_squares, (
            "the irrelevant passed pawn reached the top of the sensitivity ranking; "
            f"ranking was {[(i.square, i.delta_cp) for i in sensitivity.top(5)]}"
        )

    def test_the_mating_piece_is_salient(self, engine: ReferenceEngine) -> None:
        board = chess.Board(MATE_WITH_LOUD_PASSER)
        baseline = engine.analyse(board, depth=DEPTH, multipv=4)
        sensitivity = compute_sensitivity(engine, board, baseline=baseline, depth=DEPTH)

        assert sensitivity.items, "expected a non-empty sensitivity ranking"
        assert "a1" in sensitivity.squares_in_top(3), (
            "removing the mating rook must move the evaluation more than anything else; "
            f"ranking was {[(i.square, i.delta_cp) for i in sensitivity.top(5)]}"
        )

    def test_blacks_own_pawns_rank_as_salient(self, engine: ReferenceEngine) -> None:
        """g7/h7 entomb the king -- they are *why* it is mate, so they belong in
        the ranking even though they are Black's own material."""
        board = chess.Board(MATE_WITH_LOUD_PASSER)
        baseline = engine.analyse(board, depth=DEPTH, multipv=4)
        sensitivity = compute_sensitivity(engine, board, baseline=baseline, depth=DEPTH)

        ranked = sensitivity.squares_in_top(4)
        assert ranked & {"g7", "h7"}, f"expected the entombing pawns in the ranking, got {ranked}"


class TestPositionIdentity:
    def test_transpositions_collapse_to_one_key(self) -> None:
        a = chess.Board()
        for san in ["d4", "Nf6", "c4"]:
            a.push_san(san)
        b = chess.Board()
        for san in ["c4", "Nf6", "d4"]:
            b.push_san(san)
        assert position_key(a) == position_key(b)

    def test_move_counters_do_not_affect_identity(self) -> None:
        a = chess.Board("7k/6pp/8/8/8/8/1p6/R5K1 w - - 0 1")
        b = chess.Board("7k/6pp/8/8/8/8/1p6/R5K1 w - - 44 90")
        assert position_key(a) == position_key(b)

    def test_unreachable_en_passant_square_is_normalised_away(self) -> None:
        """Without this, two positions that play identically get distinct keys
        and every transposition through them is lost."""
        with_ep = chess.Board("4k3/8/8/8/1p6/8/P7/4K3 w - - 0 1")
        with_ep.push_san("a4")  # sets an ep square; b4xa3 e.p. IS legal here
        assert "a3" in canonical_epd(with_ep)

        no_capture = chess.Board("4k3/8/8/8/8/8/P7/4K3 w - - 0 1")
        no_capture.push_san("a4")  # ep square set, but no pawn can take
        assert canonical_epd(no_capture).endswith(" -")


class TestChess960:
    """960 is the honest test of the architecture: every theory tier is empty,
    so anything the pipeline still produces came from the engine layers."""

    def test_key_is_variant_scoped(self) -> None:
        standard = chess.Board()
        as_960 = chess960_board(518)  # 518 is the standard start
        assert standard.board_fen() == as_960.board_fen()
        assert position_key(standard) != position_key(as_960), (
            "standard and 960 positions must not share a key -- castling "
            "semantics and available knowledge tiers differ"
        )
        assert position_key(as_960).variant is Variant.CHESS960

    def test_intrinsic_tiers_survive_without_theory(self) -> None:
        availability = KnowledgeAvailability.intrinsic_only()
        assert not availability.theory_applies
        assert availability.has(KnowledgeTier.ENGINE)
        assert availability.has(KnowledgeTier.ABLATION)
        assert availability.has(KnowledgeTier.PLAN_FINGERPRINT)
        assert not availability.has(KnowledgeTier.OPENING_NAME)
        assert not availability.has(KnowledgeTier.LITERATURE)

    @pytest.mark.parametrize("scharnagl_id", [0, 356, 959])
    def test_engine_and_fingerprint_work_on_960(
        self, engine: ReferenceEngine, scharnagl_id: int
    ) -> None:
        board = chess960_board(scharnagl_id)
        report = engine.analyse(board, depth=2, multipv=3)
        assert report.lines
        assert report.key.variant is Variant.CHESS960

        fingerprint = fingerprint_from_pv(board, report.best.pv_uci)
        assert fingerprint.steps, "plan fingerprinting must not depend on opening theory"


class TestPlanFingerprint:
    def test_zones_are_relative_to_the_mover(self) -> None:
        """The same idea played by either colour must produce the same token."""
        assert zone_of(chess.B2, chess.WHITE) == zone_of(chess.B7, chess.BLACK)
        assert zone_of(chess.G7, chess.WHITE) == zone_of(chess.G2, chess.BLACK)

    def test_mirrored_lines_share_a_fingerprint(self) -> None:
        white_to_move = chess.Board("4k3/8/8/8/8/8/1P6/4K3 w - - 0 1")
        black_to_move = chess.Board("4k3/1p6/8/8/8/8/8/4K3 b - - 0 1")
        wf = fingerprint_from_pv(white_to_move, ["b2b4"])
        bf = fingerprint_from_pv(black_to_move, ["b7b5"])
        assert wf.digest == bf.digest, f"{wf.token_string()!r} != {bf.token_string()!r}"

    def test_illegal_pv_truncates_rather_than_fabricating(self) -> None:
        board = chess.Board()
        fingerprint = fingerprint_from_pv(board, ["e2e4", "e7e5", "a1a8"])
        assert len(fingerprint.steps) == 2
