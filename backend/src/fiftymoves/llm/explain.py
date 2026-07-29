from __future__ import annotations

from collections.abc import Sequence

import chess
from pydantic import BaseModel

from fiftymoves.analysis.landscape import compute_landscape
from fiftymoves.analysis.sensitivity import compute_sensitivity
from fiftymoves.cache import LruCache
from fiftymoves.domain.book import MoveCost
from fiftymoves.domain.explanations import Evidence, Explanation
from fiftymoves.domain.graph import GraphEdge, GraphNode
from fiftymoves.domain.models import (
    EngineReport,
    EvalLandscape,
    PositionAttribution,
    SensitivityReport,
)
from fiftymoves.domain.openings import OpeningFamily
from fiftymoves.engine.attribution import attribute
from fiftymoves.engine.protocol import EngineProvider
from fiftymoves.llm.facts import build_evidence
from fiftymoves.llm.mistakes import Mistake, build_mistake_evidence
from fiftymoves.llm.provider import (
    AnthropicProvider,
    DeterministicProvider,
    Draft,
    ExplanationProvider,
    Persona,
    PositionBrief,
)
from fiftymoves.llm.tools import EngineProbe


class PositionStudy(BaseModel):
    report: EngineReport
    sensitivity: SensitivityReport
    landscape: EvalLandscape
    attribution: PositionAttribution | None = None


ExplanationCache = LruCache[Explanation]
StudyCache = LruCache[PositionStudy]

_cache: LruCache[Explanation] | None = None
_studies: LruCache[PositionStudy] | None = None


def get_cache(max_entries: int = 512) -> LruCache[Explanation]:
    global _cache
    if _cache is None:
        _cache = LruCache[Explanation](max_entries)
    return _cache


def get_studies(max_entries: int = 512) -> LruCache[PositionStudy]:
    global _studies
    if _studies is None:
        _studies = LruCache[PositionStudy](max_entries)
    return _studies


def reset_cache() -> None:
    global _cache, _studies
    _cache = None
    _studies = None


def study_key(digest: str, pipeline_version: str, depth: int, ablation_depth: int) -> str:
    return f"{pipeline_version}:d{depth}:a{ablation_depth}:{digest}"


def cache_key(digest: str, pipeline_version: str, provider: ExplanationProvider) -> str:
    """Keyed by position alone, so one paid call serves every player who lands here."""
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


def study_position(
    board: chess.Board,
    *,
    engine: EngineProvider,
    depth: int = 18,
    ablation_depth: int = 12,
    multipv: int = 3,
    binary_path: str | None = None,
) -> PositionStudy:
    report = engine.analyse(board, depth=depth, multipv=multipv)
    # Stockfish will account for its own evaluation per piece if asked, which
    # costs one call and no search.
    attribution = attribute(binary_path, board) if binary_path else None
    return PositionStudy(
        report=report,
        sensitivity=compute_sensitivity(engine, board, baseline=report, depth=ablation_depth),
        landscape=compute_landscape(board, report),
        attribution=attribution,
    )


def explain_position(
    board: chess.Board,
    *,
    provider: ExplanationProvider,
    digest: str,
    engine: EngineProvider | None = None,
    node: GraphNode | None = None,
    family: OpeningFamily | None = None,
    continuations: Sequence[GraphEdge] = (),
    depth: int = 18,
    ablation_depth: int = 12,
    multipv: int = 3,
    study: PositionStudy | None = None,
    probe: EngineProbe | None = None,
) -> Explanation:
    if study is None:
        if engine is None:
            raise ValueError("explain_position needs either an engine or a prepared study")
        study = study_position(
            board, engine=engine, depth=depth, ablation_depth=ablation_depth, multipv=multipv
        )
    evidence = build_evidence(
        board,
        study.report,
        study.sensitivity,
        study.landscape,
        node=node,
        family=family,
        continuations=continuations,
        attribution=study.attribution,
    )
    draft = provider.explain(brief_for(board, node), evidence, probe)
    # Anything the engine answered mid-conversation is evidence too, so a claim
    # resting on a probe still has to cite something real.
    if probe is not None:
        evidence = [*evidence, *probe.evidence]
    return ground(digest, draft, evidence, provider)


def mistake_key(parent_digest: str, uci: str, pipeline_version: str, provider_name: str) -> str:
    return f"{pipeline_version}:{provider_name}:{parent_digest}:{uci}"


def explain_mistake(
    board: chess.Board,
    *,
    provider: ExplanationProvider,
    engine: EngineProvider,
    cost: MoveCost,
    played_uci: str,
    node: GraphNode | None = None,
    probe: EngineProbe | None = None,
) -> Explanation:
    """Why one move gave ground, keyed by the move rather than the position."""
    mistake = Mistake(board, played_uci, cost)
    evidence = build_mistake_evidence(engine, mistake, depth=cost.depth)
    draft = provider.explain(
        brief_for(board, node),
        evidence,
        probe,
        persona=Persona.MISTAKE,
        ask=f"Explain what went wrong with {mistake.played_san}.",
    )
    if probe is not None:
        evidence = [*evidence, *probe.evidence]
    return ground(f"{cost.digest}:{played_uci}", draft, evidence, provider)


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
