from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gtochess.analysis.profile import opening_edge, repertoire_consistency
from gtochess.analysis.selection import select_for_analysis
from gtochess.config import get_settings
from gtochess.domain.games import GameRecord
from gtochess.domain.repertoire import RepertoireNode
from gtochess.ingest.lichess import LichessError
from gtochess.ingest.pipeline import IngestProgress, ingest_player
from gtochess.ingest.repertoire import build_opening_records


def show(progress: IngestProgress) -> None:
    eta = f"~{progress.eta_seconds / 60:.0f} min left" if progress.eta_seconds else "unknown"
    print(
        f"  {progress.exported} exported, {progress.usable} usable, {progress.rate:.0f}/s, {eta}",
        flush=True,
    )


def summarise(out_dir: Path | None, username: str) -> None:
    settings = get_settings()
    if out_dir is None:
        return

    games_path = out_dir / f"{username}.games.jsonl"
    nodes_path = out_dir / f"{username}.nodes.jsonl"
    if not games_path.exists() or not nodes_path.exists():
        return

    with games_path.open(encoding="utf-8") as handle:
        games = [GameRecord(**json.loads(line)) for line in handle if line.strip()]
    with nodes_path.open(encoding="utf-8") as handle:
        nodes = [RepertoireNode(**json.loads(line)) for line in handle if line.strip()]

    result = select_for_analysis(nodes, settings.selection_policy())
    print(f"selected for engine: {result.selected_count} of {result.considered}")
    for reason, count in sorted(result.skipped.items()):
        print(f"  skipped {count:5}  {reason.value}")
    print("accounted for:", result.fully_accounted_for)

    consistency = repertoire_consistency(nodes)
    if consistency:
        print(f"consistency: {consistency.value:.3f} over {consistency.sample_size} positions")

    records = build_opening_records(games)
    edge = opening_edge(
        records,
        min_games=settings.opening_edge_min_games,
        prior_games=settings.opening_edge_prior_games,
    )
    print(f"openings: {len(records)}")
    if edge:
        print(f"opening edge: {edge.value:+.4f} over {edge.sample_size} games")
        print("strongest:", ", ".join(edge.evidence))


def run(username: str, *, max_games: int | None, out_dir: Path | None) -> int:
    settings = get_settings()
    result = ingest_player(
        username,
        settings=settings,
        max_games=max_games,
        out_dir=out_dir,
        on_progress=show,
    )

    rate = "60/s (own games)" if result.authenticated else "20/s (anonymous)"
    print(f"\nexported {result.exported} at {rate} in {result.seconds}s")
    print(f"usable {result.usable}, skipped {sum(result.skipped.values())}")
    for reason, count in sorted(result.skipped.items(), key=lambda pair: -pair[1]):
        print(f"  skipped {count:5}  {reason}")

    if not result.usable:
        return 1

    print("speeds:", result.speeds)
    print("as white:", result.as_white, "of", result.usable)
    print("score:", result.score)
    print("decision positions:", result.decision_positions)
    if result.games_path:
        print("wrote", result.games_path)
    if result.nodes_path:
        print("wrote", result.nodes_path)

    print()
    summarise(out_dir, username)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a lichess account and summarise it.")
    parser.add_argument("username")
    parser.add_argument("--max", dest="max_games", type=int, default=None)
    parser.add_argument("--out", dest="out_dir", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        return run(args.username, max_games=args.max_games, out_dir=args.out_dir)
    except LichessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
