from __future__ import annotations

import chess

from fiftymoves.domain.structure import PawnShape, Structure

FILES = "abcdefgh"
OUTPOST_RANKS = {chess.WHITE: (3, 4, 5), chess.BLACK: (4, 3, 2)}
# Only the contested part of a side's own half. The back two ranks are behind the
# pawns and no pawn ever covers them, so counting those calls every position full
# of holes and says nothing.
WEAK_RANKS = {chess.WHITE: (2, 3), chess.BLACK: (5, 4)}


def _forward(colour: chess.Color, rank: int) -> range:
    return range(rank + 1, 8) if colour == chess.WHITE else range(0, rank)


def _pawn_files(pawns: set[int]) -> dict[int, list[int]]:
    by_file: dict[int, list[int]] = {}
    for square in pawns:
        by_file.setdefault(chess.square_file(square), []).append(chess.square_rank(square))
    return by_file


def _islands(files: set[int]) -> int:
    count = 0
    for f in sorted(files):
        if f - 1 not in files:
            count += 1
    return count


def _can_ever_attack(colour: chess.Color, pawns: set[int], square: int) -> bool:
    """Whether a pawn of this colour could one day cover the square by advancing."""
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    for neighbour in (file - 1, file + 1):
        if not 0 <= neighbour <= 7:
            continue
        for pawn in pawns:
            if chess.square_file(pawn) != neighbour:
                continue
            pawn_rank = chess.square_rank(pawn)
            if colour == chess.WHITE and pawn_rank < rank:
                return True
            if colour == chess.BLACK and pawn_rank > rank:
                return True
    return False


def _shape(board: chess.Board, colour: chess.Color) -> PawnShape:
    pawns = set(board.pieces(chess.PAWN, colour))
    enemy = set(board.pieces(chess.PAWN, not colour))
    by_file = _pawn_files(pawns)

    isolated: list[int] = []
    doubled: list[int] = []
    backward: list[int] = []
    passed: list[int] = []

    for square in sorted(pawns):
        file = chess.square_file(square)
        rank = chess.square_rank(square)
        neighbours = [f for f in (file - 1, file + 1) if 0 <= f <= 7 and f in by_file]

        if not neighbours:
            isolated.append(square)
        if len(by_file.get(file, [])) > 1:
            doubled.append(square)

        ahead = _forward(colour, rank)
        blocked = any(
            chess.square_file(p) in (file - 1, file, file + 1) and chess.square_rank(p) in ahead
            for p in enemy
        )
        if not blocked:
            passed.append(square)

        # Behind every neighbour and unable to advance without meeting a pawn.
        if neighbours and not any(
            (
                chess.square_rank(sq) <= rank
                if colour == chess.WHITE
                else chess.square_rank(sq) >= rank
            )
            for f in neighbours
            for sq in pawns
            if chess.square_file(sq) == f
        ):
            step = 1 if colour == chess.WHITE else -1
            front = chess.square(file, rank + step) if 0 <= rank + step <= 7 else None
            if front is not None and _can_ever_attack(not colour, enemy, front):
                backward.append(square)

    weak = [
        sq
        for sq in chess.SQUARES
        if chess.square_rank(sq) in WEAK_RANKS[colour] and not _can_ever_attack(colour, pawns, sq)
    ]

    outposts = [
        sq
        for sq in chess.SQUARES
        if chess.square_rank(sq) in OUTPOST_RANKS[colour]
        and not _can_ever_attack(not colour, enemy, sq)
        and board.attackers(colour, sq) & board.pieces(chess.PAWN, colour)
    ]

    name = chess.square_name
    return PawnShape(
        isolated=tuple(name(s) for s in isolated),
        doubled=tuple(name(s) for s in doubled),
        backward=tuple(name(s) for s in backward),
        passed=tuple(name(s) for s in passed),
        islands=_islands(set(by_file)),
        weak_squares=tuple(name(s) for s in weak),
        outposts=tuple(name(s) for s in outposts),
    )


def compute_structure(board: chess.Board) -> Structure:
    white = set(board.pieces(chess.PAWN, chess.WHITE))
    black = set(board.pieces(chess.PAWN, chess.BLACK))
    white_files = {chess.square_file(s) for s in white}
    black_files = {chess.square_file(s) for s in black}

    return Structure(
        white=_shape(board, chess.WHITE),
        black=_shape(board, chess.BLACK),
        open_files=tuple(
            FILES[f] for f in range(8) if f not in white_files and f not in black_files
        ),
        half_open_white=tuple(
            FILES[f] for f in range(8) if f not in white_files and f in black_files
        ),
        half_open_black=tuple(
            FILES[f] for f in range(8) if f not in black_files and f in white_files
        ),
    )
