from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from pydantic import BaseModel

from fiftymoves.config import Settings, get_settings
from fiftymoves.domain.games import GameRecord
from fiftymoves.ingest.lichess import LichessClient
from fiftymoves.ingest.parse import UnusableGame, parse_lichess_game
from fiftymoves.ingest.repertoire import build_decision_nodes


class IngestProgress(BaseModel):
    username: str
    exported: int
    usable: int
    skipped: int
    limit: int | None
    rate: float
    eta_seconds: float | None

    @property
    def percent(self) -> float | None:
        if not self.limit:
            return None
        return min(100.0, self.exported / self.limit * 100)


class IngestResult(BaseModel):
    username: str
    exported: int
    usable: int
    skipped: dict[str, int]
    speeds: dict[str, int]
    as_white: int
    score: float
    decision_positions: int
    games_path: str | None
    nodes_path: str | None
    seconds: float
    authenticated: bool


ProgressHook = Callable[[IngestProgress], None]


def ingest_player(
    username: str,
    *,
    settings: Settings | None = None,
    max_games: int | None = None,
    out_dir: Path | None = None,
    on_progress: ProgressHook | None = None,
    report_every: int = 500,
) -> IngestResult:
    settings = settings or get_settings()
    games: list[GameRecord] = []
    skipped: Counter[str] = Counter()

    games_path = out_dir / f"{username}.games.jsonl" if out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    limit = max_games if max_games is not None else settings.ingest_max_games
    started = time.monotonic()
    exported = 0
    authenticated = False

    with ExitStack() as stack:
        client = stack.enter_context(LichessClient.from_settings(settings))
        authenticated = client.authenticated
        # Written as games arrive so a long export survives an interruption.
        handle = stack.enter_context(games_path.open("w", encoding="utf-8")) if games_path else None

        stream = client.export_user_games(
            username,
            max_games=limit,
            rated=settings.ingest_rated_only or None,
            perf_types=settings.perf_type_list(),
        )
        for exported, raw in enumerate(stream, start=1):
            try:
                game = parse_lichess_game(raw, username)
            except UnusableGame as exc:
                skipped[str(exc)] += 1
            else:
                games.append(game)
                if handle is not None:
                    handle.write(game.model_dump_json() + "\n")

            if exported % report_every == 0:
                if handle is not None:
                    handle.flush()
                if on_progress is not None:
                    on_progress(_progress(username, exported, games, skipped, limit, started))

    elapsed = time.monotonic() - started
    if on_progress is not None:
        on_progress(_progress(username, exported, games, skipped, limit, started))

    nodes_path: Path | None = None
    decision_positions = 0
    if games:
        nodes = build_decision_nodes(games, max_ply=settings.ingest_max_ply)
        decision_positions = len(nodes)
        if out_dir:
            nodes_path = out_dir / f"{username}.nodes.jsonl"
            with nodes_path.open("w", encoding="utf-8") as sink:
                for node in nodes:
                    sink.write(json.dumps(node.model_dump(mode="json")) + "\n")

    return IngestResult(
        username=username,
        exported=exported,
        usable=len(games),
        skipped=dict(skipped),
        speeds=dict(Counter(g.speed for g in games).most_common()),
        as_white=sum(1 for g in games if g.player_is_white),
        score=round(sum(g.score for g in games) / len(games), 4) if games else 0.0,
        decision_positions=decision_positions,
        games_path=str(games_path) if games_path else None,
        nodes_path=str(nodes_path) if nodes_path else None,
        seconds=round(elapsed, 1),
        authenticated=authenticated,
    )


def load_player_games(username: str, directory: Path) -> tuple[list[GameRecord], int]:
    path = directory / f"{username}.games.jsonl"
    if not path.exists():
        return [], 0
    with path.open(encoding="utf-8") as handle:
        games = [GameRecord(**json.loads(line)) for line in handle if line.strip()]
    return games, path.stat().st_mtime_ns


def _progress(
    username: str,
    exported: int,
    games: list[GameRecord],
    skipped: Counter[str],
    limit: int | None,
    started: float,
) -> IngestProgress:
    elapsed = time.monotonic() - started
    rate = exported / elapsed if elapsed else 0.0
    remaining = (limit - exported) / rate if rate and limit else None
    return IngestProgress(
        username=username,
        exported=exported,
        usable=len(games),
        skipped=sum(skipped.values()),
        limit=limit,
        rate=round(rate, 1),
        eta_seconds=round(remaining, 0) if remaining is not None else None,
    )
