from __future__ import annotations

from typing import Any

from celery import Task

from fiftymoves.config import get_settings
from fiftymoves.ingest.lichess import LichessError
from fiftymoves.ingest.pipeline import IngestProgress, ingest_player
from fiftymoves.jobs.app import app

IMPORT_TASK = "fiftymoves.import_player"


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
        return {"username": username, "failed": str(exc)}

    return result.model_dump()
