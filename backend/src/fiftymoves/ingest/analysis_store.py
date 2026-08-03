from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiftymoves.llm.explain import Analysis

FILENAME = "analyses.jsonl"


class AnalysisStore:
    def __init__(self, directory: Path) -> None:
        self._path = directory / FILENAME
        self._entries: dict[str, Analysis] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Analysis]:
        from fiftymoves.llm.explain import Analysis

        if self._entries is not None:
            return self._entries
        entries: dict[str, Analysis] = {}
        if self._path.exists():
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"key": key, "analysis": analysis.model_dump(mode="json")}) + "\n"
            )

    def __len__(self) -> int:
        return len(self._load())
