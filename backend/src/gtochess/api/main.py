from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from typing import Any
from urllib.parse import unquote

import chess
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gtochess.analysis.annotations import classify
from gtochess.analysis.book import (
    BOOK_MAX_CHILDREN,
    BOOK_MAX_PLY,
    BOOK_MIN_VOLUME,
    score_openings,
)
from gtochess.analysis.outcomes import measure_outcomes
from gtochess.api.auth import (
    AuthError,
    Principal,
    authorize,
    get_limiter,
    guards,
    readable_player,
    status_for,
)
from gtochess.api.imports import ImportJob, read_job
from gtochess.cache import LruCache
from gtochess.config import EngineNotProvisioned, Settings, get_settings
from gtochess.domain.accounts import Account, LichessLink
from gtochess.domain.annotations import MoveQuality
from gtochess.domain.book import OpeningPhase
from gtochess.domain.explanations import Explanation
from gtochess.domain.games import GameRecord, Side
from gtochess.domain.graph import RepertoireGraph
from gtochess.domain.knowledge import KnowledgeView, PlanNeighbour
from gtochess.domain.outcomes import OutcomeReport
from gtochess.engine.stockfish import StockfishEngine
from gtochess.ingest.account_store import AccountStore
from gtochess.ingest.analysis_store import AnalysisStore
from gtochess.ingest.annotations_store import AnnotationStore, shape_key
from gtochess.ingest.explanation_store import ExplanationStore
from gtochess.ingest.graph import GameWalk, prune_walk, walk_games
from gtochess.ingest.knowledge_store import KnowledgeStore
from gtochess.ingest.loss_store import LossStore
from gtochess.ingest.oauth import LichessOAuth, OAuthError, PendingStore
from gtochess.ingest.pipeline import games_name, player_key
from gtochess.jobs.tasks import annotate_player, import_player, learn_positions
from gtochess.llm.explain import (
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
from gtochess.llm.provider import ExplanationProvider, ProviderError
from gtochess.llm.tools import EngineProbe
from gtochess.shared import get_shared
from gtochess.storage import StorageError, get_storage

app = FastAPI(title="GTO Chess", version="0.1.0")

DEFAULT_SIDE = Query(default=Side.WHITE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# One gate rather than a dependency on twenty routes, so a new endpoint is
# closed by default instead of open until somebody remembers.
PLAYER_PATH = re.compile(r"^/api/players/([^/]+)/")

# Short lived on purpose. The cache is per process and the API scales out, so a
# link made on one task has to become visible on the others. Without an expiry
# the second task keeps refusing the caller until its entry happens to be
# evicted, which with a handful of accounts is never.
ACCOUNT_TTL_S = 30.0
ACCOUNT_CACHE_MAX = 256

MISSING = "missing"

# The value is None when the subject has no record. A signed in user who has not
# linked lichess is the busiest caller there is, and without holding that answer
# every one of their requests was an object read before being refused.
_accounts: dict[str, tuple[float, Account | None]] = {}


def cached_account(subject: str) -> Account | None | str:
    """The held answer, or MISSING when nothing is held. None means no record."""
    held = _accounts.get(subject)
    if held is None:
        return MISSING
    stamped, account = held
    if time.monotonic() - stamped > ACCOUNT_TTL_S:
        _accounts.pop(subject, None)
        return MISSING
    return account


def hold_account(subject: str, account: Account | None) -> Account | None:
    if len(_accounts) >= ACCOUNT_CACHE_MAX:
        _accounts.clear()
    _accounts[subject] = (time.monotonic(), account)
    return account


def forget_accounts() -> None:
    _accounts.clear()


def account_for(principal: Principal) -> Account | None:
    """The caller's record, read only.

    Deliberately does not create one. This runs on the authorization path of
    every request, and a read must not depend on a write succeeding: a missing
    record and an unlinked record mean the same thing to `readable_player`.
    """
    held = cached_account(principal.subject)
    if held is not MISSING:
        return held  # type: ignore[return-value]
    return hold_account(principal.subject, AccountStore(get_storage()).get(principal.subject))


def ensure_account(principal: Principal) -> Account:
    """The caller's record, created if absent. Only where a write is expected."""
    held = cached_account(principal.subject)
    if isinstance(held, Account):
        return held
    made = AccountStore(get_storage()).ensure(principal.subject, email=principal.email)
    hold_account(principal.subject, made)
    return made


def remember_account(account: Account) -> Account:
    AccountStore(get_storage()).upsert(account)
    hold_account(account.subject, account)
    return account


@app.middleware("http")
async def require_account(request: Request, call_next: Any) -> Response:
    path = request.url.path
    if guards(path) and request.method != "OPTIONS":
        settings = get_settings()
        try:
            principal = authorize(request.headers.get("authorization"), settings)
            # Verifying the token says who is asking. This says whose games they
            # get, which is a separate question and the one that was missing.
            named = PLAYER_PATH.match(path)
            if named and settings.auth_required:
                readable_player(account_for(principal), unquote(named.group(1)), settings)
        except AuthError as exc:
            return JSONResponse(status_code=status_for(exc), content={"detail": str(exc)})
        except StorageError:
            # The account could not be read, so we cannot say this caller may
            # proceed. Refusing beats serving somebody else's repertoire.
            return JSONResponse(
                status_code=503, content={"detail": "accounts are unreadable right now"}
            )
    response: Response = await call_next(request)
    return response


def spender(authorization: str | None = Header(default=None)) -> Principal:
    """Guards the endpoints that spend.

    The middleware above already established there is an account. This is the
    daily ceiling on top, because a press here is a model call on our bill.
    """
    try:
        return authorize(authorization, get_settings(), charge=True)
    except AuthError as exc:
        raise HTTPException(status_code=status_for(exc), detail=str(exc)) from exc


def caller(authorization: str | None = Header(default=None)) -> Principal:
    """Identity, without the daily charge that SPENDER adds."""
    try:
        return authorize(authorization, get_settings())
    except AuthError as exc:
        raise HTTPException(status_code=status_for(exc), detail=str(exc)) from exc


SPENDER = Depends(spender)
CALLER = Depends(caller)


@app.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    """What the browser needs to start a sign in. Deliberately unauthenticated."""
    settings = get_settings()
    return {
        "required": settings.auth_required,
        "domain": settings.cognito_domain,
        "client_id": settings.cognito_client_id,
        "region": settings.cognito_region,
    }


def configured_provider(settings: Settings) -> ExplanationProvider:
    return build_provider(
        kind=settings.llm_provider,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_s,
        api_key=settings.anthropic_credentials(),
    )


def import_stamp(username: str) -> int:
    return get_storage().stamp(games_name(username))


@lru_cache(maxsize=2)
def load_games(username: str, stamp: int) -> tuple[GameRecord, ...]:
    store = get_storage()
    name = games_name(username)
    if not store.exists(name):
        raise HTTPException(
            status_code=404,
            detail=f"no games imported for {username!r} yet",
        )
    return tuple(GameRecord(**json.loads(line)) for line in store.lines(name))


def player_games(username: str) -> tuple[GameRecord, ...]:
    return load_games(player_key(username), import_stamp(username))


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
    key = f"{player_key(username)}:{import_stamp(username)}:{side.value}:{max_ply}"
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
    key = (
        f"{player_key(username)}:{import_stamp(username)}:"
        f"{side.value}:{max_ply}:{min_volume}:{max_children}"
    )
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
        "shared_state": get_shared().label,
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "credentials": settings.anthropic_credentials() is not None,
        },
    }


@app.post("/api/players/{username}/import", response_model=ImportJob, status_code=202)
def start_import(
    username: str,
    max_games: int | None = Query(default=None, ge=1),
    who: Principal = CALLER,
) -> ImportJob:
    handle = import_player.delay(username, max_games, who.subject)
    return ImportJob(job_id=handle.id, username=username, state="queued")


@app.get("/api/imports/{job_id}", response_model=ImportJob)
def import_status(job_id: str, username: str = Query(default="")) -> ImportJob:
    handle = import_player.AsyncResult(job_id)
    return read_job(job_id, username, handle.state, handle.info)


@app.get("/api/auth/lichess")
def lichess_auth_status(who: Principal = CALLER) -> dict[str, Any]:
    settings = get_settings()
    held = account_for(who)
    link = held.lichess if held else None
    mine = link.usable_token if link else None
    connected = bool(mine or settings.lichess_token)
    return {
        "connected": connected,
        "source": "oauth" if mine else ("env" if settings.lichess_token else None),
        "username": link.username if link else None,
        "export_rate": 60 if connected else 20,
    }


def pending_store() -> PendingStore:
    return PendingStore(get_shared(), ttl_s=get_settings().lichess_pending_ttl_s)


@app.post("/api/auth/lichess/start")
def lichess_auth_start() -> dict[str, str]:
    oauth = LichessOAuth.from_settings()
    try:
        url, pending = oauth.start()
    finally:
        oauth.close()
    pending_store().hold(pending)
    return {"authorize_url": url, "state": pending.state}


@app.post("/api/auth/lichess/callback")
def lichess_auth_callback(
    code: str = Query(...), state: str = Query(...), who: Principal = CALLER
) -> dict[str, Any]:
    pending = pending_store().take(state)
    if pending is None:
        raise HTTPException(status_code=400, detail="that sign in attempt expired; start again")

    oauth = LichessOAuth.from_settings()
    try:
        token = oauth.exchange(code, pending)
    except OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        oauth.close()

    if not token.username:
        raise HTTPException(status_code=502, detail="lichess did not say which account that was")

    link = LichessLink(
        username=token.username,
        access_token=token.access_token,
        token_type=token.token_type,
        expires_at=token.expires_at,
    )
    remember_account(ensure_account(who).linked_to(link))
    return {"connected": True, "username": token.username, "export_rate": 60}


@app.delete("/api/auth/lichess", status_code=204)
def lichess_auth_disconnect(who: Principal = CALLER) -> None:
    remember_account(ensure_account(who).unlinked())


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
    store = KnowledgeStore(get_storage())
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
    store = KnowledgeStore(get_storage())
    plans = store.by_plan()
    shared = {p: rs for p, rs in plans.items() if len(rs) > 1}
    return {
        "positions": len(store),
        "distinct_plans": len(plans),
        "plans_shared_by_several_positions": len(shared),
    }


@app.get("/api/players/{username}/annotations")
def player_annotations(
    username: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> dict[str, Any]:
    shape = shape_key(username, side, max_ply, min_volume, max_children, import_stamp(username))
    stored = AnnotationStore(get_storage()).read(username, shape)
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
    knowledge = KnowledgeStore(get_storage())
    key = cache_key(digest, settings.pipeline_version, provider, knowledge=knowledge)

    cached = get_cache(settings.llm_cache_entries).get(key)
    if cached is None:
        cached = ExplanationStore(get_storage()).get(key)
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
    knowledge = KnowledgeStore(get_storage())
    key = cache_key(digest, settings.pipeline_version, provider, knowledge=knowledge)
    store = ExplanationStore(get_storage())

    # SPENDER charged before this ran, so anything that returns without a model
    # call gives it back. Otherwise a cache hit and a typo both cost a caller one
    # of their ten analyses for the day.
    existing = get_cache(settings.llm_cache_entries).get(key) or store.get(key)
    if existing is not None:
        get_limiter().refund(caller.subject)
        return existing

    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )
    node = next((n for n in graph.nodes if n.digest == digest), None)
    if node is None:
        get_limiter().refund(caller.subject)
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
                board,
                provider=provider,
                digest=digest,
                study=study,
                probe=probe,
                knowledge=knowledge,
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
    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )
    costs = LossStore(get_storage())
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
    # How the flagged moves actually turn out is a fact about the player, so it is
    # measured on the whole repertoire rather than on whatever slice is on screen.
    graph = graph_for(
        username,
        side=side,
        max_ply=BOOK_MAX_PLY,
        min_volume=BOOK_MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    store = LossStore(get_storage())
    held = {n.digest: r for n in graph.nodes if (r := store.get(n.digest)) is not None}
    return measure_outcomes(graph, held)


@app.get("/api/players/{username}/opening-phase", response_model=OpeningPhase)
def opening_phase(username: str, side: Side = DEFAULT_SIDE) -> OpeningPhase:
    # How far the book runs is a fact about the player, so it is measured on the
    # whole repertoire rather than on whatever slice is on screen.
    graph = graph_for(
        username,
        side=side,
        max_ply=BOOK_MAX_PLY,
        min_volume=BOOK_MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    store = LossStore(get_storage())
    costs = {n.digest: c for n in graph.nodes if (c := store.get(n.digest)) is not None}
    return score_openings(graph, costs)


@app.get("/api/players/{username}/positions/{digest}/analysis")
def read_analysis(username: str, digest: str) -> dict[str, Any]:
    settings = get_settings()
    provider = configured_provider(settings)
    held = AnalysisStore(get_storage()).get(
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
    store = AnalysisStore(get_storage())
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
    costs = LossStore(get_storage())
    cost = costs.get(digest)
    if cost is None:
        raise HTTPException(status_code=404, detail="this position has not been measured yet")
    if cost.for_move(uci) is None:
        raise HTTPException(status_code=404, detail="that move was not measured from this position")

    key = mistake_key(digest, uci, settings.pipeline_version, provider.name)
    store = ExplanationStore(get_storage())
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
