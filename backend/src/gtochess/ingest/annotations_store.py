from __future__ import annotations

import hashlib
from pathlib import Path

from gtochess.domain.annotations import AnnotationSet
from gtochess.domain.games import Side
from gtochess.ingest.pipeline import player_key
from gtochess.storage import Storage, StorageError, as_storage


def shape_key(
    username: str, side: Side, max_ply: int, min_volume: int, max_children: int, stamp: int
) -> str:
    raw = f"{player_key(username)}:{side.value}:{max_ply}:{min_volume}:{max_children}:{stamp}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


class AnnotationStore:
    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)

    def name_for(self, username: str, shape: str) -> str:
        return f"{player_key(username)}.annotations.{shape}.json"

    def read(self, username: str, shape: str) -> AnnotationSet | None:
        try:
            body = self._storage.read(self.name_for(username, shape))
        except StorageError:
            return None
        if body is None:
            return None
        try:
            return AnnotationSet.model_validate_json(body)
        except ValueError:
            return None

    def write(self, annotations: AnnotationSet) -> str:
        name = self.name_for(annotations.username, annotations.shape)
        self._storage.write(name, annotations.model_dump_json())
        return name
