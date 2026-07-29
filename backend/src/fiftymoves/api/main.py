from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from fiftymoves.config import EngineNotProvisioned, get_settings
from fiftymoves.domain.games import GameRecord
from fiftymoves.domain.graph import RepertoireGraph
from fiftymoves.ingest.graph import build_graph

app = FastAPI(title="FiftyMoves", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@lru_cache(maxsize=8)
def load_games(username: str) -> tuple[GameRecord, ...]:
    path = Path("data") / f"{username}.games.jsonl"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no import for {username!r}; run fiftymoves.tools.ingest_lichess first",
        )
    with path.open(encoding="utf-8") as handle:
        return tuple(GameRecord(**json.loads(line)) for line in handle if line.strip())


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
    }


@app.get("/api/players/{username}/graph", response_model=RepertoireGraph)
def player_graph(
    username: str,
    max_ply: int = Query(default=12, ge=1, le=40),
    min_volume: int = Query(default=1, ge=1),
    max_children: int = Query(default=4, ge=1, le=12),
) -> RepertoireGraph:
    return build_graph(
        load_games(username),
        max_ply=max_ply,
        min_volume=min_volume,
        max_children=max_children,
    )
