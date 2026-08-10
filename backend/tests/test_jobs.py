from __future__ import annotations

import pytest

from gtochess.api.imports import read_job
from gtochess.ingest.oauth import StoredToken, challenge_for, make_verifier
from gtochess.ingest.pipeline import IngestProgress, IngestResult

RESULT = {
    "username": "dylanette",
    "exported": 100,
    "usable": 98,
    "skipped": {"variant": 2},
    "speeds": {"blitz": 98},
    "as_white": 50,
    "score": 0.51,
    "decision_positions": 300,
    "games_path": "data/dylanette.games.jsonl",
    "nodes_path": "data/dylanette.nodes.jsonl",
    "seconds": 12.0,
    "authenticated": True,
}


class TestPkce:
    def test_the_challenge_is_the_sha256_of_the_verifier(self) -> None:
        verifier = make_verifier()
        assert challenge_for(verifier) == challenge_for(verifier)

    def test_verifiers_are_unique_per_attempt(self) -> None:
        assert make_verifier() != make_verifier()

    def test_the_challenge_carries_no_padding(self) -> None:
        assert "=" not in challenge_for(make_verifier())

    def test_the_verifier_meets_the_length_floor(self) -> None:
        assert 43 <= len(make_verifier()) <= 128


class TestStoredToken:
    def test_an_expiry_in_the_past_is_expired(self) -> None:
        assert StoredToken(access_token="a", expires_at=1).expired is True

    def test_no_expiry_never_expires(self) -> None:
        assert StoredToken(access_token="a").expired is False

    def test_a_future_expiry_is_live(self) -> None:
        assert StoredToken(access_token="a", expires_at=4_102_444_800).expired is False


class TestProgress:
    def test_percent_tracks_the_limit(self) -> None:
        progress = IngestProgress(
            username="d", exported=50, usable=48, skipped=2, limit=200, rate=20.0, eta_seconds=7.5
        )
        assert progress.percent == 25.0

    def test_percent_is_unknown_without_a_limit(self) -> None:
        progress = IngestProgress(
            username="d", exported=50, usable=50, skipped=0, limit=None, rate=20.0, eta_seconds=None
        )
        assert progress.percent is None

    def test_percent_never_runs_past_the_end(self) -> None:
        progress = IngestProgress(
            username="d", exported=250, usable=250, skipped=0, limit=200, rate=20.0, eta_seconds=0
        )
        assert progress.percent == 100.0


class TestJobReading:
    def test_a_pending_task_reads_as_queued(self) -> None:
        job = read_job("j1", "dylanette", "PENDING", None)
        assert job.state == "queued"

    def test_progress_meta_is_surfaced(self) -> None:
        meta = {
            "username": "dylanette",
            "exported": 500,
            "usable": 480,
            "skipped": 20,
            "limit": 28000,
            "rate": 19.5,
            "eta_seconds": 1410.0,
        }
        job = read_job("j1", "", "PROGRESS", meta)
        assert job.state == "running"
        assert job.progress is not None
        assert job.progress.exported == 500
        assert job.username == "dylanette"

    def test_a_finished_task_carries_its_result(self) -> None:
        job = read_job("j1", "", "SUCCESS", RESULT)
        assert job.state == "done"
        assert job.result is not None
        assert job.result.usable == 98
        assert job.result.authenticated is True

    def test_a_lichess_error_reads_as_failed_not_done(self) -> None:
        job = read_job("j1", "dylanette", "SUCCESS", {"username": "dylanette", "failed": "404"})
        assert job.state == "failed"
        assert job.error == "404"

    def test_a_crashed_task_reads_as_failed(self) -> None:
        job = read_job("j1", "dylanette", "FAILURE", RuntimeError("boom"))
        assert job.state == "failed"
        assert job.error == "boom"

    def test_an_unknown_state_falls_back_to_queued(self) -> None:
        assert read_job("j1", "d", "SOMETHING_NEW", None).state == "queued"


class TestIngestResult:
    def test_it_round_trips_through_json(self) -> None:
        result = IngestResult.model_validate(RESULT)
        assert IngestResult.model_validate_json(result.model_dump_json()) == result

    def test_a_result_without_paths_is_valid(self) -> None:
        payload = dict(RESULT, games_path=None, nodes_path=None)
        assert IngestResult.model_validate(payload).games_path is None


def test_celery_app_is_configured_from_settings() -> None:
    pytest.importorskip("celery")
    from gtochess.jobs.app import celery_app

    app = celery_app()
    assert app.conf.task_serializer == "json"
    assert app.conf.task_track_started is True
