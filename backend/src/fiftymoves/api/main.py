from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import chess
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from fiftymoves.analysis.annotations import classify
from fiftymoves.analysis.book import (
    BOOK_MAX_CHILDREN,
    BOOK_MAX_PLY,
    BOOK_MIN_VOLUME,
    score_openings,
)
from fiftymoves.analysis.outcomes import measure_outcomes
from fiftymoves.api.auth import (
    AuthError,
    Principal,
    bearer_token,
    get_limiter,
    verify_token,
)
from fiftymoves.api.imports import ImportJob, read_job
from fiftymoves.cache import LruCache
from fiftymoves.config import EngineNotProvisioned, Settings, get_settings
from fiftymoves.domain.annotations import MoveQuality
from fiftymoves.domain.book import OpeningPhase
from fiftymoves.domain.explanations import Explanation
from fiftymoves.domain.games import GameRecord, Side
from fiftymoves.domain.graph import RepertoireGraph
from fiftymoves.domain.knowledge import KnowledgeView, PlanNeighbour
from fiftymoves.domain.outcomes import OutcomeReport
from fiftymoves.engine.stockfish import StockfishEngine
from fiftymoves.ingest.analysis_store import AnalysisStore
from fiftymoves.ingest.annotations_store import AnnotationStore, shape_key
from fiftymoves.ingest.explanation_store import ExplanationStore
from fiftymoves.ingest.graph import GameWalk, prune_walk, walk_games
from fiftymoves.ingest.knowledge_store import KnowledgeStore
from fiftymoves.ingest.loss_store import LossStore
from fiftymoves.ingest.oauth import LichessOAuth, OAuthError, PendingAuthorization
from fiftymoves.ingest.tokens import TokenStore, resolve_token
from fiftymoves.jobs.tasks import annotate_player, import_player, learn_positions
from fiftymoves.llm.explain import (
    Analysis,
    analyse_position,
    analysis_key,
    build_provider,
    cache_key,
    explain_mistake,
    explain_position,
    get_cache,
    get_studies,
    mistake_key,
    study_key,
    study_position,
)
from fiftymoves.llm.provider import ExplanationProvider, ProviderError
from fiftymoves.llm.tools import EngineProbe

app = FastAPI(title="FiftyMoves", version="0.1.0")

DEFAULT_SIDE = Query(default=Side.WHITE)
_pending: LruCache[PendingAuthorization] = LruCache(max_entries=16)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def spender(authorization: str | None = Header(default=None)) -> Principal:
    """Guards the endpoints that cost money.

    A press here is a model call on this account's bill, so it needs both a
    verified account and room under that account's daily ceiling. The free
    endpoints that read what has already been paid for stay open.
    """
    settings = get_settings()
    if not settings.auth_required:
        return Principal(subject="anonymous", username="anonymous")

    token = bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="sign in to run an analysis")

    try:
        principal = verify_token(token, settings=settings)
        get_limiter().charge(principal.subject)
    except AuthError as exc:
        status = 429 if "ceiling" in str(exc) else 401
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return principal


SPENDER = Depends(spender)


def data_dir(settings: Settings) -> Path:
    return settings.data_dir


def configured_provider(settings: Settings) -> ExplanationProvider:
    return build_provider(
        kind=settings.llm_provider,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_s,
        api_key=settings.anthropic_credentials(),
    )


def games_file(username: str) -> Path:
    return data_dir(get_settings()) / f"{username}.games.jsonl"


def import_stamp(username: str) -> int:
    path = games_file(username)
    return path.stat().st_mtime_ns if path.exists() else 0


@lru_cache(maxsize=2)
def load_games(username: str, directory: str, stamp: int) -> tuple[GameRecord, ...]:
    path = Path(directory) / f"{username}.games.jsonl"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no games imported for {username!r} yet",
        )
    with path.open(encoding="utf-8") as handle:
        return tuple(GameRecord(**json.loads(line)) for line in handle if line.strip())


def player_games(username: str) -> tuple[GameRecord, ...]:
    return load_games(username, str(data_dir(get_settings())), import_stamp(username))


_graphs: LruCache[RepertoireGraph] | None = None
_walks: LruCache[GameWalk] | None = None


def graph_cache() -> LruCache[RepertoireGraph]:
    global _graphs
    if _graphs is None:
        _graphs = LruCache[RepertoireGraph](get_settings().graph_cache_entries)
    return _graphs


def walk_cache() -> LruCache[GameWalk]:
    global _walks
    if _walks is None:
        _walks = LruCache[GameWalk](get_settings().walk_cache_entries)
    return _walks


def walk_for(username: str, *, side: Side, max_ply: int) -> GameWalk:
    walks = walk_cache()
    key = f"{username}:{import_stamp(username)}:{side.value}:{max_ply}"
    cached = walks.get(key)
    if cached is not None:
        return cached

    walk = walk_games(player_games(username), side=side, max_ply=max_ply)
    walks.put(key, walk)
    return walk


def graph_for(
    username: str, *, side: Side, max_ply: int, min_volume: int, max_children: int
) -> RepertoireGraph:
    settings = get_settings()
    graphs = graph_cache()
    key = f"{username}:{import_stamp(username)}:{side.value}:{max_ply}:{min_volume}:{max_children}"
    cached = graphs.get(key)
    if cached is not None:
        return cached

    graph = prune_walk(
        walk_for(username, side=side, max_ply=max_ply),
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )
    graphs.put(key, graph)
    return graph


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    engine: dict[str, Any]
    try:
        engine = {"available": True, "path": str(settings.resolve_engine_path())}
    except EngineNotProvisioned as exc:
        engine = {"available": False, "error": str(exc).splitlines()[0]}

    return {
        "status": "ok",
        "pipeline_version": settings.pipeline_version,
        "engine": engine,
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "credentials": settings.anthropic_credentials() is not None,
        },
    }


@app.post("/api/players/{username}/import", response_model=ImportJob, status_code=202)
def start_import(username: str, max_games: int | None = Query(default=None, ge=1)) -> ImportJob:
    handle = import_player.delay(username, max_games)
    return ImportJob(job_id=handle.id, username=username, state="queued")


@app.get("/api/imports/{job_id}", response_model=ImportJob)
def import_status(job_id: str, username: str = Query(default="")) -> ImportJob:
    handle = import_player.AsyncResult(job_id)
    return read_job(job_id, username, handle.state, handle.info)


@app.get("/api/auth/lichess")
def lichess_auth_status() -> dict[str, Any]:
    settings = get_settings()
    stored = TokenStore.from_settings(settings).read()
    return {
        "connected": resolve_token(settings) is not None,
        "source": "env" if settings.lichess_token else ("oauth" if stored else None),
        "username": stored.username if stored else None,
        "export_rate": 60 if resolve_token(settings) else 20,
    }


@app.post("/api/auth/lichess/start")
def lichess_auth_start() -> dict[str, str]:
    oauth = LichessOAuth.from_settings()
    try:
        url, pending = oauth.start()
    finally:
        oauth.close()
    _pending.put(pending.state, pending)
    return {"authorize_url": url, "state": pending.state}


@app.post("/api/auth/lichess/callback")
def lichess_auth_callback(code: str = Query(...), state: str = Query(...)) -> dict[str, Any]:
    pending = _pending.get(state)
    if pending is None:
        raise HTTPException(status_code=400, detail="that sign in attempt expired; start again")

    oauth = LichessOAuth.from_settings()
    try:
        token = oauth.exchange(code, pending)
    except OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        oauth.close()

    TokenStore.from_settings().write(token)
    return {"connected": True, "username": token.username, "export_rate": 60}


@app.delete("/api/auth/lichess", status_code=204)
def lichess_auth_disconnect() -> None:
    TokenStore.from_settings().clear()


@app.post("/api/players/{username}/knowledge", status_code=202)
def start_learning(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> dict[str, str]:
    handle = learn_positions.delay(username, side.value, max_ply, min_volume, max_children)
    return {"job_id": handle.id, "state": "queued"}


@app.get("/api/knowledge/{digest}", response_model=KnowledgeView)
def position_knowledge(
    digest: str, neighbours: int = Query(default=6, ge=0, le=40)
) -> KnowledgeView:
    settings = get_settings()
    store = KnowledgeStore(settings.data_dir)
    record = store.get(digest)
    if record is None:
        raise HTTPException(status_code=404, detail="this position has not been studied yet")

    steps, kin = store.sharing_prefix(record)
    return KnowledgeView(
        position=record,
        shares_plan_with=tuple(
            PlanNeighbour(digest=r.digest, epd=r.epd, best_san=r.best_san, best_cp=r.best_cp)
            for r in kin[:neighbours]
        ),
        plan_steps=steps,
    )


@app.get("/api/knowledge")
def knowledge_summary() -> dict[str, Any]:
    store = KnowledgeStore(get_settings().data_dir)
    plans = store.by_plan()
    shared = {p: rs for p, rs in plans.items() if len(rs) > 1}
    return {
        "positions": len(store),
        "distinct_plans": len(plans),
        "plans_shared_by_several_positions": len(shared),
        "path": str(store.path),
    }


@app.get("/api/players/{username}/annotations")
def player_annotations(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> dict[str, Any]:
    settings = get_settings()
    shape = shape_key(username, side, max_ply, min_volume, max_children, import_stamp(username))
    stored = AnnotationStore(settings.data_dir).read(username, shape)
    if stored is None:
        return {"state": "missing", "shape": shape}
    return {"state": "ready", "shape": shape, "annotations": stored.model_dump()}


@app.post("/api/players/{username}/annotations", status_code=202)
def start_annotation(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> dict[str, str]:
    handle = annotate_player.delay(username, side.value, max_ply, min_volume, max_children)
    return {"job_id": handle.id, "state": "queued"}


@app.get("/api/annotations/{job_id}")
def annotation_status(job_id: str) -> dict[str, Any]:
    handle = annotate_player.AsyncResult(job_id)
    return {"job_id": job_id, "state": handle.state, "info": handle.info}


@app.get("/api/players/{username}/graph", response_model=RepertoireGraph)
def player_graph(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> RepertoireGraph:
    return graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )


@app.get("/api/players/{username}/positions/{digest}/explanation")
def read_explanation(username: str, digest: str) -> dict[str, Any]:
    settings = get_settings()
    provider = configured_provider(settings)
    key = cache_key(digest, settings.pipeline_version, provider)

    cached = get_cache(settings.llm_cache_entries).get(key)
    if cached is None:
        cached = ExplanationStore(settings.data_dir).get(key)
        if cached is not None:
            get_cache(settings.llm_cache_entries).put(key, cached)

    if cached is None:
        return {"state": "missing", "model": provider.model}
    return {"state": "ready", "explanation": cached.model_dump()}


@app.post("/api/players/{username}/positions/{digest}/explanation", response_model=Explanation)
def write_explanation(
    username: str,
    digest: str,
    caller: Principal = SPENDER,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> Explanation:
    settings = get_settings()
    provider = configured_provider(settings)
    key = cache_key(digest, settings.pipeline_version, provider)
    store = ExplanationStore(settings.data_dir)

    existing = get_cache(settings.llm_cache_entries).get(key) or store.get(key)
    if existing is not None:
        return existing

    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )
    node = next((n for n in graph.nodes if n.digest == digest), None)
    if node is None:
        raise HTTPException(status_code=404, detail="position is not in this graph")

    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        raise HTTPException(status_code=503, detail=str(exc).splitlines()[0]) from exc

    board = chess.Board(f"{node.epd} 0 1")
    studies = get_studies(settings.llm_cache_entries)
    engine_key = study_key(
        digest, settings.pipeline_version, settings.explain_depth, settings.ablation_depth
    )
    study = studies.get(engine_key)

    engine = None
    try:
        if study is None or settings.llm_probe_enabled:
            engine = StockfishEngine(
                str(engine_path),
                threads=settings.engine_threads,
                hash_mb=settings.engine_hash_mb,
            )
        if study is None:
            study = study_position(
                board,
                engine=engine,  # type: ignore[arg-type]
                depth=settings.explain_depth,
                ablation_depth=settings.ablation_depth,
                multipv=settings.multipv,
            )
            studies.put(engine_key, study)

        probe = (
            EngineProbe(
                engine,
                board,
                depth=settings.llm_probe_depth,
                max_calls=settings.llm_probe_max_calls,
            )
            if engine is not None and settings.llm_probe_enabled
            else None
        )

        try:
            explanation = explain_position(
                board, provider=provider, digest=digest, study=study, probe=probe
            )
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if engine is not None:
            engine.close()

    get_cache(settings.llm_cache_entries).put(key, explanation)
    store.put(key, explanation)
    return explanation


@app.get("/api/players/{username}/move-losses")
def player_move_losses(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> dict[str, Any]:
    settings = get_settings()
    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )
    costs = LossStore(settings.data_dir)
    marks: list[dict[str, Any]] = []
    measured = 0
    for edge in graph.edges:
        if not edge.by_player:
            continue
        cost = costs.get(edge.parent)
        if cost is None:
            continue
        loss = cost.for_move(edge.uci)
        if loss is None:
            continue
        measured += 1
        quality = classify(loss)
        if quality is MoveQuality.SOUND:
            continue
        marks.append(
            {
                "parent": edge.parent,
                "child": edge.child,
                "uci": edge.uci,
                "san": edge.san,
                "quality": quality.value,
                "loss_cp": loss,
                "best_san": cost.best_san,
                "games": edge.games,
                "depth": cost.depth,
            }
        )
    marks.sort(key=lambda m: (-int(m["loss_cp"]), -int(m["games"])))
    return {"measured_moves": measured, "flagged": len(marks), "marks": marks}


@app.get("/api/players/{username}/outcomes", response_model=OutcomeReport)
def player_outcomes(username: str, side: Side = DEFAULT_SIDE) -> OutcomeReport:
    settings = get_settings()
    # How the flagged moves actually turn out is a fact about the player, so it is
    # measured on the whole repertoire rather than on whatever slice is on screen.
    graph = graph_for(
        username,
        side=side,
        max_ply=BOOK_MAX_PLY,
        min_volume=BOOK_MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    store = LossStore(settings.data_dir)
    held = {n.digest: r for n in graph.nodes if (r := store.get(n.digest)) is not None}
    return measure_outcomes(graph, held)


@app.get("/api/players/{username}/opening-phase", response_model=OpeningPhase)
def opening_phase(username: str, side: Side = DEFAULT_SIDE) -> OpeningPhase:
    settings = get_settings()
    # How far the book runs is a fact about the player, so it is measured on the
    # whole repertoire rather than on whatever slice is on screen.
    graph = graph_for(
        username,
        side=side,
        max_ply=BOOK_MAX_PLY,
        min_volume=BOOK_MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    store = LossStore(settings.data_dir)
    costs = {n.digest: c for n in graph.nodes if (c := store.get(n.digest)) is not None}
    return score_openings(graph, costs)


@app.get("/api/players/{username}/positions/{digest}/analysis")
def read_analysis(username: str, digest: str) -> dict[str, Any]:
    settings = get_settings()
    provider = configured_provider(settings)
    held = AnalysisStore(settings.data_dir).get(
        analysis_key(digest, settings.pipeline_version, provider)
    )
    if held is None:
        return {"state": "missing", "model": provider.model}
    return {"state": "ready", "analysis": held.model_dump(mode="json")}


@app.post("/api/players/{username}/positions/{digest}/analysis")
def build_analysis(
    username: str,
    digest: str,
    caller: Principal = SPENDER,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> Analysis:
    settings = get_settings()
    provider = configured_provider(settings)
    key = analysis_key(digest, settings.pipeline_version, provider)
    store = AnalysisStore(settings.data_dir)
    held = store.get(key)
    if held is not None:
        return held

    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )
    node = next((n for n in graph.nodes if n.digest == digest), None)
    if node is None:
        raise HTTPException(status_code=404, detail="position is not in this graph")
    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        raise HTTPException(status_code=503, detail=str(exc).splitlines()[0]) from exc

    board = chess.Board(f"{node.epd} 0 1")
    engine = StockfishEngine(
        str(engine_path), threads=settings.engine_threads, hash_mb=settings.engine_hash_mb
    )
    try:
        analysis = analyse_position(
            board,
            provider=provider,
            engine=engine,
            digest=digest,
            depth=settings.llm_probe_depth,
            max_calls=settings.llm_probe_max_calls,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        engine.close()

    store.put(key, analysis)
    return analysis


@app.post("/api/players/{username}/positions/{digest}/moves/{uci}/explanation")
def explain_move(username: str, digest: str, uci: str, caller: Principal = SPENDER) -> Explanation:
    settings = get_settings()
    provider = configured_provider(settings)
    costs = LossStore(settings.data_dir)
    cost = costs.get(digest)
    if cost is None:
        raise HTTPException(status_code=404, detail="this position has not been measured yet")
    if cost.for_move(uci) is None:
        raise HTTPException(status_code=404, detail="that move was not measured from this position")

    key = mistake_key(digest, uci, settings.pipeline_version, provider.name)
    store = ExplanationStore(settings.data_dir)
    held = get_cache(settings.llm_cache_entries).get(key) or store.get(key)
    if held is not None:
        return held

    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        raise HTTPException(status_code=503, detail=str(exc).splitlines()[0]) from exc

    board = chess.Board(f"{cost.epd} 0 1")
    engine = StockfishEngine(
        str(engine_path), threads=settings.engine_threads, hash_mb=settings.engine_hash_mb
    )
    try:
        explanation = explain_mistake(
            board, provider=provider, engine=engine, cost=cost, played_uci=uci
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        engine.close()

    get_cache(settings.llm_cache_entries).put(key, explanation)
    store.put(key, explanation)
    return explanation
