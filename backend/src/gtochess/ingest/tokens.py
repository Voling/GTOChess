from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from gtochess.config import Settings, get_settings
from gtochess.storage import Storage, StorageError, as_storage

TOKEN_FILENAME = "lichess_token.json"


class StoredToken(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: int | None = None
    username: str | None = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= int(time.time())


class TokenStore:
    def __init__(self, target: Storage | Path, *, name: str = TOKEN_FILENAME) -> None:
        self._storage = as_storage(target)
        self._name = name

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> TokenStore:
        from gtochess.storage import build_storage

        return cls(build_storage(settings or get_settings()))

    @property
    def storage(self) -> Storage:
        return self._storage

    def read(self) -> StoredToken | None:
        try:
            body = self._storage.read(self._name)
        except StorageError:
            return None
        if body is None:
            return None
        try:
            return StoredToken.model_validate_json(body)
        except ValueError:
            return None

    def write(self, token: StoredToken) -> None:
        self._storage.write(self._name, token.model_dump_json(), private=True)

    def clear(self) -> None:
        self._storage.delete(self._name)


def resolve_token(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if settings.lichess_token:
        return settings.lichess_token
    stored = TokenStore.from_settings(settings).read()
    if stored is None or stored.expired:
        return None
    return stored.access_token
