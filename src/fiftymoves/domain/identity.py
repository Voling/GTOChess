"""Canonical position identity.

The whole graph hangs off this key, so it has to be exactly right:

* Halfmove clock and fullmove number are dropped -- they are game state, not
  position state, and keeping them would shatter every transposition.
* The en-passant square is kept only when an en-passant capture is actually
  legal. Otherwise two positions that play identically would get distinct keys.
* Castling rights use Shredder notation (rook files), which is unambiguous for
  both standard chess and Chess960. Polyglot's Zobrist hashing assumes standard
  castling squares and is not safe for 960, so we hash the canonical string
  ourselves instead.
"""

from __future__ import annotations

import hashlib
from typing import Final

import chess

from fiftymoves.domain.models import PositionKey, Variant

_DIGEST_BYTES: Final = 16


def canonical_epd(board: chess.Board) -> str:
    """Position-identifying fields only, in Shredder-FEN form.

    Returns ``<placement> <turn> <castling> <ep>``.
    """
    placement, turn, castling, ep_square = board.shredder_fen().split(" ")[:4]
    if not board.has_legal_en_passant():
        ep_square = "-"
    return f"{placement} {turn} {castling} {ep_square}"


def variant_of(board: chess.Board) -> Variant:
    return Variant.CHESS960 if board.chess960 else Variant.STANDARD


def position_key(board: chess.Board) -> PositionKey:
    """Stable content-addressed key for a position.

    The variant is part of the hashed payload: a standard position and a 960
    position with identical placement are *not* interchangeable, because their
    castling semantics and their available knowledge tiers differ.
    """
    variant = variant_of(board)
    epd = canonical_epd(board)
    digest = hashlib.blake2b(
        f"{variant.value}|{epd}".encode(), digest_size=_DIGEST_BYTES
    ).hexdigest()
    return PositionKey(variant=variant, epd=epd, digest=digest)


def board_from_epd(epd: str, variant: Variant) -> chess.Board:
    """Inverse of :func:`canonical_epd`. Move counters are reset to 0/1."""
    board = chess.Board(f"{epd} 0 1", chess960=variant is Variant.CHESS960)
    return board


def board_from_key(key: PositionKey) -> chess.Board:
    return board_from_epd(key.epd, key.variant)


def chess960_board(scharnagl_id: int) -> chess.Board:
    """Starting position for a Chess960 slot (0-959, standard chess is 518)."""
    if not 0 <= scharnagl_id <= 959:
        raise ValueError(f"Chess960 position id must be 0-959, got {scharnagl_id}")
    board = chess.Board(chess960=True)
    board.set_chess960_pos(scharnagl_id)
    return board
