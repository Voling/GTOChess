from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types.beta import (
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
)
from pydantic import BaseModel

from fiftymoves.domain.explanations import Claim, Evidence

SYSTEM_PROMPT = """You explain chess positions for an assessment tool. A player is \
navigating a graph of the openings they actually play, and you describe the position \
they just landed on.

You are given a numbered list of facts. Every fact was measured: evaluations and lines \
come from Stockfish, sensitivity figures come from perturbing the board and re-searching, \
and repertoire figures come from the player's own games. You have no other information \
about this position and you must not supply any.

Rules:

1. Every claim you write must cite exactly one fact by its id. If you cannot ground a \
sentence in a supplied fact, do not write the sentence.
2. Never restate a fact verbatim. Say what it means for the player.
3. Rank by what the measurements say matters. If one move is forced, that is the story, \
and a positional observation about some other part of the board is not. If the sensitivity \
figures put a piece or a tempo at the top, that is what the position turns on.
4. Do not name openings, plans, or theory that the facts do not mention. Do not guess at \
history or at what is "known" or "standard". This tool also handles Chess960, where \
opening theory does not apply at all.
5. Do not coach, do not encourage, and do not hedge. State what is true.
6. Write 2 to 4 claims. One sentence each, plain and specific. No lists inside a claim.

The headline is a single clause naming what the position turns on, under 60 characters, \
with no trailing period."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence_id": {"type": "string"},
                },
                "required": ["text", "evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "claims"],
    "additionalProperties": False,
}


class PositionBrief(BaseModel):
    line: str
    side_to_move: str
    variant: str
    depth_ply: int


class Draft(BaseModel):
    headline: str
    claims: tuple[Claim, ...]


class ProviderError(RuntimeError):
    pass


Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")


def effort_level(value: str) -> Effort:
    if value not in EFFORT_LEVELS:
        raise ProviderError(f"unknown effort {value!r}; expected one of {', '.join(EFFORT_LEVELS)}")
    return value


class ExplanationProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    def explain(self, brief: PositionBrief, evidence: Sequence[Evidence]) -> Draft: ...


def render_request(brief: PositionBrief, evidence: Sequence[Evidence]) -> str:
    facts = "\n".join(f"[{e.id}] {e.statement}" for e in evidence)
    line = brief.line or "the starting position"
    return (
        f"Position: {line}\n"
        f"Variant: {brief.variant}. {brief.side_to_move} to move at ply {brief.depth_ply}.\n\n"
        f"Facts:\n{facts}\n\n"
        f"Explain what this position turns on."
    )


class DeterministicProvider:
    @property
    def name(self) -> str:
        return "deterministic"

    @property
    def model(self) -> str | None:
        return None

    def explain(self, brief: PositionBrief, evidence: Sequence[Evidence]) -> Draft:
        if not evidence:
            raise ProviderError("no evidence to explain")
        claims = tuple(Claim(text=e.statement, evidence_id=e.id) for e in evidence[:4])
        return Draft(headline="Measured facts for this position", claims=claims)


class AnthropicProvider:
    def __init__(
        self,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._effort = effort_level(effort)
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str | None:
        return self._model

    def explain(self, brief: PositionBrief, evidence: Sequence[Evidence]) -> Draft:
        system: list[BetaTextBlockParam] = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        output_config: BetaOutputConfigParam = {
            "effort": self._effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        }
        messages: list[BetaMessageParam] = [
            {"role": "user", "content": render_request(brief, evidence)}
        ]

        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                output_config=output_config,
                messages=messages,
            )
        except anthropic.APIError as exc:
            raise ProviderError(str(exc)) from exc

        if response.stop_reason == "refusal":
            raise ProviderError("the model declined to answer for this position")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("the model returned no text")
        return Draft.model_validate_json(text)
