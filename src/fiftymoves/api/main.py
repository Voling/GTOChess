"""API surface.

Currently just enough to make the container verifiable end to end. The
node-expansion endpoints land with the Postgres schema.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from fiftymoves.config import EngineNotProvisioned, get_settings

app = FastAPI(title="FiftyMoves", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus engine provisioning status.

    Engine availability is reported rather than asserted: the API process itself
    does not analyse (that happens in enrichment workers), so a missing engine
    is a degraded state worth surfacing, not a reason to fail the probe.
    """
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
