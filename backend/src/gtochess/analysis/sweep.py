from __future__ import annotations

import atexit
import multiprocessing
import os
import time
from collections import defaultdict
from collections.abc import Callable, Sequence

import chess
import chess.engine
from pydantic import BaseModel, ConfigDict

from gtochess.analysis.book import MIN_VOLUME, measure_losses
from gtochess.domain.book import PositionLosses
from gtochess.domain.graph import RepertoireGraph
from gtochess.engine.protocol import EngineError
from gtochess.engine.stockfish import StockfishEngine
from gtochess.ingest.loss_store import LossStore

TIERS = ((25, 28), (10, 24), (MIN_VOLUME, 20))
FAILURE_STREAK = 25
# Depth 20 everywhere. The tier depths above are inert while the ceiling sits
# here, and only their volume floors still bite.
MAX_DEPTH = 20


class SweepItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    digest: str
    epd: str
    replies: tuple[str, ...]
    depth: int
    games: int


class SweepProgress(BaseModel):
    done: int
    total: int
    failed: int
    seconds: float

    @property
    def per_minute(self) -> float:
        return self.done / self.seconds * 60 if self.seconds else 0.0

    @property
    def eta_seconds(self) -> float | None:
        if not self.done or not self.seconds:
            return None
        return (self.total - self.done) * (self.seconds / self.done)


class SweepResult(BaseModel):
    total: int
    measured: int
    failed: int
    held: int
    seconds: float


class SweepBroken(RuntimeError):
    pass


def depth_for(games: int, ceiling: int = MAX_DEPTH) -> int | None:
    for floor, depth in TIERS:
        if games >= floor:
            return min(depth, ceiling)
    return None


def plan_sweep(
    graph: RepertoireGraph,
    store: LossStore,
    *,
    max_depth: int = MAX_DEPTH,
    limit: int = 0,
) -> list[SweepItem]:
    nodes = {n.digest: n for n in graph.nodes}
    replies: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.by_player:
            replies[edge.parent].append(edge.uci)

    work: list[SweepItem] = []
    for digest, ucis in replies.items():
        node = nodes.get(digest)
        if node is None:
            continue
        depth = depth_for(node.games, max_depth)
        if depth is None:
            continue
        held = store.get(digest)
        if held is not None and held.depth >= depth:
            continue
        work.append(
            SweepItem(
                digest=digest, epd=node.epd, replies=tuple(ucis), depth=depth, games=node.games
            )
        )

    # Heaviest first, which both puts the positions that matter most in front of a
    # run that gets cut short and keeps the pool balanced at the end.
    work.sort(key=lambda item: -item.games)
    return work[:limit] if limit else work


_engine: StockfishEngine | None = None


def _open_engine(path: str, threads: int, hash_mb: int) -> None:
    global _engine
    _engine = StockfishEngine(path, threads=threads, hash_mb=hash_mb)
    atexit.register(_engine.close)


def _measure_one(item: SweepItem) -> PositionLosses | None:
    if _engine is None:
        raise SweepBroken("worker started without an engine")
    try:
        board = chess.Board(f"{item.epd} 0 1")
        return measure_losses(_engine, board, item.replies, digest=item.digest, depth=item.depth)
    except (EngineError, chess.engine.EngineError, ValueError):
        return None


def worker_count(requested: int, threads: int) -> int:
    return requested or max(1, (os.cpu_count() or 4) // max(1, threads))


def run_sweep(
    items: Sequence[SweepItem],
    store: LossStore,
    *,
    engine_path: str,
    workers: int = 0,
    threads: int = 1,
    hash_mb: int = 128,
    on_progress: Callable[[SweepProgress], None] | None = None,
    report_every: int = 20,
) -> SweepResult:
    held = len(store)
    if not items:
        return SweepResult(total=0, measured=0, failed=0, held=held, seconds=0.0)

    started = time.perf_counter()
    done = 0
    failed = 0
    streak = 0
    pool = multiprocessing.Pool(
        worker_count(workers, threads),
        initializer=_open_engine,
        initargs=(engine_path, threads, hash_mb),
    )
    try:
        # chunksize 1 because a depth-28 position costs an order of magnitude more
        # than a depth-20 one, and chunking strands a worker on a run of them.
        for record in pool.imap_unordered(_measure_one, items, chunksize=1):
            done += 1
            if record is None:
                failed += 1
                streak += 1
                if streak >= FAILURE_STREAK:
                    raise SweepBroken(
                        f"{streak} positions failed in a row at {done}/{len(items)}; "
                        "the engine pool is gone, so stopping rather than burning the queue"
                    )
            else:
                streak = 0
                store.extend([record])
            if on_progress and (done % report_every == 0 or done == len(items)):
                on_progress(
                    SweepProgress(
                        done=done,
                        total=len(items),
                        failed=failed,
                        seconds=time.perf_counter() - started,
                    )
                )
        pool.close()
    except BaseException:
        pool.terminate()
        raise
    finally:
        pool.join()

    return SweepResult(
        total=len(items),
        measured=done - failed,
        failed=failed,
        held=held,
        seconds=round(time.perf_counter() - started, 1),
    )
