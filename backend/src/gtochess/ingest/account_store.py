from __future__ import annotations

import hashlib
import re
from pathlib import Path

from gtochess.domain.accounts import Account
from gtochess.storage import Storage, StorageError, as_storage


class AccountUnreadable(StorageError):
    """The record exists but does not parse. Never treated as absence."""


PREFIX = "accounts"
SAFE = re.compile(r"[^A-Za-z0-9_-]")


def name_for(subject: str) -> str:
    """One object per subject, so no two accounts ever write the same key.

    A Cognito subject is already a uuid, but it arrives from a token rather than
    from us, so it is not spliced into a path unchecked. Anything unexpected
    collapses to a digest of itself instead of escaping the prefix.
    """
    if not subject:
        raise ValueError("an account needs a subject")
    safe = SAFE.sub("", subject)
    if safe != subject or not safe:
        safe = hashlib.blake2b(subject.encode(), digest_size=16).hexdigest()
    return f"{PREFIX}/{safe}.json"


class AccountStore:
    def __init__(self, target: Storage | Path) -> None:
        self._storage = as_storage(target)

    @property
    def storage(self) -> Storage:
        return self._storage

    def get(self, subject: str) -> Account | None:
        """The record, or None when there genuinely is not one.

        A storage failure is deliberately not caught. Swallowing it here would
        turn an outage into "you have no account", which reads to a linked user
        as being told to link again, and would let `ensure` write over a record
        that was merely unreadable.
        """
        body = self._storage.read(name_for(subject))
        if body is None:
            return None
        try:
            return Account.model_validate_json(body)
        except ValueError as exc:
            # A partial write or an old schema. Loud, because the alternative is
            # silently replacing somebody's lichess link with a blank record.
            raise AccountUnreadable(f"the record for {subject} could not be read") from exc

    def upsert(self, account: Account) -> Account:
        # A token lives in here, so it is written the way a credential is.
        self._storage.write(name_for(account.subject), account.model_dump_json(), private=True)
        return account

    def ensure(self, subject: str, *, email: str | None = None) -> Account:
        held = self.get(subject)
        if held is not None:
            if email and held.email != email:
                return self.upsert(held.model_copy(update={"email": email}))
            return held
        return self.upsert(Account(subject=subject, email=email))
