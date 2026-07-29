from __future__ import annotations

import os
import stat
import time
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel

from fiftymoves.config import Settings, get_settings

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
    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> TokenStore:
        settings = settings or get_settings()
        return cls(settings.data_dir / TOKEN_FILENAME)

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> StoredToken | None:
        if not self._path.exists():
            return None
        try:
            return StoredToken.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def write(self, token: StoredToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(token.model_dump_json(), encoding="utf-8")
        # Best effort on platforms with POSIX permissions; a no-op on Windows.
        with suppress(OSError):
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def resolve_token(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if settings.lichess_token:
        return settings.lichess_token
    stored = TokenStore.from_settings(settings).read()
    if stored is None or stored.expired:
        return None
    return stored.access_token
