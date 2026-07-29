from __future__ import annotations

from pydantic import BaseModel, ConfigDict

UNCLASSIFIED = "unclassified"


class OpeningFamily(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    eco_low: str | None
    eco_high: str | None
    games: int
    as_white: int
    score: float
    forcing_rate: float
    decisive_rate: float
    sharpness: float
    slot: int

    @property
    def eco_range(self) -> str | None:
        if self.eco_low is None:
            return None
        if self.eco_high is None or self.eco_high == self.eco_low:
            return self.eco_low
        return f"{self.eco_low}-{self.eco_high}"
