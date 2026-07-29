from __future__ import annotations

import hashlib
from pathlib import Path

from fiftymoves.domain.annotations import AnnotationSet
from fiftymoves.domain.games import Side


def shape_key(
    username: str, side: Side, max_ply: int, min_volume: int, max_children: int, stamp: int
) -> str:
    raw = f"{username}:{side.value}:{max_ply}:{min_volume}:{max_children}:{stamp}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


class AnnotationStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path_for(self, username: str, shape: str) -> Path:
        return self._directory / f"{username}.annotations.{shape}.json"

    def read(self, username: str, shape: str) -> AnnotationSet | None:
        path = self.path_for(username, shape)
        if not path.exists():
            return None
        try:
            return AnnotationSet.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def write(self, annotations: AnnotationSet) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(annotations.username, annotations.shape)
        path.write_text(annotations.model_dump_json(), encoding="utf-8")
        return path
