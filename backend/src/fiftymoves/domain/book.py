from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PositionLosses(BaseModel):
    model_config = ConfigDict(frozen=True)

    digest: str
    epd: str
    depth: int
    best_uci: str
    best_san: str
    best_cp: int
    losses: dict[str, int] = Field(
        description="Reply in UCI to centipawns given up, never negative"
    )
    sans: dict[str, str] = Field(default_factory=dict)

    def for_move(self, uci: str) -> int | None:
        return self.losses.get(uci)


class MoveAccuracy(BaseModel):
    model_config = ConfigDict(frozen=True)

    full_move: int
    positions: int = Field(description="Distinct positions, so repetition cannot inflate this")
    played: int
    mean_loss_cp: float
    clean_share: float

    @property
    def pawns(self) -> float:
        return round(self.mean_loss_cp / 100, 2)


class FamilyBook(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    games: int
    positions: int
    by_move: tuple[MoveAccuracy, ...] = ()
    raw_depth: int = Field(description="Last full move the player held the band, before shrinking")
    book_depth: float = Field(description="Shrunk toward the player's own mean by volume")
    clean_share: float
    mean_loss_cp: float


class OpeningPhase(BaseModel):
    families: tuple[FamilyBook, ...] = ()
    book_depth: float
    clean_share: float
    mean_loss_cp: float
    positions_scored: int
    moves_scored: int
    band_cp: int
