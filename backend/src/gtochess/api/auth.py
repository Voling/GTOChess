from __future__ import annotations

import time
from typing import Any

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict

from gtochess.config import Settings, get_settings
from gtochess.domain.accounts import Account


class AuthError(Exception):
    pass


class Forbidden(AuthError):
    """Signed in, but not for this. A different status from not signed in."""


# Reachable without an account. Everything else under /api/ needs one, because
# building a graph over 28,000 games is real work even before a model is asked
# anything.
OPEN_PATHS = frozenset({"/health", "/api/auth/config", "/docs", "/openapi.json"})


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    username: str | None = None
    email: str | None = None

    @property
    def label(self) -> str:
        return self.username or self.email or self.subject


ANONYMOUS = Principal(subject="anonymous", username="anonymous")

_clients: dict[str, PyJWKClient] = {}


def guards(path: str) -> bool:
    return path not in OPEN_PATHS and path.startswith("/api/")


def authorize(header: str | None, settings: Settings, *, charge: bool = False) -> Principal:
    """Who is asking, and may they.

    ``charge`` is the difference between reading what has already been paid for
    and starting something that spends. Both need an account.
    """
    if not settings.auth_required:
        return ANONYMOUS
    token = bearer_token(header)
    if token is None:
        raise AuthError("sign in to continue")
    principal = verify_token(token, settings=settings)
    if charge:
        get_limiter().charge(principal.subject)
    return principal


def status_for(error: AuthError) -> int:
    if "ceiling" in str(error):
        return 429
    return 403 if isinstance(error, Forbidden) else 401


def readable_player(account: Account | None, username: str, settings: Settings) -> str:
    """The username this caller may look at.

    Verifying a token says who is asking. It does not say whose games they get,
    and until this existed any signed in account could read any player by typing
    a name into the box.
    """
    if not settings.auth_required:
        return username
    if account is None or account.player is None:
        raise Forbidden("link a lichess account before reading a repertoire")
    if not account.may_read(username):
        raise Forbidden(f"that account is linked to {account.player}, not {username}")
    return account.player


def issuer_for(settings: Settings) -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )


def jwk_client(settings: Settings) -> PyJWKClient:
    issuer = issuer_for(settings)
    client = _clients.get(issuer)
    if client is None:
        # The client caches keys itself; Cognito rotates rarely and a fetch per
        # request would put a network hop in front of every analysis.
        client = PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True)
        _clients[issuer] = client
    return client


def reset_clients() -> None:
    _clients.clear()


def verify_token(token: str, *, settings: Settings | None = None) -> Principal:
    settings = settings or get_settings()
    if not settings.cognito_user_pool_id or not settings.cognito_client_id:
        raise AuthError("no user pool is configured, so no token can be trusted")

    try:
        key = jwk_client(settings).get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer_for(settings),
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"that token was rejected: {exc}") from exc
    except Exception as exc:
        raise AuthError("the signing keys could not be fetched") from exc

    return _principal(claims, settings)


def _principal(claims: dict[str, Any], settings: Settings) -> Principal:
    # Cognito puts the client on `client_id` for an access token and on `aud` for
    # an id token. Checking whichever is present stops a token minted for another
    # application in the same pool from being replayed here.
    audience = claims.get("client_id") or claims.get("aud")
    if audience != settings.cognito_client_id:
        raise AuthError("that token was issued for a different application")

    if claims.get("token_use") not in ("access", "id"):
        raise AuthError("that is not an access or identity token")

    subject = claims.get("sub")
    if not subject:
        raise AuthError("that token names no subject")

    return Principal(
        subject=str(subject),
        username=claims.get("username") or claims.get("cognito:username"),
        email=claims.get("email"),
    )


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class SpendLimiter:
    """Caps how many paid calls one account can start in a day.

    Authenticating says who is spending; it does not say how much. A signed-in
    account holding the button down is the same bill as an anonymous one, so the
    ceiling is per subject and per day.
    """

    def __init__(self, per_day: int) -> None:
        self._per_day = per_day
        self._spent: dict[tuple[str, int], int] = {}

    @staticmethod
    def _today(now: float | None = None) -> int:
        return int((now if now is not None else time.time()) // 86_400)

    def remaining(self, subject: str, *, now: float | None = None) -> int:
        if self._per_day <= 0:
            return 0
        used = self._spent.get((subject, self._today(now)), 0)
        return max(0, self._per_day - used)

    def charge(self, subject: str, *, now: float | None = None) -> None:
        if self.remaining(subject, now=now) <= 0:
            raise AuthError(
                f"that account has started {self._per_day} analyses today, "
                "which is the daily ceiling"
            )
        key = (subject, self._today(now))
        self._spent[key] = self._spent.get(key, 0) + 1

    def refund(self, subject: str, *, now: float | None = None) -> None:
        """Gives a charge back when the work it paid for never happened.

        The charge lands before the handler runs, so without this a request that
        404s on an unknown position, or serves an already cached answer, still
        costs one of the caller's analyses for the day.
        """
        key = (subject, self._today(now))
        held = self._spent.get(key, 0)
        if held > 0:
            self._spent[key] = held - 1

    def forget(self) -> None:
        self._spent.clear()


_limiter: SpendLimiter | None = None


def get_limiter() -> SpendLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SpendLimiter(get_settings().analysis_daily_limit)
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None
