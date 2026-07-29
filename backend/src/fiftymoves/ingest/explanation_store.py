from __future__ import annotations

import json
from pathlib import Path

from fiftymoves.domain.explanations import Explanation

FILENAME = "explanations.jsonl"


class ExplanationStore:
    """Explanations on disk, keyed by position.

    A model call costs money, so a result outlives the process that paid for it
    and is shared by every player who reaches the same position.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / FILENAME
        self._entries: dict[str, Explanation] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Explanation]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, Explanation] = {}
        if self._path.exists():
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"key": key, "explanation": explanation.model_dump(mode="json")}) + "\n"
            )

    def __len__(self) -> int:
        return len(self._load())
