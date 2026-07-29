from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fiftymoves.domain.models import Variant


class PositionKnowledge(BaseModel):
    """What is known about one position, independent of who reached it.

    Built from the engine and the player's games, never from scraped prose. The
    plan digest is the transfer key: two positions that look nothing alike share
    it when the engine plays the same idea in both.
    """

    model_config = ConfigDict(frozen=True)

    digest: str
    epd: str
    variant: Variant
    depth: int

    best_san: str
    best_cp: int
    delta_to_second_cp: int | None
    is_single_answer: bool
    playable_moves: int
    legal_moves: int

    plan_digest: str
    plan_tokens: str = Field(description="The abstracted plan, readable and comparable")
    load_bearing: tuple[str, ...] = Field(
        default=(), description="Squares the evaluation depends on beyond material"
    )

    @property
    def pawns(self) -> float:
        return round(self.best_cp / 100, 2)

    def plan_prefix(self, steps: int) -> str:
        return " ".join(self.plan_tokens.split()[:steps])


class PlanNeighbour(BaseModel):
    model_config = ConfigDict(frozen=True)

    digest: str
    epd: str
    best_san: str
    best_cp: int


class KnowledgeView(BaseModel):
    position: PositionKnowledge
    shares_plan_with: tuple[PlanNeighbour, ...] = ()
    plan_steps: int = Field(
        default=0, description="How many steps of the plan the neighbours actually share"
    )
