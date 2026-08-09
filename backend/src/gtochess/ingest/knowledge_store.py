from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from gtochess.domain.knowledge import PositionKnowledge
from gtochess.storage import Storage, as_storage

FILENAME = "knowledge.jsonl"


class KnowledgeStore:
    """Position knowledge on disk, indexed by plan.

    Keyed by position, so it is shared by every player: the engine's view of a
    position does not depend on who reached it.
    """

    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)
        self._entries: dict[str, PositionKnowledge] | None = None

    @property
    def storage(self) -> Storage:
        return self._storage

    def _load(self) -> dict[str, PositionKnowledge]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, PositionKnowledge] = {}
        for line in self._storage.lines(FILENAME):
            try:
                record = PositionKnowledge.model_validate_json(line)
            except ValueError:
                continue
            entries[record.digest] = record
        self._entries = entries
        return entries

    def get(self, digest: str) -> PositionKnowledge | None:
        return self._load().get(digest)

    def known(self, digests: Iterable[str]) -> set[str]:
        entries = self._load()
        return {d for d in digests if d in entries}

    def extend(self, records: Iterable[PositionKnowledge]) -> int:
        entries = self._load()
        fresh = [r for r in records if r.digest not in entries]
        if not fresh:
            return 0
        self._storage.append(FILENAME, "".join(r.model_dump_json() + "\n" for r in fresh))
        for record in fresh:
            entries[record.digest] = record
        return len(fresh)

    def by_plan(self) -> dict[str, list[PositionKnowledge]]:
        grouped: dict[str, list[PositionKnowledge]] = defaultdict(list)
        for record in self._load().values():
            if record.plan_digest:
                grouped[record.plan_digest].append(record)
        return grouped

    def sharing_plan(
        self, plan_digest: str, *, exclude: str | None = None
    ) -> list[PositionKnowledge]:
        return [
            record
            for record in self._load().values()
            if record.plan_digest == plan_digest and record.digest != exclude
        ]

    def sharing_prefix(
        self, position: PositionKnowledge, *, minimum: int = 2
    ) -> tuple[int, list[PositionKnowledge]]:
        """Neighbours that share the longest opening stretch of the plan.

        A full plan is almost as unique as the position it came from: across 220
        studied positions only three plans recurred. The first two or three steps
        are what actually transfers, so this walks the plan back until it finds
        company and reports how much of it survived.
        """
        entries = [r for r in self._load().values() if r.digest != position.digest]
        total = len(position.plan_tokens.split())
        for steps in range(total, minimum - 1, -1):
            prefix = position.plan_prefix(steps)
            if not prefix:
                continue
            matches = [r for r in entries if r.plan_prefix(steps) == prefix]
            if matches:
                return steps, matches
        return 0, []

    def __len__(self) -> int:
        return len(self._load())
