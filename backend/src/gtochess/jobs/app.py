from __future__ import annotations

from celery import Celery

from gtochess.config import get_settings

_app: Celery | None = None


def celery_app() -> Celery:
    global _app
    if _app is None:
        settings = get_settings()
        _app = Celery(
            "gtochess",
            broker=settings.redis_url,
            backend=settings.redis_url,
            include=["gtochess.jobs.tasks"],
        )
        _app.conf.update(
            # Measurement forks an engine pool, which a prefork worker cannot do,
            # so it gets its own queue to be served by a solo-pool worker.
            task_routes={"gtochess.measure_losses": {"queue": "measure"}},
            task_default_queue="default",
            task_track_started=True,
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            result_expires=settings.job_result_ttl_s,
            worker_max_tasks_per_child=8,
            broker_connection_retry_on_startup=True,
        )
    return _app


app = celery_app()
