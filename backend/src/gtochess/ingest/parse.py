from __future__ import annotations

from typing import Any

from gtochess.domain.games import GameRecord, GameSource
from gtochess.domain.models import MATE_SCORE_CP, Variant

SUPPORTED_VARIANTS: dict[str, Variant] = {
    "standard": Variant.STANDARD,
    "chess960": Variant.CHESS960,
}

UNPLAYED_STATUSES = frozenset({"created", "started", "aborted", "noStart", "unknownFinish"})


class UnusableGame(ValueError):
    pass


def _fold_mate(mate_in: int) -> int:
    sign = 1 if mate_in > 0 else -1
    return sign * (MATE_SCORE_CP - abs(mate_in))


def _evals(analysis: list[dict[str, Any]] | None) -> tuple[int | None, ...]:
    if not analysis:
        return ()
    out: list[int | None] = []
    for entry in analysis:
        if "mate" in entry:
            out.append(_fold_mate(int(entry["mate"])))
        elif "eval" in entry:
            out.append(int(entry["eval"]))
        else:
            out.append(None)
    return tuple(out)


def _player_name(side: dict[str, Any]) -> str | None:
    user = side.get("user") or {}
    name = user.get("id") or user.get("name")
    return str(name).lower() if name else None


def parse_lichess_game(raw: dict[str, Any], username: str) -> GameRecord:
    status = raw.get("status")
    if status in UNPLAYED_STATUSES:
        raise UnusableGame(f"status {status!r}")

    variant_name = raw.get("variant", "standard")
    variant = SUPPORTED_VARIANTS.get(variant_name)
    if variant is None:
        raise UnusableGame(f"unsupported variant {variant_name!r}")

    moves = tuple((raw.get("moves") or "").split())
    if not moves:
        raise UnusableGame("no moves")

    players = raw.get("players") or {}
    white = players.get("white") or {}
    black = players.get("black") or {}
    wanted = username.lower()

    if _player_name(white) == wanted:
        player_is_white = True
    elif _player_name(black) == wanted:
        player_is_white = False
    else:
        raise UnusableGame(f"{username!r} did not play this game")

    winner = raw.get("winner")
    if winner is None:
        score = 0.5
    elif (winner == "white") == player_is_white:
        score = 1.0
    else:
        score = 0.0

    mine = white if player_is_white else black
    theirs = black if player_is_white else white
    opening = raw.get("opening") or {}
    clock = raw.get("clock") or {}

    return GameRecord(
        source=GameSource.LICHESS,
        game_id=str(raw["id"]),
        played_at_ms=int(raw.get("createdAt") or 0),
        variant=variant,
        speed=str(raw.get("speed") or "unknown"),
        rated=bool(raw.get("rated")),
        player_is_white=player_is_white,
        player_rating=mine.get("rating"),
        opponent_rating=theirs.get("rating"),
        score=score,
        eco=opening.get("eco"),
        opening_name=opening.get("name"),
        opening_ply=opening.get("ply"),
        initial_fen=raw.get("initialFen"),
        moves_san=moves,
        clocks_cs=tuple(raw.get("clocks") or ()),
        evals_cp=_evals(raw.get("analysis")),
        initial_seconds=clock.get("initial"),
        increment_seconds=clock.get("increment"),
    )
