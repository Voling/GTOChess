from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field


class LichessLink(BaseModel):
    """One account's connection to lichess, including its export token.

    The token used to be one file for everybody, which meant one person's
    credential exporting another person's games. It belongs to whoever
    authorised it and to nobody else.
    """

    model_config = ConfigDict(frozen=True)

    username: str
    access_token: str | None = None
    token_type: str = "Bearer"
    expires_at: int | None = None
    linked_at: int = Field(default_factory=lambda: int(time.time()))

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= int(time.time())

    @property
    def usable_token(self) -> str | None:
        return None if self.expired else self.access_token


class Account(BaseModel):
    """A person, keyed by the subject their identity provider issued.

    Held one object per subject rather than one file of all of them: two people
    editing their own record must not be a read, modify and write over a shared
    object, because the loser of that race disappears.
    """

    model_config = ConfigDict(frozen=True)

    subject: str
    email: str | None = None
    lichess: LichessLink | None = None
    created_at: int = Field(default_factory=lambda: int(time.time()))

    @property
    def player(self) -> str | None:
        return self.lichess.username if self.lichess else None

    def may_read(self, username: str) -> bool:
        """Whose games this account is allowed to look at: its own, and no others."""
        held = self.player
        return held is not None and held.lower() == username.lower()

    def linked_to(self, link: LichessLink) -> Account:
        return self.model_copy(update={"lichess": link})

    def unlinked(self) -> Account:
        return self.model_copy(update={"lichess": None})
