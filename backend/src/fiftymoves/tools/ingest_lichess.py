from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from fiftymoves.analysis.profile import opening_edge, repertoire_consistency
from fiftymoves.analysis.selection import select_for_analysis
from fiftymoves.config import get_settings
from fiftymoves.domain.games import GameRecord
from fiftymoves.ingest.lichess import LichessClient, LichessError
from fiftymoves.ingest.parse import UnusableGame, parse_lichess_game
from fiftymoves.ingest.repertoire import build_decision_nodes, build_opening_records


def run(username: str, *, max_games: int | None, out_dir: Path | None) -> int:
    settings = get_settings()
    games: list[GameRecord] = []
    skipped: Counter[str] = Counter()

    with LichessClient.from_settings(settings) as client:
        account = client.account(username)
        counts = account.get("count", {})
        print(
            f"account {account.get('id')}: {counts.get('all')} games, {counts.get('rated')} rated"
        )

        limit = max_games if max_games is not None else settings.ingest_max_games
        print(f"exporting up to {limit} games, perf={settings.perf_type_list() or 'all'}")

        for raw in client.export_user_games(
            username,
            max_games=limit,
            rated=settings.ingest_rated_only or None,
            perf_types=settings.perf_type_list(),
        ):
            try:
                games.append(parse_lichess_game(raw, username))
            except UnusableGame as exc:
                skipped[str(exc)] += 1

    print(f"parsed {len(games)} games, skipped {sum(skipped.values())}")
    for reason, count in skipped.most_common():
        print(f"  skipped {count:5}  {reason}")

    if not games:
        return 1

    speeds = Counter(g.speed for g in games)
    print("speeds:", dict(speeds.most_common()))
    print("as white:", sum(1 for g in games if g.player_is_white), "of", len(games))
    print("score:", round(sum(g.score for g in games) / len(games), 3))
    print("with server evals:", sum(1 for g in games if g.evals_cp))
    print("with clocks:", sum(1 for g in games if g.clocks_cs))

    nodes = build_decision_nodes(games, max_ply=settings.ingest_max_ply)
    print(f"\ndecision positions: {len(nodes)}")

    result = select_for_analysis(nodes, settings.selection_policy())
    print(f"selected for engine: {result.selected_count} of {result.considered}")
    for reason, count in sorted(result.skipped.items()):
        print(f"  skipped {count:5}  {reason.value}")
    print("accounted for:", result.fully_accounted_for)

    consistency = repertoire_consistency(nodes)
    if consistency:
        print(f"\nconsistency: {consistency.value:.3f} over {consistency.sample_size} positions")

    records = build_opening_records(games)
    print(f"openings: {len(records)}")
    edge = opening_edge(
        records,
        min_games=settings.opening_edge_min_games,
        prior_games=settings.opening_edge_prior_games,
    )
    if edge:
        print(f"opening edge: {edge.value:+.4f} over {edge.sample_size} games")
        print("strongest:", ", ".join(edge.evidence))
    else:
        print(f"opening edge: omitted, no opening reached {settings.opening_edge_min_games} games")

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        games_path = out_dir / f"{username}.games.jsonl"
        with games_path.open("w", encoding="utf-8") as handle:
            for game in games:
                handle.write(game.model_dump_json() + "\n")
        nodes_path = out_dir / f"{username}.nodes.json"
        nodes_path.write_text(
            json.dumps([n.model_dump(mode="json") for n in nodes], indent=2), encoding="utf-8"
        )
        print(f"\nwrote {games_path}")
        print(f"wrote {nodes_path}")

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
