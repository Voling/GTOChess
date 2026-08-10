from __future__ import annotations

from pathlib import Path

import pytest

from gtochess.api.auth import Forbidden, readable_player, status_for
from gtochess.api.main import (
    ACCOUNT_CACHE_MAX,
    ACCOUNT_TTL_S,
    MISSING,
    _accounts,
    cached_account,
    forget_accounts,
    hold_account,
)
from gtochess.config import Settings
from gtochess.domain.accounts import Account, LichessLink
from gtochess.domain.games import Side
from gtochess.ingest.account_store import AccountStore, AccountUnreadable, name_for
from gtochess.ingest.annotations_store import shape_key
from gtochess.ingest.pipeline import games_name, nodes_name
from gtochess.storage import LocalStorage, StorageError


def closed() -> Settings:
    return Settings(auth_required=True)


class TestNaming:
    def test_one_object_per_subject(self) -> None:
        assert name_for("abc-123") == "accounts/abc-123.json"

    def test_two_subjects_never_share_a_key(self) -> None:
        assert name_for("a") != name_for("b")

    def test_a_subject_cannot_escape_the_prefix(self) -> None:
        # It arrives from a token rather than from us, so it is never spliced in raw.
        name = name_for("../../secrets")
        assert name.startswith("accounts/")
        assert ".." not in name

    def test_a_slash_does_not_make_a_directory(self) -> None:
        assert name_for("a/b").count("/") == 1

    def test_an_escaped_subject_is_still_stable(self) -> None:
        assert name_for("../x") == name_for("../x")

    def test_no_subject_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="needs a subject"):
            name_for("")


class TestAccount:
    def test_an_unlinked_account_reads_nobody(self) -> None:
        assert Account(subject="u1").may_read("dylanette") is False

    def test_it_reads_the_player_it_is_linked_to(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="dylanette"))
        assert held.may_read("dylanette") is True

    def test_lichess_names_are_matched_without_case(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="DylaNette"))
        assert held.may_read("dylanette") is True

    def test_it_reads_nobody_else(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="dylanette"))
        assert held.may_read("magnuscarlsen") is False

    def test_unlinking_drops_the_token_with_the_name(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="d", access_token="secret"))
        assert held.unlinked().lichess is None

    def test_an_expired_token_is_not_offered(self) -> None:
        link = LichessLink(username="d", access_token="secret", expires_at=1)
        assert link.expired is True
        assert link.usable_token is None

    def test_a_live_token_is_offered(self) -> None:
        link = LichessLink(username="d", access_token="secret", expires_at=4_102_444_800)
        assert link.usable_token == "secret"


class TestStore:
    def test_an_unknown_subject_is_nothing(self, tmp_path: Path) -> None:
        assert AccountStore(tmp_path).get("nobody") is None

    def test_it_round_trips(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        store.upsert(Account(subject="u1", email="a@b.c"))
        assert store.get("u1").email == "a@b.c"  # type: ignore[union-attr]

    def test_ensure_creates_once_and_keeps_the_created_time(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        first = store.ensure("u1", email="a@b.c")
        again = store.ensure("u1", email="a@b.c")
        assert again.created_at == first.created_at

    def test_ensure_does_not_wipe_a_link(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        store.upsert(Account(subject="u1", lichess=LichessLink(username="d")))
        assert store.ensure("u1").player == "d"

    def test_a_changed_email_is_carried_over(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        store.upsert(Account(subject="u1", email="old@b.c", lichess=LichessLink(username="d")))
        fresh = store.ensure("u1", email="new@b.c")
        assert fresh.email == "new@b.c"
        assert fresh.player == "d"

    def test_two_accounts_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        store.upsert(Account(subject="u1", lichess=LichessLink(username="one")))
        store.upsert(Account(subject="u2", lichess=LichessLink(username="two")))
        assert store.get("u1").player == "one"  # type: ignore[union-attr]
        assert store.get("u2").player == "two"  # type: ignore[union-attr]

    def test_an_absent_record_is_absence_not_an_error(self, tmp_path: Path) -> None:
        assert AccountStore(tmp_path).get("never-seen") is None


class TestReadablePlayer:
    def test_an_unlinked_account_reads_no_repertoire(self) -> None:
        with pytest.raises(Forbidden, match="link a lichess account"):
            readable_player(Account(subject="u1"), "dylanette", closed())

    def test_no_account_at_all_is_refused(self) -> None:
        with pytest.raises(Forbidden):
            readable_player(None, "dylanette", closed())

    def test_reading_somebody_else_is_refused(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="dylanette"))
        with pytest.raises(Forbidden, match="not magnuscarlsen"):
            readable_player(held, "magnuscarlsen", closed())

    def test_reading_your_own_is_allowed(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="dylanette"))
        assert readable_player(held, "dylanette", closed()) == "dylanette"

    def test_a_refusal_is_forbidden_not_unauthorised(self) -> None:
        # 401 says sign in, which is wrong advice when you already have.
        assert status_for(Forbidden("nope")) == 403

    def test_auth_off_lets_local_work_carry_on(self) -> None:
        assert readable_player(None, "anyone", Settings(auth_required=False)) == "anyone"


class TestAccountCache:
    def teardown_method(self) -> None:
        forget_accounts()

    def test_nothing_held_is_distinct_from_no_record(self) -> None:
        # Held-as-absent and never-looked-up must not look the same, or the
        # absent case can never be cached.
        assert cached_account("u1") is MISSING
        hold_account("u1", None)
        assert cached_account("u1") is None

    def test_a_held_account_is_returned(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="d"))
        hold_account("u1", held)
        assert cached_account("u1") == held

    def test_it_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Otherwise a link made on one API task never becomes visible on another.
        clock = [1000.0]
        monkeypatch.setattr("gtochess.api.main.time.monotonic", lambda: clock[0])
        hold_account("u1", Account(subject="u1"))
        clock[0] += ACCOUNT_TTL_S + 1
        assert cached_account("u1") is MISSING

    def test_an_absent_record_expires_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = [1000.0]
        monkeypatch.setattr("gtochess.api.main.time.monotonic", lambda: clock[0])
        hold_account("u1", None)
        clock[0] += ACCOUNT_TTL_S + 1
        assert cached_account("u1") is MISSING

    def test_it_survives_inside_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = [1000.0]
        monkeypatch.setattr("gtochess.api.main.time.monotonic", lambda: clock[0])
        hold_account("u1", Account(subject="u1"))
        clock[0] += ACCOUNT_TTL_S - 1
        assert cached_account("u1") is not MISSING

    def test_a_relink_replaces_what_is_held(self) -> None:
        hold_account("u1", Account(subject="u1"))
        hold_account("u1", Account(subject="u1", lichess=LichessLink(username="d")))
        assert cached_account("u1").player == "d"  # type: ignore[union-attr]

    def test_it_does_not_grow_without_bound(self) -> None:
        for index in range(ACCOUNT_CACHE_MAX + 5):
            hold_account(f"u{index}", Account(subject=f"u{index}"))
        assert len(_accounts) <= ACCOUNT_CACHE_MAX


class TestUnreadableRecords:
    def test_a_storage_failure_is_not_reported_as_no_account(self, tmp_path: Path) -> None:
        # Swallowing it would tell a linked user to link again during an outage.
        class Broken(LocalStorage):
            def read(self, name: str) -> str | None:
                raise StorageError("s3 is having a day")

        with pytest.raises(StorageError):
            AccountStore(Broken(tmp_path)).get("u1")

    def test_a_corrupt_record_is_loud(self, tmp_path: Path) -> None:
        store = AccountStore(tmp_path)
        LocalStorage(tmp_path).write(name_for("u1"), "{ truncated")
        with pytest.raises(AccountUnreadable):
            store.get("u1")

    def test_ensure_never_writes_over_a_record_it_could_not_read(self, tmp_path: Path) -> None:
        # The failure mode this guards: a blank Account replacing a live lichess link.
        store = AccountStore(tmp_path)
        LocalStorage(tmp_path).write(name_for("u1"), "{ truncated")
        with pytest.raises(AccountUnreadable):
            store.ensure("u1")
        assert LocalStorage(tmp_path).read(name_for("u1")) == "{ truncated"


class TestPlayerKey:
    def test_case_does_not_make_a_second_import(self) -> None:
        assert games_name("Dylanette") == games_name("dylanette")

    def test_nodes_follow_the_same_key(self) -> None:
        assert nodes_name("Dylanette") == nodes_name("dylanette")

    def test_annotations_follow_the_same_key(self) -> None:
        stamp = 1
        first = shape_key("Dylanette", Side.WHITE, 10, 25, 4, stamp)
        second = shape_key("dylanette", Side.WHITE, 10, 25, 4, stamp)
        assert first == second

    def test_surrounding_space_is_not_a_different_player(self) -> None:
        assert games_name(" dylanette ") == games_name("dylanette")

    def test_the_authorised_spelling_and_the_key_agree(self) -> None:
        held = Account(subject="u1", lichess=LichessLink(username="Dylanette"))
        allowed = readable_player(held, "dylanette", closed())
        assert games_name(allowed) == games_name("dylanette")
