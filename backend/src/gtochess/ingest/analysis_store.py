from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from gtochess.storage import Storage, as_storage

if TYPE_CHECKING:
    from gtochess.llm.explain import Analysis

FILENAME = "analyses.jsonl"


class AnalysisStore:
    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)
        self._entries: dict[str, Analysis] | None = None

    @property
    def storage(self) -> Storage:
        return self._storage

    def _load(self) -> dict[str, Analysis]:
        from gtochess.llm.explain import Analysis

        if self._entries is not None:
            return self._entries
        entries: dict[str, Analysis] = {}
        for line in self._storage.lines(FILENAME):
            try:
                record = json.loads(line)
                entries[record["key"]] = Analysis.model_validate(record["analysis"])
            except (ValueError, KeyError):
                continue
        self._entries = entries
        return entries

    def get(self, key: str) -> Analysis | None:
        return self._load().get(key)

    def put(self, key: str, analysis: Analysis) -> None:
        entries = self._load()
        entries[key] = analysis
        body = json.dumps({"key": key, "analysis": analysis.model_dump(mode="json")})
        self._storage.append(FILENAME, body + "\n")

    def __len__(self) -> int:
        return len(self._load())
