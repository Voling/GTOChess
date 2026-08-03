from __future__ import annotations

import chess

from fiftymoves.analysis.structure import compute_structure

STARTING = chess.STARTING_FEN
# 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Bxc6 dxc6, the Exchange Ruy: Black's queenside is
# doubled on the c file and White has a healthy four against three on the other wing.
EXCHANGE_RUY = "r1bqkbnr/1pp2ppp/p1p5/4p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 5"
# An isolated queen's pawn on d4 with nothing on c or e.
ISOLANI = "rnbqkbnr/pp3ppp/4p3/8/3P4/8/PP3PPP/RNBQKBNR w KQkq - 0 1"
PASSER = "8/8/8/3P4/8/8/8/8 w - - 0 1"


def structure(fen: str):
    return compute_structure(chess.Board(fen))


class TestOpeningPosition:
    def test_nothing_is_wrong_with_the_starting_position(self) -> None:
        shape = structure(STARTING)
        assert shape.white.empty
        assert shape.black.empty
        assert shape.white.islands == 1
        assert shape.open_files == ()

    def test_no_file_is_open_before_a_pawn_moves(self) -> None:
        shape = structure(STARTING)
        assert shape.half_open_white == ()
        assert shape.half_open_black == ()


class TestDoubled:
    def test_the_exchange_ruy_doubles_black_on_the_c_file(self) -> None:
        shape = structure(EXCHANGE_RUY)
        assert set(shape.black.doubled) == {"c6", "c7"}
        assert shape.white.doubled == ()

    def test_doubling_costs_black_a_pawn_island(self) -> None:
        assert structure(EXCHANGE_RUY).black.islands == 2

    def test_the_d_file_is_half_open_for_black_after_dxc6(self) -> None:
        assert "d" in structure(EXCHANGE_RUY).half_open_black


class TestIsolated:
    def test_a_lone_d_pawn_is_isolated(self) -> None:
        shape = structure(ISOLANI)
        assert "d4" in shape.white.isolated

    def test_a_pawn_with_a_neighbour_is_not_isolated(self) -> None:
        assert structure(STARTING).white.isolated == ()


class TestPassed:
    def test_a_pawn_with_nothing_in_front_is_passed(self) -> None:
        assert structure(PASSER).white.passed == ("d5",)

    def test_an_enemy_pawn_on_the_next_file_stops_it_being_passed(self) -> None:
        shape = structure("8/2p5/8/3P4/8/8/8/8 w - - 0 1")
        assert shape.white.passed == ()

    def test_nothing_is_passed_from_the_start(self) -> None:
        assert structure(STARTING).white.passed == ()


class TestWeakSquares:
    def test_the_starting_position_has_no_holes(self) -> None:
        shape = structure(STARTING)
        assert shape.white.weak_squares == ()

    def test_advancing_both_bishop_pawns_leaves_a_hole(self) -> None:
        # White pawns on b4 and d4 can never cover c3 again.
        shape = structure("rnbqkbnr/pppppppp/8/8/1P1P4/8/P1P1PPPP/RNBQKBNR w KQkq - 0 1")
        assert "c3" in shape.white.weak_squares


class TestOutposts:
    def test_a_pawn_backed_square_the_enemy_cannot_challenge_is_an_outpost(self) -> None:
        # The e5 pawn covers d6 and f6, and Black has no c or e pawn to drive it off.
        shape = structure("rnbqkbnr/pp1p1ppp/8/4P3/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
        assert "d6" in shape.white.outposts

    def test_a_square_the_enemy_g_pawn_still_covers_is_no_outpost(self) -> None:
        # f6 is covered by the e5 pawn too, but g7 attacks it and always will.
        shape = structure("rnbqkbnr/pp1p1ppp/8/4P3/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
        assert "f6" not in shape.white.outposts

    def test_a_square_an_enemy_pawn_still_covers_is_no_outpost(self) -> None:
        assert structure(STARTING).white.outposts == ()
