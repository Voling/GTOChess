"""Core domain models.

Design rule that governs this whole file: every statement the LLM is allowed to
make must be traceable to an object here that was produced deterministically.
There is no field anywhere for "the model's opinion".
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_PLAN_DIGEST_BYTES = 12

# Mate scores are folded into centipawns so ranking is total. Kept well clear of
# any real evaluation so a mate never sorts below a material advantage.
MATE_SCORE_CP: int = 100_000


class Variant(StrEnum):
    STANDARD = "standard"
    CHESS960 = "chess960"


class PositionKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant: Variant
    epd: str
    digest: str


# --------------------------------------------------------------------------
# Engine output
# --------------------------------------------------------------------------


class EngineLine(BaseModel):
    """One principal variation from a multi-PV search."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1, description="1 = engine's best line")
    move_san: str
    move_uci: str
    pv_san: list[str]
    pv_uci: list[str]
    score_cp: int = Field(description="White's point of view, mates folded to MATE_SCORE_CP")
    mate_in: int | None = None
    depth: int

    @property
    def is_mate(self) -> bool:
        return self.mate_in is not None


class EngineReport(BaseModel):
    """Everything one search told us about a position."""

    key: PositionKey
    lines: list[EngineLine]
    depth: int
    engine_id: str

    @property
    def best(self) -> EngineLine:
        return self.lines[0]

    @property
    def score_cp(self) -> int:
        return self.best.score_cp

    def delta_to_second(self) -> int | None:
        """Gap between best and second-best, in centipawns from the mover's side.

        ``None`` when only one legal move exists -- that is a different fact
        from "all moves are equal" and callers must not conflate them.
        """
        if len(self.lines) < 2:
            return None
        return abs(self.lines[0].score_cp - self.lines[1].score_cp)


# --------------------------------------------------------------------------
# Sensitivity -- measured, not enumerated
# --------------------------------------------------------------------------


class AblationKind(StrEnum):
    PIECE_REMOVAL = "piece_removal"
    MOVE_SPACE = "move_space"
    TEMPO = "tempo"


class SensitivityItem(BaseModel):
    """How much one element of the position actually matters.

    Produced by perturbing the board and re-searching. This is what replaces a
    fixed taxonomy of position types: nothing here had to be anticipated.
    """

    model_config = ConfigDict(frozen=True)

    kind: AblationKind
    square: str | None = None
    piece_symbol: str | None = None
    delta_cp: int = Field(description="How far the evaluation moved when perturbed")
    expected_cp: int = Field(
        default=0, description="Delta predicted by material value alone, so ranking sees the rest"
    )
    baseline_cp: int
    perturbed_cp: int
    note: str | None = None

    @property
    def residual_cp(self) -> int:
        return self.delta_cp - self.expected_cp

    @property
    def magnitude(self) -> int:
        return abs(self.residual_cp)


class SensitivityReport(BaseModel):
    key: PositionKey
    items: list[SensitivityItem] = Field(description="Descending by magnitude")
    ablation_depth: int

    def top(self, n: int) -> list[SensitivityItem]:
        return self.items[:n]

    def squares_in_top(self, n: int) -> set[str]:
        return {i.square for i in self.top(n) if i.square}


# --------------------------------------------------------------------------
# Landscape descriptors -- continuous, so the output space stays open
# --------------------------------------------------------------------------


class EvalLandscape(BaseModel):
    """Shape of the evaluation surface, not a category label.

    These are inputs to cost routing and to prose, never gates on content.
    """

    model_config = ConfigDict(frozen=True)

    best_cp: int
    legal_move_count: int
    playable_move_count: int = Field(description="Moves within the 'not a mistake' band")
    delta_to_second_cp: int | None
    top_move_entropy: float = Field(description="0 = one move stands alone, higher = many equals")
    eval_volatility_cp: float = Field(description="Stddev of best_cp across search depths")
    best_move_changes: int = Field(description="Times the engine changed its mind while deepening")
    forced_mate_in: int | None = None
    only_move_threshold_cp: int = 300

    @property
    def is_single_answer(self) -> bool:
        """One move and only one move. Covers mate-in-N and pure only-moves alike."""
        if self.forced_mate_in is not None:
            return True
        if self.legal_move_count == 1:
            return True
        return (
            self.delta_to_second_cp is not None
            and self.delta_to_second_cp >= self.only_move_threshold_cp
        )


# --------------------------------------------------------------------------
# Transfer keys
# --------------------------------------------------------------------------


class Zone(StrEnum):
    """Board region relative to the side that moved. Deliberately coarse -- exact
    squares do not transfer between positions, regions do."""

    OWN_QUEENSIDE = "own_qs"
    OWN_CENTER = "own_ctr"
    OWN_KINGSIDE = "own_ks"
    MID_QUEENSIDE = "mid_qs"
    MID_CENTER = "mid_ctr"
    MID_KINGSIDE = "mid_ks"
    OPP_QUEENSIDE = "opp_qs"
    OPP_CENTER = "opp_ctr"
    OPP_KINGSIDE = "opp_ks"


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    piece: str = Field(description="Uppercase piece letter, colour-independent")
    from_zone: Zone
    to_zone: Zone
    is_capture: bool
    is_check: bool

    def token(self) -> str:
        flags = ("x" if self.is_capture else "") + ("+" if self.is_check else "")
        return f"{self.piece}:{self.from_zone.value}>{self.to_zone.value}{flags}"


def plan_token_string(steps: Sequence[PlanStep]) -> str:
    return " ".join(step.token() for step in steps)


class PlanFingerprint(BaseModel):
    """What the engine *does* here, abstracted away from exact squares.

    This is the strongest transfer key in the system. Structural similarity says
    two positions look alike; this says they play alike, and only the second one
    reliably carries an explanation across.
    """

    model_config = ConfigDict(frozen=True)

    steps: list[PlanStep]
    digest: str

    @classmethod
    def from_steps(cls, steps: Sequence[PlanStep]) -> PlanFingerprint:
        tokens = plan_token_string(steps)
        digest = hashlib.blake2b(tokens.encode(), digest_size=_PLAN_DIGEST_BYTES).hexdigest()
        return cls(steps=list(steps), digest=digest)

    def token_string(self) -> str:
        return plan_token_string(self.steps)


# --------------------------------------------------------------------------
# Knowledge availability -- Chess960 falls out of this for free
# --------------------------------------------------------------------------


class KnowledgeTier(StrEnum):
    ENGINE = "engine"
    ABLATION = "ablation"
    PLAN_FINGERPRINT = "plan_fingerprint"
    STRUCTURAL_EMBEDDING = "structural_embedding"
    EMPIRICAL = "empirical"
    OPENING_NAME = "opening_name"
    LITERATURE = "literature"
    PRECEDENT = "precedent"


#: Tiers that are computed from the position itself and therefore always
#: available -- including in Chess960, where no theory exists.
INTRINSIC_TIERS: frozenset[KnowledgeTier] = frozenset(
    {
        KnowledgeTier.ENGINE,
        KnowledgeTier.ABLATION,
        KnowledgeTier.PLAN_FINGERPRINT,
        KnowledgeTier.STRUCTURAL_EMBEDDING,
    }
)


class KnowledgeAvailability(BaseModel):
    """Which evidence tiers are populated for this position.

    A missing tier must produce *fewer claims*, never an invented one. Chess960
    is the honest test of that: everything outside INTRINSIC_TIERS is empty and
    the explanation still has to stand up.
    """

    model_config = ConfigDict(frozen=True)

    available: frozenset[KnowledgeTier]

    @classmethod
    def intrinsic_only(cls) -> KnowledgeAvailability:
        return cls(available=INTRINSIC_TIERS)

    def has(self, tier: KnowledgeTier) -> bool:
        return tier in self.available

    @property
    def theory_applies(self) -> bool:
        return self.has(KnowledgeTier.OPENING_NAME) or self.has(KnowledgeTier.EMPIRICAL)


# --------------------------------------------------------------------------
