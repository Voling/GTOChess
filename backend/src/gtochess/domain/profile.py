from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from gtochess.domain.models import PositionKey


class MoveDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: PositionKey
    game_id: str
    ply: int
    played_uci: str
    played_san: str
    mover_is_white: bool

    position_eval_cp: int
    eval_loss_cp: int | None
    is_decision_point: bool

    best_uci: str
    best_san: str

    played_reply_count: int
    played_material_gain_cp: int
    played_is_capture: bool
    played_is_check: bool
    played_targets_opponent_zone: bool
    best_targets_opponent_zone: bool

    played_plan_digest: str | None = None
    theory_plan_digest: str | None = None

    alternative_reply_counts: tuple[int, ...] = ()
    alternative_material_gains: tuple[int, ...] = ()

    @property
    def had_alternatives(self) -> bool:
        return len(self.alternative_reply_counts) > 0

    @property
    def quiet_alternative_available(self) -> bool:
        return any(gain == 0 for gain in self.alternative_material_gains)

    @property
    def capture_available(self) -> bool:
        return self.played_material_gain_cp > 0 or any(
            gain > 0 for gain in self.alternative_material_gains
        )


class Trait(StrEnum):
    FORCING_PREFERENCE = "forcing_preference"
    MATERIAL_GREED = "material_greed"
    AGGRESSION = "aggression"
    ACCURACY = "accuracy"
    RESILIENCE = "resilience"
    CONVERSION = "conversion"
    TILT = "tilt"
    CONSISTENCY = "consistency"
    OPENING_EDGE = "opening_edge"


class TraitUnit(StrEnum):
    SHARE = "share"
    CENTIPAWNS = "centipawns"


class TraitScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    trait: Trait
    value: float
    unit: TraitUnit
    sample_size: int
    evidence: tuple[str, ...] = Field(default=())


class PlayerProfile(BaseModel):
    traits: tuple[TraitScore, ...]
    omitted: tuple[Trait, ...]
    decisions_considered: int
    games_considered: int

    def get(self, trait: Trait) -> TraitScore | None:
        for score in self.traits:
            if score.trait is trait:
                return score
        return None
