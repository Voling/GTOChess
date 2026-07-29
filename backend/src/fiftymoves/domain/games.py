from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from fiftymoves.domain.models import Variant


class GameSource(StrEnum):
    LICHESS = "lichess"
    CHESSCOM = "chesscom"


class Side(StrEnum):
    WHITE = "white"
    BLACK = "black"
    BOTH = "both"

    def covers(self, player_is_white: bool) -> bool:
        if self is Side.BOTH:
            return True
        return (self is Side.WHITE) == player_is_white


class GameRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: GameSource
    game_id: str
    played_at_ms: int
    variant: Variant
    speed: str
    rated: bool
    player_is_white: bool
    player_rating: int | None
    opponent_rating: int | None
    score: float
    eco: str | None
    opening_name: str | None
    opening_ply: int | None
    initial_fen: str | None
    moves_san: tuple[str, ...]
    clocks_cs: tuple[int, ...]
    evals_cp: tuple[int | None, ...]
    initial_seconds: int | None
    increment_seconds: int | None

    @property
    def opening_id(self) -> str | None:
        if self.eco is None:
            return None
        return f"{self.eco}:{'white' if self.player_is_white else 'black'}"
