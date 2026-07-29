from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(StrEnum):
    EVAL = "eval"
    ENGINE_LINE = "engine_line"
    SENSITIVITY = "sensitivity"
    LANDSCAPE = "landscape"
    REPERTOIRE = "repertoire"
    OPENING = "opening"


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: EvidenceKind
    statement: str


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    evidence_id: str


class Explanation(BaseModel):
    model_config = ConfigDict(frozen=True)

    digest: str
    headline: str
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    source: str
    model: str | None = None
    dropped_claims: int = Field(default=0, description="Claims cut for citing unknown evidence")
    fallback_reason: str | None = Field(
        default=None, description="Why the configured model was not used"
    )

    @property
    def grounded(self) -> bool:
        known = {e.id for e in self.evidence}
        return all(c.evidence_id in known for c in self.claims)
