from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from gtochess.domain.book import PositionLosses
from gtochess.storage import Storage, as_storage

FILENAME = "move_costs.jsonl"


class LossStore:
    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)
        self._entries: dict[str, PositionLosses] | None = None

    @property
    def storage(self) -> Storage:
        return self._storage

    def _load(self) -> dict[str, PositionLosses]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, PositionLosses] = {}
        for line in self._storage.lines(FILENAME):
            try:
                record = PositionLosses.model_validate_json(line)
            except ValueError:
                continue
            kept = entries.get(record.digest)
            if kept is None or record.depth >= kept.depth:
                entries[record.digest] = record
        self._entries = entries
        return entries

    def get(self, digest: str) -> PositionLosses | None:
        return self._load().get(digest)

    def missing(self, digests: Iterable[str], *, depth: int = 0) -> list[str]:
        entries = self._load()
        out = []
        for digest in digests:
            held = entries.get(digest)
            if held is None or held.depth < depth:
                out.append(digest)
        return out

    def extend(self, records: Iterable[PositionLosses]) -> int:
        entries = self._load()
        fresh = [
            r for r in records if (held := entries.get(r.digest)) is None or r.depth > held.depth
        ]
        if not fresh:
            return 0
        self._storage.append(FILENAME, "".join(r.model_dump_json() + "\n" for r in fresh))
        for record in fresh:
            entries[record.digest] = record
        return len(fresh)

    def __len__(self) -> int:
        return len(self._load())
