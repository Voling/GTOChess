from __future__ import annotations

import chess

PIECE_VALUE_CP: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}


def material_cp(board: chess.Board) -> int:
    total = 0
    for piece_type, value in PIECE_VALUE_CP.items():
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total -= value * len(board.pieces(piece_type, chess.BLACK))
    return total


def capture_value_cp(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return PIECE_VALUE_CP[chess.PAWN]
    captured = board.piece_at(move.to_square)
    if captured is None:
        return 0
    return PIECE_VALUE_CP[captured.piece_type]
