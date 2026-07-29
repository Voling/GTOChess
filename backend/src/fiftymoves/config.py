from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from fiftymoves.domain.repertoire import SelectionPolicy
from fiftymoves.layout import CONTAINER_ENGINE_DIR, engine_binary_name, local_engine_dir

_settings: Settings | None = None


class EngineNotProvisioned(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIFTYMOVES_", env_file=".env", extra="ignore")

    engine_path: Path | None = None
    engine_threads: int = 1
    engine_hash_mb: int = 128

    analysis_depth: int = 20
    ablation_depth: int = 12
    multipv: int = 4

    only_move_threshold_cp: int = 300
    playable_band_cp: int = 30
    sensitivity_floor_cp: int = 40
    sensitivity_top_n: int = 5

    selection_min_ply: int = 6
    selection_max_ply: int = 24
    selection_min_games: int = 3
    selection_min_divergence_games: int = 2
    selection_budget: int = 400
    selection_frontier_sample: int = 2

    profile_min_sample: int = 20
    profile_evidence_limit: int = 5
    blunder_cp: int = 100
    winning_cp: int = 200
    losing_cp: int = -200

    flaw_min_occurrences: int = 2
    flaw_min_mean_loss_cp: float = 50.0
    flaw_limit: int = 25

    opening_edge_min_games: int = 10
    opening_edge_prior_games: int = 30

    family_window_ply: int = 16
    family_min_games: int = 4
    family_prior_games: int = 12
    family_slots: int = 3

    llm_provider: str = "auto"
    llm_model: str = "claude-opus-5"
    llm_effort: str = "medium"
    llm_max_tokens: int = 1500
    llm_timeout_s: float = 60.0
    llm_cache_entries: int = 512
    anthropic_api_key: str | None = None
    explain_depth: int = 18

    lichess_base_url: str = "https://lichess.org"
    lichess_token: str | None = None
    # Any string identifies a lichess public client; it needs no registration.
    lichess_client_id: str = "fiftymoves.local"
    lichess_redirect_uri: str = "http://localhost:5173/auth/lichess/callback"
    lichess_timeout_s: float = 60.0
    lichess_max_retries: int = 4
    lichess_backoff_s: float = 60.0

    ingest_rated_only: bool = True
    ingest_perf_types: str | None = None
    ingest_max_games: int | None = 2000
    ingest_max_ply: int = 24

    user_agent: str = "FiftyMoves/0.1"
    pipeline_version: str = "v1"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    data_dir: Path = Path("data")
    # A large import runs to hundreds of megabytes in memory, so this stays small.
    graph_cache_entries: int = 8
    walk_cache_entries: int = 4

    job_result_ttl_s: int = 86400
    job_report_every: int = 250
    # Posted to when a job finishes, so a caller can await an import without polling.
    job_webhook_url: str | None = None
    job_webhook_secret: str | None = None
    job_webhook_timeout_s: float = 10.0

    # Opening evaluations differ by tens of centipawns between sound moves, so the
    # floor sits well above the middlegame convention or every repertoire looks bad.
    annotation_depth: int = 16
    annotation_dubious_cp: int = 90
    annotation_mistake_cp: int = 160
    annotation_blunder_cp: int = 300
    annotation_min_games: int = 2
    annotation_budget: int = 400

    database_url: str = "postgresql+psycopg://fiftymoves@localhost/fiftymoves"
    redis_url: str = "redis://localhost:6379/0"
    s3_bucket: str | None = None

    def anthropic_credentials(self) -> str | None:
        return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")

    def _candidate_engine_dirs(self) -> list[Path]:
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

    def selection_policy(self) -> SelectionPolicy:
        return SelectionPolicy(
            min_ply=self.selection_min_ply,
            max_ply=self.selection_max_ply,
            min_games=self.selection_min_games,
            min_divergence_games=self.selection_min_divergence_games,
            budget=self.selection_budget,
            frontier_sample=self.selection_frontier_sample,
        )

    def perf_type_list(self) -> tuple[str, ...]:
        if not self.ingest_perf_types:
            return ()
        return tuple(p.strip() for p in self.ingest_perf_types.split(",") if p.strip())


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
