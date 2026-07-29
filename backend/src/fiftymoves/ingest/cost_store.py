from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fiftymoves.domain.book import MoveCost

FILENAME = "move_costs.jsonl"


class MoveCostStore:
    """Priced positions on disk, keyed by position rather than by graph shape.

    Keying by shape would mean a slider nudge discards hours of engine time, and
    the cost of a move does not depend on how the graph was pruned.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / FILENAME
        self._entries: dict[str, MoveCost] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, MoveCost]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, MoveCost] = {}
        if self._path.exists():
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = MoveCost.model_validate_json(line)
                    except ValueError:
                        continue
                    kept = entries.get(record.digest)
                    if kept is None or record.depth >= kept.depth:
                        entries[record.digest] = record
        self._entries = entries
        return entries

    def get(self, digest: str) -> MoveCost | None:
        return self._load().get(digest)

    def missing(self, digests: Iterable[str], *, depth: int = 0) -> list[str]:
        entries = self._load()
        out = []
        for digest in digests:
            held = entries.get(digest)
            if held is None or held.depth < depth:
                out.append(digest)
        return out

    def extend(self, records: Iterable[MoveCost]) -> int:
        entries = self._load()
        fresh = [
            r for r in records if (held := entries.get(r.digest)) is None or r.depth > held.depth
        ]
        if not fresh:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            for record in fresh:
                handle.write(record.model_dump_json() + "\n")
                entries[record.digest] = record
        return len(fresh)

    def __len__(self) -> int:
        return len(self._load())
