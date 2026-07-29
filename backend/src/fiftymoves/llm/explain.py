from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

import chess

from fiftymoves.analysis.landscape import compute_landscape
from fiftymoves.analysis.sensitivity import compute_sensitivity
from fiftymoves.domain.explanations import Evidence, Explanation
from fiftymoves.domain.graph import GraphEdge, GraphNode
from fiftymoves.domain.openings import OpeningFamily
from fiftymoves.engine.protocol import EngineProvider
from fiftymoves.llm.facts import build_evidence
from fiftymoves.llm.provider import (
    AnthropicProvider,
    DeterministicProvider,
    Draft,
    ExplanationProvider,
    PositionBrief,
)


class ExplanationCache:
    def __init__(self, max_entries: int = 512) -> None:
        self._entries: OrderedDict[str, Explanation] = OrderedDict()
        self._max_entries = max_entries

    def get(self, key: str) -> Explanation | None:
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: Explanation) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


_cache: ExplanationCache | None = None


def get_cache(max_entries: int = 512) -> ExplanationCache:
    global _cache
    if _cache is None:
        _cache = ExplanationCache(max_entries)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def cache_key(digest: str, pipeline_version: str, provider: ExplanationProvider) -> str:
    return f"{pipeline_version}:{provider.name}:{provider.model or 'none'}:{digest}"


def ground(
    digest: str,
    draft: Draft,
    evidence: Sequence[Evidence],
    provider: ExplanationProvider,
) -> Explanation:
    known = {e.id for e in evidence}
    kept = tuple(claim for claim in draft.claims if claim.evidence_id in known)
    return Explanation(
        digest=digest,
        headline=draft.headline,
        claims=kept,
        evidence=tuple(evidence),
        source=provider.name,
        model=provider.model,
        dropped_claims=len(draft.claims) - len(kept),
    )


def brief_for(board: chess.Board, node: GraphNode | None) -> PositionBrief:
    return PositionBrief(
        line=" ".join(node.san_path) if node else "",
        side_to_move="White" if board.turn == chess.WHITE else "Black",
        variant="Chess960" if board.chess960 else "standard",
        depth_ply=node.depth_ply if node else 0,
    )


def explain_position(
    board: chess.Board,
    *,
    engine: EngineProvider,
    provider: ExplanationProvider,
    digest: str,
    node: GraphNode | None = None,
    family: OpeningFamily | None = None,
    continuations: Sequence[GraphEdge] = (),
    depth: int = 18,
    ablation_depth: int = 12,
    multipv: int = 3,
) -> Explanation:
    report = engine.analyse(board, depth=depth, multipv=multipv)
    sensitivity = compute_sensitivity(engine, board, baseline=report, depth=ablation_depth)
    landscape = compute_landscape(board, report)
    evidence = build_evidence(
        board,
        report,
        sensitivity,
        landscape,
        node=node,
        family=family,
        continuations=continuations,
    )
    return ground(digest, provider.explain(brief_for(board, node), evidence), evidence, provider)


def build_provider(
    *,
    kind: str,
    model: str,
    effort: str,
    max_tokens: int,
    timeout: float,
    api_key: str | None,
) -> ExplanationProvider:
    if kind == "deterministic":
        return DeterministicProvider()
    if kind == "anthropic" or (kind == "auto" and api_key):
        return AnthropicProvider(
            model=model,
            effort=effort,
            max_tokens=max_tokens,
            api_key=api_key,
            timeout=timeout,
        )
    return DeterministicProvider()
