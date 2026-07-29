from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from fiftymoves.ingest.pipeline import IngestProgress, IngestResult

JobState = Literal["queued", "running", "done", "failed"]

_CELERY_STATES: dict[str, JobState] = {
    "PENDING": "queued",
    "RECEIVED": "queued",
    "STARTED": "running",
    "PROGRESS": "running",
    "RETRY": "running",
    "SUCCESS": "done",
    "FAILURE": "failed",
    "REVOKED": "failed",
}


class ImportJob(BaseModel):
    job_id: str
    username: str
    state: JobState
    progress: IngestProgress | None = None
    result: IngestResult | None = None
    error: str | None = None


def read_job(job_id: str, username: str, raw_state: str, payload: Any) -> ImportJob:
    state = _CELERY_STATES.get(raw_state, "queued")

    if state == "failed":
        return ImportJob(
            job_id=job_id, username=username, state=state, error=str(payload or "job failed")
        )

    if state == "done" and isinstance(payload, dict):
        if payload.get("failed"):
            return ImportJob(
                job_id=job_id,
                username=payload.get("username", username),
                state="failed",
                error=str(payload["failed"]),
            )
        result = IngestResult.model_validate(payload)
        return ImportJob(job_id=job_id, username=result.username, state=state, result=result)

    if state == "running" and isinstance(payload, dict) and "exported" in payload:
        progress = IngestProgress.model_validate(payload)
        return ImportJob(job_id=job_id, username=progress.username, state=state, progress=progress)

    return ImportJob(job_id=job_id, username=username, state=state)
