from __future__ import annotations

import time

import pytest

from fiftymoves.api.auth import AuthError, SpendLimiter, _principal, bearer_token
from fiftymoves.config import Settings

POOL = "us-east-1_abc123"
CLIENT = "6a1clientid"


def settings() -> Settings:
    return Settings(
        cognito_user_pool_id=POOL,
        cognito_client_id=CLIENT,
        cognito_region="us-east-1",
    )


class TestBearer:
    def test_it_reads_a_bearer_header(self) -> None:
        assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_the_scheme_is_case_insensitive(self) -> None:
        assert bearer_token("bearer abc") == "abc"

    def test_another_scheme_is_not_a_token(self) -> None:
        assert bearer_token("Basic abc") is None

    def test_a_bare_word_is_not_a_token(self) -> None:
        assert bearer_token("abc") is None

    def test_nothing_is_not_a_token(self) -> None:
        assert bearer_token(None) is None
        assert bearer_token("") is None
        assert bearer_token("Bearer ") is None


class TestClaims:
    def test_an_access_token_for_this_client_is_accepted(self) -> None:
        who = _principal(
            {"sub": "u1", "client_id": CLIENT, "token_use": "access", "username": "dyl"},
            settings(),
        )
        assert who.subject == "u1"
        assert who.label == "dyl"

    def test_an_identity_token_carries_the_client_on_aud(self) -> None:
        who = _principal(
            {"sub": "u1", "aud": CLIENT, "token_use": "id", "email": "a@b.c"}, settings()
        )
        assert who.email == "a@b.c"

    def test_a_token_for_another_application_is_refused(self) -> None:
        with pytest.raises(AuthError, match="different application"):
            _principal(
                {"sub": "u1", "client_id": "someone-else", "token_use": "access"}, settings()
            )

    def test_a_refresh_token_cannot_buy_an_analysis(self) -> None:
        with pytest.raises(AuthError, match="access or identity"):
            _principal({"sub": "u1", "client_id": CLIENT, "token_use": "refresh"}, settings())

    def test_a_token_naming_nobody_is_refused(self) -> None:
        with pytest.raises(AuthError, match="names no subject"):
            _principal({"client_id": CLIENT, "token_use": "access"}, settings())

    def test_the_subject_is_the_fallback_label(self) -> None:
        who = _principal({"sub": "u1", "client_id": CLIENT, "token_use": "access"}, settings())
        assert who.label == "u1"


class TestSpendLimiter:
    def test_it_counts_down_from_the_ceiling(self) -> None:
        limiter = SpendLimiter(3)
        assert limiter.remaining("u1") == 3
        limiter.charge("u1")
        assert limiter.remaining("u1") == 2

    def test_the_ceiling_stops_the_next_call(self) -> None:
        limiter = SpendLimiter(2)
        limiter.charge("u1")
        limiter.charge("u1")
        with pytest.raises(AuthError, match="daily ceiling"):
            limiter.charge("u1")

    def test_one_account_cannot_spend_anothers_budget(self) -> None:
        limiter = SpendLimiter(1)
        limiter.charge("u1")
        limiter.charge("u2")
        assert limiter.remaining("u1") == 0
        assert limiter.remaining("u2") == 0

    def test_the_budget_comes_back_the_next_day(self) -> None:
        limiter = SpendLimiter(1)
        now = time.time()
        limiter.charge("u1", now=now)
        with pytest.raises(AuthError):
            limiter.charge("u1", now=now)
        limiter.charge("u1", now=now + 86_400)

    def test_a_ceiling_of_zero_permits_nothing(self) -> None:
        limiter = SpendLimiter(0)
        assert limiter.remaining("u1") == 0
        with pytest.raises(AuthError):
            limiter.charge("u1")


class TestConfiguration:
    def test_an_unconfigured_pool_trusts_no_token(self) -> None:
        from fiftymoves.api.auth import verify_token

        with pytest.raises(AuthError, match="no user pool"):
            verify_token("anything", settings=Settings(cognito_user_pool_id=None))

    def test_the_spending_endpoints_are_closed_unless_opened(self) -> None:
        # The shipped default, not whatever a local .env turned off.
        assert Settings.model_fields["auth_required"].default is True
