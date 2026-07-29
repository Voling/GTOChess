from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import chess
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from fiftymoves.config import EngineNotProvisioned, Settings, get_settings
from fiftymoves.domain.explanations import Explanation
from fiftymoves.domain.games import GameRecord
from fiftymoves.domain.graph import RepertoireGraph
from fiftymoves.engine.stockfish import StockfishEngine
from fiftymoves.ingest.graph import build_graph
from fiftymoves.llm.explain import (
    build_provider,
    cache_key,
    explain_position,
    get_cache,
)
from fiftymoves.llm.provider import ProviderError

app = FastAPI(title="FiftyMoves", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def data_dir(settings: Settings) -> Path:
    return settings.data_dir


@lru_cache(maxsize=8)
def load_games(username: str, directory: str) -> tuple[GameRecord, ...]:
    path = Path(directory) / f"{username}.games.jsonl"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no import for {username!r}; run fiftymoves.tools.ingest_lichess first",
        )
    with path.open(encoding="utf-8") as handle:
        return tuple(GameRecord(**json.loads(line)) for line in handle if line.strip())


def player_games(username: str) -> tuple[GameRecord, ...]:
    return load_games(username, str(data_dir(get_settings())))


def graph_for(
    username: str, *, max_ply: int, min_volume: int, max_children: int
) -> RepertoireGraph:
    settings = get_settings()
    return build_graph(
        player_games(username),
        max_ply=max_ply,
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )


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


@app.get("/api/players/{username}/graph", response_model=RepertoireGraph)
def player_graph(
    username: str,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> RepertoireGraph:
    return graph_for(username, max_ply=max_ply, min_volume=min_volume, max_children=max_children)


@app.get("/api/players/{username}/positions/{digest}/explanation", response_model=Explanation)
def position_explanation(
    username: str,
    digest: str,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> Explanation:
    settings = get_settings()
    graph = graph_for(username, max_ply=max_ply, min_volume=min_volume, max_children=max_children)

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
    key = cache_key(digest, settings.pipeline_version, provider)
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

    engine = StockfishEngine(
        str(engine_path),
        threads=settings.engine_threads,
        hash_mb=settings.engine_hash_mb,
    )
    try:
        explanation = explain_position(
            board,
            engine=engine,
            provider=provider,
            digest=digest,
            node=node,
            family=family,
            continuations=continuations,
            depth=settings.explain_depth,
            ablation_depth=settings.ablation_depth,
            multipv=settings.multipv,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        engine.close()

    cache.put(key, explanation)
    return explanation
