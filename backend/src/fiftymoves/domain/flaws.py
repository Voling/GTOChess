from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from fiftymoves.domain.models import PositionKey


class IntentConfidence(StrEnum):
    REPEATED = "repeated"
    SINGLE = "single"
    UNKNOWN = "unknown"


class OpeningFlaw(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: PositionKey
    depth_ply: int
    played_uci: str
    played_san: str
    best_uci: str
    best_san: str
    occurrences: int
    mean_eval_loss_cp: float
    played_plan_digest: str | None
    theory_plan_digest: str | None
    plan_recurrences: int
    game_ids: tuple[str, ...]

    @property
    def damage_cp(self) -> float:
        return self.occurrences * self.mean_eval_loss_cp

    @property
    def intent_confidence(self) -> IntentConfidence:
        if self.played_plan_digest is None:
            return IntentConfidence.UNKNOWN
        if self.plan_recurrences > 1:
            return IntentConfidence.REPEATED
        return IntentConfidence.SINGLE

    @property
    def is_off_theme(self) -> bool:
        if self.played_plan_digest is None or self.theory_plan_digest is None:
            return False
        return self.played_plan_digest != self.theory_plan_digest


class OpeningRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    opening_id: str
    games: int
    score: float
    population_score: float
