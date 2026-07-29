"""Runtime configuration.

Engine resolution deliberately never falls back to a Stockfish on ``PATH``.
Evaluation numbers are baked into cache keys, sensitivity rankings and golden-set
assertions, so an unpinned engine of unknown build silently invalidates
reproducibility. If no provisioned binary is found we fail loudly and say how to
get one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from fiftymoves.layout import (
    CONTAINER_ENGINE_DIR,
    engine_binary_name,
    local_engine_dir,
)


class EngineNotProvisioned(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIFTYMOVES_", env_file=".env", extra="ignore")

    engine_path: Path | None = Field(
        default=None, description="Explicit engine override. Must be a pinned build."
    )
    engine_threads: int = 1
    engine_hash_mb: int = 128

    #: Depth for the main multi-PV report.
    analysis_depth: int = 20
    #: Ablation runs once per candidate element, so it stays shallow on purpose.
    ablation_depth: int = 12
    multipv: int = 4

    #: Sensitivity thresholds. Config, not constants -- they interact with depth and
    #: are pinned by the golden set.
    only_move_threshold_cp: int = 300
    playable_band_cp: int = 30
    sensitivity_floor_cp: int = 40
    sensitivity_top_n: int = 5

    #: Bumping this rotates every cached explanation without a CDN purge.
    pipeline_version: str = "v1"

    database_url: str = "postgresql+psycopg://fiftymoves@localhost/fiftymoves"
    redis_url: str = "redis://localhost:6379/0"
    s3_bucket: str | None = None

    def _candidate_engine_dirs(self) -> list[Path]:
        # Container first: in a deployed image this is the only one that exists,
        # and checking it first avoids a pointless stat of a dev-only path.
        return [CONTAINER_ENGINE_DIR, local_engine_dir()]

    def resolve_engine_path(self) -> Path:
        if self.engine_path is not None:
            if not self.engine_path.exists():
                raise EngineNotProvisioned(
                    f"FIFTYMOVES_ENGINE_PATH does not exist: {self.engine_path}"
                )
            return self.engine_path

        binary = engine_binary_name()
        for directory in self._candidate_engine_dirs():
            candidate = directory / binary
            if candidate.exists():
                return candidate

        searched = "\n  ".join(str(d) for d in self._candidate_engine_dirs())
        raise EngineNotProvisioned(
            "No provisioned Stockfish found. Searched:\n  "
            f"{searched}\n\n"
            "In Docker the engine is fetched during the build stage.\n"
            "For local development run:  python -m fiftymoves.tools.fetch_stockfish\n\n"
            "A Stockfish on PATH is intentionally not used -- evaluations feed cache "
            "keys and golden-set assertions, so the build must be pinned."
        )

    def engine_available(self) -> bool:
        try:
            self.resolve_engine_path()
        except EngineNotProvisioned:
            return False
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
