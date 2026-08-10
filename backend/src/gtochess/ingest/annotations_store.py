from __future__ import annotations

import hashlib
from pathlib import Path

from gtochess.domain.annotations import AnnotationSet
from gtochess.domain.games import Side
from gtochess.ingest.pipeline import player_key
from gtochess.storage import Storage, StorageError, as_storage


class AnnotationsUnreadable(StorageError):
    """The record exists but does not parse. Never treated as absence."""


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
        """The stored marks, or None when none have been made.

        A storage failure is deliberately not caught. Reporting an outage as
        "nothing annotated yet" makes the client re-queue a full engine sweep
        over the whole graph, which is the most expensive possible answer to a
        transient read error.
        """
        body = self._storage.read(self.name_for(username, shape))
        if body is None:
            return None
        try:
            return AnnotationSet.model_validate_json(body)
        except ValueError as exc:
            raise AnnotationsUnreadable(
                f"the annotations for {username} at shape {shape} could not be read"
            ) from exc

    def write(self, annotations: AnnotationSet) -> str:
        name = self.name_for(annotations.username, annotations.shape)
        self._storage.write(name, annotations.model_dump_json())
        return name
