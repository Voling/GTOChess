from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from gtochess.domain.games import GameRecord
from gtochess.domain.openings import UNCLASSIFIED, OpeningFamily

FORCING_CEILING = 0.30
FORCING_WEIGHT = 0.55
DECISIVE_WEIGHT = 0.45

_SLUG = re.compile(r"[^a-z0-9]+")


def family_name(opening_name: str | None) -> str:
    if not opening_name:
        return "Unclassified"
    return opening_name.split(":")[0].strip() or "Unclassified"


def family_key(opening_name: str | None) -> str:
    name = family_name(opening_name)
    if name == "Unclassified":
        return UNCLASSIFIED
    return _SLUG.sub("-", name.lower()).strip("-") or UNCLASSIFIED


def forcing_share(moves_san: Sequence[str], window_ply: int) -> float:
    window = list(moves_san[:window_ply])
    if not window:
        return 0.0
    forcing = sum(1 for san in window if "x" in san or san.endswith(("+", "#")))
    return forcing / len(window)


class _Bucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ecos: set[str] = set()
        self.scores: list[float] = []
        self.forcing: list[float] = []
        self.as_white = 0

    def add(self, game: GameRecord, window_ply: int) -> None:
        if game.eco:
            self.ecos.add(game.eco)
        self.scores.append(game.score)
        self.forcing.append(forcing_share(game.moves_san, window_ply))
        if game.player_is_white:
            self.as_white += 1

    @property
    def games(self) -> int:
        return len(self.scores)


def _shrink(value: float, games: int, population: float, prior_games: int) -> float:
    return (value * games + population * prior_games) / (games + prior_games)


def build_families(
    games: Sequence[GameRecord],
    *,
    window_ply: int = 16,
    min_games: int = 4,
    prior_games: int = 12,
    slots: int = 3,
) -> list[OpeningFamily]:
    buckets: dict[str, _Bucket] = {}
    for game in games:
        key = family_key(game.opening_name)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket(family_name(game.opening_name))
            buckets[key] = bucket
        bucket.add(game, window_ply)

    kept = {k: b for k, b in buckets.items() if b.games >= min_games and k != UNCLASSIFIED}
    if not kept:
        return []

    total_games = sum(b.games for b in kept.values())
    population_score = sum(sum(b.scores) for b in kept.values()) / total_games
    population_forcing = sum(sum(b.forcing) for b in kept.values()) / total_games
    population_decisive = (
        sum(sum(1 for s in b.scores if s != 0.5) for b in kept.values()) / total_games
    )

    families: list[OpeningFamily] = []
    for key, bucket in kept.items():
        raw_score = sum(bucket.scores) / bucket.games
        raw_forcing = sum(bucket.forcing) / bucket.games
        raw_decisive = sum(1 for s in bucket.scores if s != 0.5) / bucket.games

        forcing = _shrink(raw_forcing, bucket.games, population_forcing, prior_games)
        decisive = _shrink(raw_decisive, bucket.games, population_decisive, prior_games)
        relative_decisive = min(1.0, max(0.0, 0.5 + (decisive - population_decisive)))
        sharpness = FORCING_WEIGHT * min(1.0, forcing / FORCING_CEILING) + (
            DECISIVE_WEIGHT * relative_decisive
        )

        ecos = sorted(bucket.ecos)
        families.append(
            OpeningFamily(
                key=key,
                name=bucket.name,
                eco_low=ecos[0] if ecos else None,
                eco_high=ecos[-1] if ecos else None,
                games=bucket.games,
                as_white=bucket.as_white,
                score=_shrink(raw_score, bucket.games, population_score, prior_games),
                forcing_rate=forcing,
                decisive_rate=decisive,
                sharpness=sharpness,
                slot=-1,
            )
        )

    families.sort(key=lambda f: (-f.games, f.key))
    return [f.model_copy(update={"slot": i if i < slots else -1}) for i, f in enumerate(families)]


def segments(opening_name: str) -> list[str]:
    """Split a lichess name into its hierarchy.

    "Sicilian Defense: Najdorf Variation, English Attack" becomes
    ["Sicilian Defense", "Najdorf Variation", "English Attack"].
    """
    head, _, tail = opening_name.partition(":")
    parts = [head.strip()]
    parts.extend(piece.strip() for piece in tail.split(",") if piece.strip())
    return [p for p in parts if p]


def join_segments(parts: Sequence[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}: {', '.join(parts[1:])}"


def shared_name(names: Sequence[str]) -> str:
    """The most specific name every game here agrees on."""
    if not names:
        return ""
    split = [segments(name) for name in names]
    common: list[str] = []
    for index in range(min(len(parts) for parts in split)):
        candidate = split[0][index]
        if all(parts[index] == candidate for parts in split):
            common.append(candidate)
        else:
            break
    return join_segments(common)


def dominant(counts: Mapping[str, int]) -> tuple[str | None, float]:
    if not counts:
        return None, 0.0
    total = sum(counts.values())
    key, count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    return key, count / total
