from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gtochess.domain.annotations import MoveQuality


class MoveOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent: str
    child: str
    uci: str
    san: str
    line: str = Field(description="Moves leading to the position this was played from")
    quality: MoveQuality
    loss_cp: int
    best_san: str
    games: int
    wins: int
    draws: int
    losses: int
    score: float
    points_lost: float = Field(
        description="Games multiplied by how far this move scores below the sound baseline"
    )


class QualityOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    quality: MoveQuality
    moves: int = Field(description="Distinct move choices, not games")
    games: int
    wins: int
    draws: int
    losses: int
    score: float
    mean_loss_cp: float


class OutcomeReport(BaseModel):
    by_quality: tuple[QualityOutcome, ...] = ()
    worst: tuple[MoveOutcome, ...] = ()
    moves_measured: int = 0
    moves_unmeasured: int = 0
    sound_score: float = 0.0
    flawed_score: float = 0.0
    score_gap: float = Field(
        default=0.0, description="How far the flagged moves score below the sound ones"
    )
