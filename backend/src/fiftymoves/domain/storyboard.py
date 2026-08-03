from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Glyph(StrEnum):
    BLUNDER = "??"
    MISTAKE = "?"
    DUBIOUS = "?!"
    INTERESTING = "!?"
    STRONG = "!"
    BRILLIANT = "!!"
    PLAIN = ""


class ArrowRole(StrEnum):
    PLAYED = "played"
    IDEA = "idea"
    THREAT = "threat"


class Arrow(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: str
    target: str
    role: ArrowRole = ArrowRole.IDEA


class Beat(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    epd: str
    move_uci: str | None = None
    move_san: str | None = None
    glyph: Glyph = Glyph.PLAIN
    arrows: tuple[Arrow, ...] = ()
    highlights: tuple[str, ...] = ()
    note: str = ""
    score_cp: int | None = Field(
        default=None, description="From White's side, as the engine reports"
    )
    evidence_id: str | None = None

    @property
    def is_root(self) -> bool:
        return self.move_uci is None


class Scene(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    beats: tuple[Beat, ...] = ()

    @property
    def moves(self) -> int:
        return sum(1 for beat in self.beats if not beat.is_root)


class Storyboard(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_epd: str
    orientation: str = "white"
    scenes: tuple[Scene, ...] = ()

    @property
    def beats(self) -> int:
        return sum(len(scene.beats) for scene in self.scenes)
