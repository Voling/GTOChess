from __future__ import annotations

import argparse
import sys

from gtochess.analysis.book import BOOK_MAX_CHILDREN, MIN_VOLUME
from gtochess.analysis.sweep import (
    MAX_DEPTH,
    SweepProgress,
    plan_sweep,
    run_sweep,
    worker_count,
)
from gtochess.config import get_settings
from gtochess.domain.games import Side
from gtochess.ingest.loss_store import LossStore


def show(progress: SweepProgress) -> None:
    eta = progress.eta_seconds
    left = f"{eta / 60:.0f}m left" if eta else "unknown"
    print(
        f"  {progress.done}/{progress.total}  {progress.seconds / 60:.1f}m elapsed, "
        f"{left}, {progress.per_minute:.1f}/min",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure move losses across a repertoire.")
    parser.add_argument("username")
    parser.add_argument("--side", default="white")
    parser.add_argument("--max-ply", type=int, default=28)
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--hash", dest="hash_mb", type=int, default=128)
    args = parser.parse_args(argv)

    from gtochess.api.main import graph_for

    settings = get_settings()
    store = LossStore(settings.data_dir)
    graph = graph_for(
        args.username,
        side=Side(args.side),
        max_ply=args.max_ply,
        min_volume=MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    items = plan_sweep(graph, store, max_depth=args.max_depth, limit=args.limit)
    workers = worker_count(args.workers, args.threads)
    print(
        f"{len(items)} positions to measure, {len(store)} already held\n"
        f"{workers} workers x {args.threads} threads, "
        f"{workers * args.hash_mb} MB hash total, depth capped at {args.max_depth}",
        flush=True,
    )

    result = run_sweep(
        items,
        store,
        engine_path=str(settings.resolve_engine_path()),
        workers=args.workers,
        threads=args.threads,
        hash_mb=args.hash_mb,
        on_progress=show,
    )
    tail = f", {result.failed} failed" if result.failed else ""
    print(
        f"done in {result.seconds / 60:.1f}m: {result.measured} measured{tail}, "
        f"{len(store)} positions held",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
