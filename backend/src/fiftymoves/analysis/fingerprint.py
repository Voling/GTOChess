"""Plan fingerprints.

The strongest transfer key in the system, because it is behavioural rather than
visual. Two positions can look nothing alike and still share a fingerprint if
the engine plays the same idea in both -- b-pawn to b5, exchange on c6, double
on the b-file is a minority attack whether it arises from a Carlsbad structure
or from somewhere else entirely.

Squares are abstracted to zones *relative to the side making that move*, so the
same idea played by Black matches the same idea played by White.
"""

from __future__ import annotations

import chess

from fiftymoves.domain.models import EngineLine, PlanFingerprint, PlanStep, Zone

_FILE_GROUP: dict[int, str] = {
    0: "qs",
    1: "qs",
    2: "qs",  # a b c
    3: "ctr",
    4: "ctr",  # d e
    5: "ks",
    6: "ks",
    7: "ks",  # f g h
}

_ZONE_BY_PARTS: dict[tuple[str, str], Zone] = {
    ("own", "qs"): Zone.OWN_QUEENSIDE,
    ("own", "ctr"): Zone.OWN_CENTER,
    ("own", "ks"): Zone.OWN_KINGSIDE,
    ("mid", "qs"): Zone.MID_QUEENSIDE,
    ("mid", "ctr"): Zone.MID_CENTER,
    ("mid", "ks"): Zone.MID_KINGSIDE,
    ("opp", "qs"): Zone.OPP_QUEENSIDE,
    ("opp", "ctr"): Zone.OPP_CENTER,
    ("opp", "ks"): Zone.OPP_KINGSIDE,
}


def zone_of(square: int, mover: chess.Color) -> Zone:
    """Coarse board region from the mover's point of view."""
    file_group = _FILE_GROUP[chess.square_file(square)]
    rank = chess.square_rank(square)
    relative_rank = rank if mover == chess.WHITE else 7 - rank
    if relative_rank <= 2:
        depth = "own"
    elif relative_rank <= 4:
        depth = "mid"
    else:
        depth = "opp"
    return _ZONE_BY_PARTS[(depth, file_group)]


def fingerprint_from_pv(
    board: chess.Board, pv_uci: list[str], *, max_steps: int = 6
) -> PlanFingerprint:
    """Abstract a principal variation into a transferable plan signature.

    ``board`` is not mutated.
    """
    probe = board.copy(stack=False)
    steps: list[PlanStep] = []

    for uci in pv_uci[:max_steps]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in probe.legal_moves:
            # A PV that does not replay is a bug upstream, not something to
            # paper over -- stop rather than emit a fingerprint built on sand.
            break

        mover = probe.turn
        piece = probe.piece_at(move.from_square)
        if piece is None:  # pragma: no cover - defensive
            break
        is_capture = probe.is_capture(move)
        probe.push(move)
        steps.append(
            PlanStep(
                piece=chess.piece_symbol(piece.piece_type).upper(),
                from_zone=zone_of(move.from_square, mover),
                to_zone=zone_of(move.to_square, mover),
                is_capture=is_capture,
                is_check=probe.is_check(),
            )
        )

    return PlanFingerprint.from_steps(steps)


def fingerprint_from_line(
    board: chess.Board, line: EngineLine, *, max_steps: int = 6
) -> PlanFingerprint:
    return fingerprint_from_pv(board, line.pv_uci, max_steps=max_steps)
