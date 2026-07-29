from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import chess
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from fiftymoves.api.imports import ImportJob, read_job
from fiftymoves.cache import LruCache
from fiftymoves.config import EngineNotProvisioned, Settings, get_settings
from fiftymoves.domain.explanations import Explanation
from fiftymoves.domain.games import GameRecord, Side
from fiftymoves.domain.graph import RepertoireGraph
from fiftymoves.engine.stockfish import StockfishEngine
from fiftymoves.ingest.graph import build_graph
from fiftymoves.ingest.oauth import LichessOAuth, OAuthError, PendingAuthorization
from fiftymoves.ingest.tokens import TokenStore, resolve_token
from fiftymoves.jobs.tasks import import_player
from fiftymoves.llm.explain import (
    build_provider,
    cache_key,
    explain_position,
    get_cache,
    get_studies,
    study_key,
    study_position,
)
from fiftymoves.llm.provider import ProviderError

app = FastAPI(title="FiftyMoves", version="0.1.0")

DEFAULT_SIDE = Query(default=Side.WHITE)
_pending: LruCache[PendingAuthorization] = LruCache(max_entries=16)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def data_dir(settings: Settings) -> Path:
    return settings.data_dir


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
    # The stamp is part of the key so a worker rewriting the file busts the cache.
    return load_games(username, str(data_dir(get_settings())), import_stamp(username))


_graphs: LruCache[RepertoireGraph] | None = None


def graph_cache() -> LruCache[RepertoireGraph]:
    global _graphs
    if _graphs is None:
        _graphs = LruCache[RepertoireGraph](get_settings().graph_cache_entries)
    return _graphs


def graph_for(
    username: str, *, side: Side, max_ply: int, min_volume: int, max_children: int
) -> RepertoireGraph:
    settings = get_settings()
    graphs = graph_cache()
    key = f"{username}:{import_stamp(username)}:{side.value}:{max_ply}:{min_volume}:{max_children}"
    cached = graphs.get(key)
    if cached is not None:
        return cached

    graph = build_graph(
        player_games(username),
        side=side,
        max_ply=max_ply,
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


@app.get("/api/players/{username}/positions/{digest}/explanation", response_model=Explanation)
def position_explanation(
    username: str,
    digest: str,
    side: Side = DEFAULT_SIDE,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> Explanation:
    settings = get_settings()
    graph = graph_for(
        username, side=side, max_ply=max_ply, min_volume=min_volume, max_children=max_children
    )

    node = next((n for n in graph.nodes if n.digest == digest), None)
    if node is None:
        raise HTTPException(status_code=404, detail="position is not in this graph")

    provider = build_provider(
        kind=settings.llm_provider,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_s,
        api_key=settings.anthropic_credentials(),
    )
    cache = get_cache(settings.llm_cache_entries)
    key = cache_key(digest, settings.pipeline_version, provider, side)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        raise HTTPException(status_code=503, detail=str(exc).splitlines()[0]) from exc

    board = chess.Board(f"{node.epd} 0 1")
    family = next((f for f in graph.families if f.key == node.family), None)
    continuations = sorted((e for e in graph.edges if e.parent == digest), key=lambda e: -e.games)

    studies = get_studies(settings.llm_cache_entries)
    engine_key = study_key(
        digest, settings.pipeline_version, settings.explain_depth, settings.ablation_depth
    )
    study = studies.get(engine_key)

    engine = None
    try:
        if study is None:
            engine = StockfishEngine(
                str(engine_path),
                threads=settings.engine_threads,
                hash_mb=settings.engine_hash_mb,
            )
            study = study_position(
                board,
                engine=engine,
                depth=settings.explain_depth,
                ablation_depth=settings.ablation_depth,
                multipv=settings.multipv,
            )
            studies.put(engine_key, study)

        explanation = explain_position(
            board,
            provider=provider,
            digest=digest,
            node=node,
            family=family,
            continuations=continuations,
            study=study,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if engine is not None:
            engine.close()

    cache.put(key, explanation)
    return explanation
