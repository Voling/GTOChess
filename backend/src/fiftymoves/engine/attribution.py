from __future__ import annotations

import re
import subprocess

import chess

from fiftymoves.domain.models import PieceValue, PositionAttribution

_ROW = re.compile(r"^\|(.+)\|$")
_NUMBER = re.compile(r"^[+-]\d+\.\d+$")
_CONTRIBUTION = re.compile(
    r"^\|\s*(\d+|Bucket).*?\|\s*([+-]?\s*\d+\.\d+)\s*\|\s*([+-]?\s*\d+\.\d+)\s*\|"
)


def _cells(line: str) -> list[str]:
    match = _ROW.match(line.strip())
    if match is None:
        return []
    return [cell.strip() for cell in match.group(1).split("|")]


def parse_eval(output: str) -> PositionAttribution:
    """Read Stockfish's own account of where the evaluation comes from.

    ``eval`` prints a board of per piece values and a material against
    positional split. It costs one call and no search, unlike ablation, which
    re-searches once per piece.
    """
    lines = output.splitlines()
    symbols: list[list[str]] = []
    values: list[list[str]] = []

    for line in lines:
        cells = _cells(line)
        if len(cells) != 8:
            continue
        if all(c == "" or (len(c) == 1 and c.isalpha()) for c in cells):
            symbols.append(cells)
        elif any(_NUMBER.match(c) for c in cells):
            values.append(cells)

    pieces: list[PieceValue] = []
    # The board prints rank 8 first, and each rank is a symbol row then a value row.
    for index, (row_symbols, row_values) in enumerate(zip(symbols, values, strict=False)):
        rank = 7 - index
        for file, (symbol, value) in enumerate(zip(row_symbols, row_values, strict=False)):
            if not symbol or not _NUMBER.match(value):
                continue
            pieces.append(
                PieceValue(
                    square=chess.square_name(chess.square(file, rank)),
                    piece_symbol=symbol,
                    value_pawns=float(value),
                )
            )

    material = positional = None
    for line in lines:
        found = _CONTRIBUTION.match(line.strip())
        if found and found.group(1) != "Bucket":
            material = float(found.group(2).replace(" ", ""))
            positional = float(found.group(3).replace(" ", ""))

    return PositionAttribution(
        pieces=tuple(pieces),
        material_pawns=material,
        positional_pawns=positional,
    )


def attribute(
    binary_path: str, board: chess.Board, *, timeout_s: float = 15.0
) -> PositionAttribution:
    script = f"position fen {board.fen()}\neval\nquit\n"
    try:
        finished = subprocess.run(
            [binary_path],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return PositionAttribution(pieces=(), material_pawns=None, positional_pawns=None)
    return parse_eval(finished.stdout)
