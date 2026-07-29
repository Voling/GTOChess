from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fiftymoves.domain.models import PositionKey


class RepertoireNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: PositionKey
    depth_ply: int
    game_count: int
    replies: dict[str, int] = Field(default_factory=dict)

    @property
    def reply_total(self) -> int:
        return sum(self.replies.values())

    @property
    def distinct_replies(self) -> int:
        return len(self.replies)

    @property
    def has_choice(self) -> bool:
        return self.distinct_replies > 1

    @property
    def top_reply_share(self) -> float:
        total = self.reply_total
        if total == 0:
            return 0.0
        return max(self.replies.values()) / total


class SkipReason(StrEnum):
    TOO_SHALLOW = "too_shallow"
    TOO_DEEP = "too_deep"
    LOW_VOLUME = "low_volume"
    OVER_BUDGET = "over_budget"


class SelectionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_ply: int = 6
    max_ply: int = 24
    min_games: int = 3
    min_divergence_games: int = 2
    budget: int = 400
    frontier_sample: int = 2


class SelectionResult(BaseModel):
    selected: tuple[RepertoireNode, ...]
    skipped: dict[SkipReason, int]
    considered: int

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    @property
    def fully_accounted_for(self) -> bool:
        return self.selected_count + sum(self.skipped.values()) == self.considered
