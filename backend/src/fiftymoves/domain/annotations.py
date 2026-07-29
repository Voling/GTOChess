from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MoveQuality(StrEnum):
    BLUNDER = "??"
    MISTAKE = "?"
    DUBIOUS = "?!"
    SOUND = "sound"

    @property
    def symbol(self) -> str:
        return "" if self is MoveQuality.SOUND else self.value


class MoveAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent: str
    child: str
    san: str
    quality: MoveQuality
    loss_cp: int = Field(description="Centipawns given up against the engine's best, mover's view")
    best_san: str
    games: int
    by_player: bool


class AnnotationSet(BaseModel):
    username: str
    shape: str
    annotations: tuple[MoveAnnotation, ...]
    positions_searched: int
    edges_considered: int
    depth: int
    truncated: bool = False

    @property
    def flawed(self) -> tuple[MoveAnnotation, ...]:
        return tuple(a for a in self.annotations if a.quality is not MoveQuality.SOUND)
