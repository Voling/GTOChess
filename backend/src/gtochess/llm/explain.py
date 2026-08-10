from __future__ import annotations

import hashlib
from collections.abc import Sequence

import chess
from pydantic import BaseModel

from gtochess.analysis.landscape import compute_landscape
from gtochess.analysis.sensitivity import compute_sensitivity
from gtochess.cache import LruCache
from gtochess.domain.book import PositionLosses
from gtochess.domain.explanations import Evidence, Explanation
from gtochess.domain.graph import GraphEdge, GraphNode
from gtochess.domain.models import (
    EngineReport,
    EvalLandscape,
    PositionAttribution,
    SensitivityReport,
)
from gtochess.domain.openings import OpeningFamily
from gtochess.domain.storyboard import Storyboard
from gtochess.engine.attribution import attribute
from gtochess.engine.protocol import EngineProvider
from gtochess.ingest.knowledge_store import KnowledgeStore
from gtochess.llm.board import BoardSession
from gtochess.llm.facts import build_evidence, plan_principles
from gtochess.llm.mistakes import Mistake, build_mistake_evidence
from gtochess.llm.provider import (
    AnthropicProvider,
    DeterministicProvider,
    Draft,
    ExplanationProvider,
    Persona,
    PositionBrief,
)
from gtochess.llm.tools import EngineProbe


class PositionStudy(BaseModel):
    report: EngineReport
    sensitivity: SensitivityReport
    landscape: EvalLandscape
    attribution: PositionAttribution | None = None


class PersonalContext(BaseModel):
    node: GraphNode
    family: OpeningFamily | None = None
    continuations: tuple[GraphEdge, ...] = ()

    def fingerprint(self) -> str:
        parts = [
            self.node.digest,
            "/".join(self.node.san_path),
            str(self.node.games),
            f"{self.node.score:.4f}",
            self.family.key if self.family else "",
            ",".join(f"{e.uci}:{e.games}" for e in self.continuations),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


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


def cache_key(
    digest: str,
    pipeline_version: str,
    provider: ExplanationProvider,
    personal: PersonalContext | None = None,
    knowledge: KnowledgeStore | None = None,
) -> str:
    key = f"{pipeline_version}:{provider.name}:{provider.model or 'none'}:{digest}"
    if knowledge is not None:
        key = f"{key}:{knowledge_stamp(knowledge)}"
    return key if personal is None else f"{key}:{personal.fingerprint()}"


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
    attribution = attribute(binary_path, board) if binary_path else None
    return PositionStudy(
        report=report,
        sensitivity=compute_sensitivity(engine, board, baseline=report, depth=ablation_depth),
        landscape=compute_landscape(board, report),
        attribution=attribution,
    )


def knowledge_stamp(knowledge: KnowledgeStore | None) -> str:
    """How much has been learned, folded into the cache key.

    Principles are read live from the store, so an explanation generated when
    three positions shared a plan would otherwise keep claiming three forever,
    and a position studied after its explanation would never gain any.
    """
    return "k0" if knowledge is None else f"k{len(knowledge)}"


def transferable(digest: str, knowledge: KnowledgeStore | None) -> list[Evidence]:
    """General ideas that survived being measured somewhere else.

    Keyed by position and shared, like the rest of the evidence, so this stays
    outside the personal cache split.
    """
    if knowledge is None:
        return []
    held = knowledge.get(digest)
    if held is None:
        return []
    steps, neighbours = knowledge.sharing_prefix(held)
    return plan_principles(held, steps, neighbours)


def explain_position(
    board: chess.Board,
    *,
    provider: ExplanationProvider,
    digest: str,
    engine: EngineProvider | None = None,
    personal: PersonalContext | None = None,
    shared: bool = True,
    depth: int = 18,
    ablation_depth: int = 12,
    multipv: int = 3,
    study: PositionStudy | None = None,
    probe: EngineProbe | None = None,
    knowledge: KnowledgeStore | None = None,
) -> Explanation:
    if personal is not None and shared:
        raise ValueError(
            "a shared explanation is keyed by position alone, so it cannot carry one "
            "player's counts. Pass shared=False and key it with cache_key(..., personal)."
        )
    if study is None:
        if engine is None:
            raise ValueError("explain_position needs either an engine or a prepared study")
        study = study_position(
            board, engine=engine, depth=depth, ablation_depth=ablation_depth, multipv=multipv
        )
    node = personal.node if personal else None
    evidence = build_evidence(
        board,
        study.report,
        study.sensitivity,
        study.landscape,
        node=node,
        family=personal.family if personal else None,
        continuations=personal.continuations if personal else (),
        attribution=study.attribution,
        principles=transferable(digest, knowledge),
    )
    draft = provider.explain(brief_for(board, node), evidence, probe)
    if probe is not None:
        evidence = [*evidence, *probe.evidence]
    return ground(digest, draft, evidence, provider)


class Analysis(BaseModel):
    explanation: Explanation
    storyboard: Storyboard


def analysis_key(digest: str, pipeline_version: str, provider: ExplanationProvider) -> str:
    return f"{pipeline_version}:board:{provider.name}:{provider.model or 'none'}:{digest}"


def analyse_position(
    board: chess.Board,
    *,
    provider: ExplanationProvider,
    engine: EngineProvider,
    digest: str,
    node: GraphNode | None = None,
    depth: int = 14,
    max_calls: int = 8,
    seed: Sequence[Evidence] = (),
    ask: str | None = None,
) -> Analysis:
    session = BoardSession(engine, board, depth=depth, max_calls=max_calls)
    brief = brief_for(board, node)
    request = ask or "Show what this position turns on, on the board."
    draft = provider.explain(brief, seed, session, persona=Persona.ANALYST, ask=request)

    if session.scenes == 0 and session.unshown:
        draft = provider.explain(
            brief,
            [*seed, *session.evidence],
            session,
            persona=Persona.ANALYST,
            ask=(
                f"{request} You walked "
                f"{', '.join(session.unshown)} but put none of them on the board. "
                "Call show_line on the one that carries the point, with a note per "
                "move, then answer."
            ),
        )
    if session.scenes == 0:
        session.show_longest_walk()

    evidence = [*seed, *session.evidence]
    return Analysis(
        explanation=ground(digest, draft, evidence, provider),
        storyboard=session.storyboard(),
    )


def mistake_key(parent_digest: str, uci: str, pipeline_version: str, provider_name: str) -> str:
    return f"{pipeline_version}:{provider_name}:{parent_digest}:{uci}"


def explain_mistake(
    board: chess.Board,
    *,
    provider: ExplanationProvider,
    engine: EngineProvider,
    cost: PositionLosses,
    played_uci: str,
    node: GraphNode | None = None,
    probe: EngineProbe | None = None,
) -> Explanation:
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
