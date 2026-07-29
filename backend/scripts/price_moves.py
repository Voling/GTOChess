from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict

import chess

from fiftymoves.analysis.book import price_position
from fiftymoves.api.main import graph_for
from fiftymoves.config import get_settings
from fiftymoves.domain.games import Side
from fiftymoves.engine.stockfish import StockfishEngine
from fiftymoves.ingest.cost_store import MoveCostStore

TIERS = ((25, 28), (10, 24), (3, 20))


def depth_for(games: int) -> int | None:
    for floor, depth in TIERS:
        if games >= floor:
            return depth
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--side", default="white")
    parser.add_argument("--max-ply", type=int, default=28)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
    store = MoveCostStore(settings.data_dir)
    engine = StockfishEngine(settings.resolve_engine_path())

    graph = graph_for(
        args.username,
        side=Side(args.side),
        max_ply=args.max_ply,
        min_volume=1,
        max_children=12,
    )
    nodes = {n.digest: n for n in graph.nodes}
    replies: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.by_player:
            replies[edge.parent].append(edge.uci)

    work = []
    for digest, ucis in replies.items():
        node = nodes.get(digest)
        if node is None:
            continue
        depth = depth_for(node.games)
        if depth is None:
            continue
        held = store.get(digest)
        if held is not None and held.depth >= depth:
            continue
        work.append((node.games, digest, ucis, depth))
    work.sort(key=lambda row: -row[0])
    if args.limit:
        work = work[: args.limit]

    print(f"{len(work)} positions to price, {len(store)} already held", flush=True)
    started = time.perf_counter()
    batch = []
    for index, (games, digest, ucis, depth) in enumerate(work, start=1):
        node = nodes[digest]
        board = chess.Board(f"{node.epd} 0 1")
        batch.append(price_position(engine, board, ucis, digest=digest, depth=depth))
        if len(batch) >= 20 or index == len(work):
            store.extend(batch)
            batch = []
            elapsed = time.perf_counter() - started
            rate = elapsed / index
            print(
                f"  {index}/{len(work)}  {games:>4} games  depth {depth}  "
                f"{elapsed / 60:.1f}m elapsed, {(len(work) - index) * rate / 60:.0f}m left",
                flush=True,
            )

    print(f"done, {len(store)} positions priced", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
