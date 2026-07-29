from __future__ import annotations

from typing import Any

from celery import Task

from fiftymoves.config import get_settings
from fiftymoves.ingest.lichess import LichessError
from fiftymoves.ingest.pipeline import IngestProgress, ingest_player
from fiftymoves.jobs.app import app
from fiftymoves.jobs.notify import deliver

IMPORT_TASK = "fiftymoves.import_player"
ANNOTATE_TASK = "fiftymoves.annotate_player"


def announce(event: str, body: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.job_webhook_url:
        return
    deliver(
        settings.job_webhook_url,
        event,
        body,
        secret=settings.job_webhook_secret,
        timeout_s=settings.job_webhook_timeout_s,
    )


@app.task(bind=True, name=IMPORT_TASK, max_retries=0)
def import_player(self: Task, username: str, max_games: int | None = None) -> dict[str, Any]:
    settings = get_settings()

    def publish(progress: IngestProgress) -> None:
        self.update_state(state="PROGRESS", meta=progress.model_dump())

    try:
        result = ingest_player(
            username,
            settings=settings,
            max_games=max_games,
            out_dir=settings.data_dir,
            on_progress=publish,
            report_every=settings.job_report_every,
        )
    except LichessError as exc:
        # Carried as a plain result so the API can report it without a traceback.
        failure = {"username": username, "failed": str(exc), "job_id": self.request.id}
        announce("import.failed", failure)
        return failure

    body = result.model_dump()
    announce("import.finished", {**body, "job_id": self.request.id})
    return body


@app.task(bind=True, name=ANNOTATE_TASK, max_retries=0)
def annotate_player(
    self: Task,
    username: str,
    side: str = "white",
    max_ply: int = 10,
    min_volume: int = 2,
    max_children: int = 4,
) -> dict[str, Any]:
    from fiftymoves.analysis.annotations import annotate_graph
    from fiftymoves.config import EngineNotProvisioned
    from fiftymoves.domain.games import Side
    from fiftymoves.engine.stockfish import StockfishEngine
    from fiftymoves.ingest.annotations_store import AnnotationStore, shape_key
    from fiftymoves.ingest.graph import build_graph
    from fiftymoves.ingest.pipeline import load_player_games

    settings = get_settings()
    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        return {"username": username, "failed": str(exc).splitlines()[0]}

    games, stamp = load_player_games(username, settings.data_dir)
    if not games:
        return {"username": username, "failed": f"no games imported for {username!r} yet"}

    chosen = Side(side)
    graph = build_graph(
        games,
        side=chosen,
        max_ply=max_ply,
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )
    shape = shape_key(username, chosen, max_ply, min_volume, max_children, stamp)

    def publish(done: int, total: int) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"username": username, "positions": done, "total": total, "shape": shape},
        )

    engine = StockfishEngine(
        str(engine_path), threads=settings.engine_threads, hash_mb=settings.engine_hash_mb
    )
    try:
        result = annotate_graph(
            engine,
            graph,
            username=username,
            shape=shape,
            depth=settings.annotation_depth,
            dubious_cp=settings.annotation_dubious_cp,
            mistake_cp=settings.annotation_mistake_cp,
            blunder_cp=settings.annotation_blunder_cp,
            min_games=settings.annotation_min_games,
            budget=settings.annotation_budget,
            on_progress=publish,
        )
    finally:
        engine.close()

    AnnotationStore(settings.data_dir).write(result)
    body = {
        "username": username,
        "shape": shape,
        "annotated": len(result.annotations),
        "flawed": len(result.flawed),
        "positions_searched": result.positions_searched,
        "truncated": result.truncated,
        "job_id": self.request.id,
    }
    announce("annotations.finished", body)
    return body
