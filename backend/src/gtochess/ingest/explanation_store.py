from __future__ import annotations

import json
from pathlib import Path

from gtochess.domain.explanations import Explanation
from gtochess.storage import Storage, as_storage

FILENAME = "explanations.jsonl"


class ExplanationStore:
    """Explanations on disk, keyed by position.

    A model call costs money, so a result outlives the process that paid for it
    and is shared by every player who reaches the same position.
    """

    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)
        self._entries: dict[str, Explanation] | None = None

    @property
    def storage(self) -> Storage:
        return self._storage

    def _load(self) -> dict[str, Explanation]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, Explanation] = {}
        for line in self._storage.lines(FILENAME):
            try:
                record = json.loads(line)
                entries[record["key"]] = Explanation.model_validate(record["explanation"])
            except (ValueError, KeyError):
                continue
        self._entries = entries
        return entries

    def get(self, key: str) -> Explanation | None:
        return self._load().get(key)

    def put(self, key: str, explanation: Explanation) -> None:
        entries = self._load()
        entries[key] = explanation
        body = json.dumps({"key": key, "explanation": explanation.model_dump(mode="json")})
        self._storage.append(FILENAME, body + "\n")

    def __len__(self) -> int:
        return len(self._load())
