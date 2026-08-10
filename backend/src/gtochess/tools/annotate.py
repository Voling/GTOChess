from __future__ import annotations

import argparse
import sys
import time

from gtochess.analysis.annotations import annotate_graph
from gtochess.config import get_settings
from gtochess.domain.games import Side
from gtochess.engine.stockfish import StockfishEngine
from gtochess.ingest.annotations_store import AnnotationStore, shape_key
from gtochess.ingest.graph import build_graph
from gtochess.ingest.pipeline import load_player_games
from gtochess.storage import get_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mark the mistakes in a repertoire.")
    parser.add_argument("username")
    parser.add_argument("--side", default="white")
    # These four decide the shape key, so they have to match what the frontend
    # asks for or the marks it looks up will not be the ones written here.
    parser.add_argument("--max-ply", type=int, default=10)
    parser.add_argument("--min-volume", type=int, default=25)
    parser.add_argument("--max-children", type=int, default=4)
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--budget", type=int, default=0)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--hash", dest="hash_mb", type=int, default=0)
    args = parser.parse_args(argv)

    settings = get_settings()
    engine_path = settings.resolve_engine_path()
    games, stamp = load_player_games(args.username, get_storage())
    if not games:
        print(f"no games imported for {args.username!r} yet", file=sys.stderr)
        return 1

    side = Side(args.side)
    graph = build_graph(
        games,
        side=side,
        max_ply=args.max_ply,
        min_volume=args.min_volume,
        max_children=args.max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )
    shape = shape_key(args.username, side, args.max_ply, args.min_volume, args.max_children, stamp)
    depth = args.depth or settings.annotation_depth
    budget = args.budget or settings.annotation_budget
    threads = args.threads or settings.engine_threads
    hash_mb = args.hash_mb or settings.engine_hash_mb

    print(
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges, shape {shape}\n"
        f"depth {depth}, budget {budget} positions, {threads} threads, {hash_mb} MB hash",
        flush=True,
    )

    started = time.monotonic()

    def show(done: int, total: int) -> None:
        if done % 10 and done != total:
            return
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed else 0.0
        left = (total - done) / rate if rate else 0.0
        print(
            f"  {done}/{total}  {elapsed / 60:.1f}m elapsed, "
            f"{left / 60:.0f}m left, {rate * 60:.1f}/min",
            flush=True,
        )

    engine = StockfishEngine(str(engine_path), threads=threads, hash_mb=hash_mb)
    try:
        result = annotate_graph(
            engine,
            graph,
            username=args.username,
            shape=shape,
            depth=depth,
            dubious_cp=settings.annotation_dubious_cp,
            mistake_cp=settings.annotation_mistake_cp,
            blunder_cp=settings.annotation_blunder_cp,
            min_games=settings.annotation_min_games,
            budget=budget,
            on_progress=show,
        )
    finally:
        engine.close()

    path = AnnotationStore(get_storage()).write(result)
    tail = ", truncated by the budget" if result.truncated else ""
    minutes = (time.monotonic() - started) / 60
    print(
        f"done in {minutes:.1f}m: {len(result.annotations)} moves marked, "
        f"{len(result.flawed)} flawed, {result.positions_searched} searches{tail}\n{path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
