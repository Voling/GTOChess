from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Literal, Protocol, cast

import anthropic
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
)
from pydantic import BaseModel

from fiftymoves.domain.explanations import Claim, Evidence
from fiftymoves.llm.prompts import prompt
from fiftymoves.llm.tools import ProbeLimit

MAX_TURNS = 8

CACHE_CONTROL: BetaCacheControlEphemeralParam = {"type": "ephemeral"}


class Persona(StrEnum):
    POSITION = "position"
    MISTAKE = "mistake"
    ANALYST = "analyst"


class Toolbox(Protocol):
    @property
    def evidence(self) -> list[Evidence]: ...

    @property
    def schema(self) -> list[dict[str, Any]]: ...

    def dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def system_prompt(persona: Persona, *, probing: bool) -> str:
    text = prompt(persona.value)
    if not probing:
        return text
    manual = "board" if persona is Persona.ANALYST else "probe"
    return "\n\n".join((text, prompt(manual)))


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

    def explain(
        self,
        brief: PositionBrief,
        evidence: Sequence[Evidence],
        probe: Toolbox | None = None,
        persona: Persona = Persona.POSITION,
        ask: str | None = None,
    ) -> Draft: ...


def move_cache_breakpoint(messages: list[BetaMessageParam]) -> None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                block.pop("cache_control", None)
    tail = messages[-1].get("content")
    if isinstance(tail, list) and tail and isinstance(tail[-1], dict):
        tail[-1]["cache_control"] = dict(CACHE_CONTROL)


def render_request(
    brief: PositionBrief, evidence: Sequence[Evidence], ask: str | None = None
) -> str:
    facts = "\n".join(f"[{e.id}] {e.statement}" for e in evidence)
    line = brief.line or "the starting position"
    return (
        f"Position: {line}\n"
        f"Variant: {brief.variant}. {brief.side_to_move} to move at ply {brief.depth_ply}.\n\n"
        f"Facts:\n{facts}\n\n"
        f"{ask or 'Explain what this position turns on.'}"
    )


class DeterministicProvider:
    @property
    def name(self) -> str:
        return "deterministic"

    @property
    def model(self) -> str | None:
        return None

    def explain(
        self,
        brief: PositionBrief,
        evidence: Sequence[Evidence],
        probe: Toolbox | None = None,
        persona: Persona = Persona.POSITION,
        ask: str | None = None,
    ) -> Draft:
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

    def explain(
        self,
        brief: PositionBrief,
        evidence: Sequence[Evidence],
        probe: Toolbox | None = None,
        persona: Persona = Persona.POSITION,
        ask: str | None = None,
    ) -> Draft:
        system: list[BetaTextBlockParam] = [
            {
                "type": "text",
                "text": system_prompt(persona, probing=probe is not None),
                "cache_control": CACHE_CONTROL,
            }
        ]
        output_config: BetaOutputConfigParam = {
            "effort": self._effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        }
        messages: list[BetaMessageParam] = [
            {"role": "user", "content": render_request(brief, evidence, ask)}
        ]
        extra: dict[str, Any] = {"tools": probe.schema} if probe else {}

        for _ in range(MAX_TURNS):
            try:
                response = self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    system=system,
                    output_config=output_config,
                    messages=messages,
                    **extra,
                )
            except anthropic.APIError as exc:
                raise ProviderError(str(exc)) from exc

            if response.stop_reason == "refusal":
                raise ProviderError("the model declined to answer for this position")

            if response.stop_reason != "tool_use" or probe is None:
                text = next((b.text for b in response.content if b.type == "text"), None)
                if text is None:
                    raise ProviderError("the model returned no text")
                return Draft.model_validate_json(text)

            messages.append(
                cast(BetaMessageParam, {"role": "assistant", "content": response.content})
            )
            results: list[Any] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                payload = block.input if isinstance(block.input, dict) else {}
                try:
                    answer: Any = probe.dispatch(block.name, payload)
                    failed = False
                except ProbeLimit as exc:
                    answer = {"error": str(exc)}
                    failed = True
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(answer),
                        "is_error": failed,
                    }
                )
            messages.append(cast(BetaMessageParam, {"role": "user", "content": results}))
            move_cache_breakpoint(messages)

        raise ProviderError("the model kept asking the engine without answering")
