from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from fiftymoves.config import Settings, get_settings
from fiftymoves.ingest.tokens import StoredToken

AUTHORIZE_PATH = "/oauth"
TOKEN_PATH = "/api/token"
ACCOUNT_PATH = "/api/account"


class OAuthError(RuntimeError):
    pass


class PendingAuthorization(BaseModel):
    state: str
    verifier: str
    redirect_uri: str
    created_at: int


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_verifier() -> str:
    return _b64url(secrets.token_bytes(64))


def challenge_for(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


class LichessOAuth:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        redirect_uri: str,
        timeout_s: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._client = client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LichessOAuth:
        settings = settings or get_settings()
        return cls(
            base_url=settings.lichess_base_url,
            client_id=settings.lichess_client_id,
            redirect_uri=settings.lichess_redirect_uri,
            timeout_s=settings.lichess_timeout_s,
        )

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def start(self) -> tuple[str, PendingAuthorization]:
        pending = PendingAuthorization(
            state=_b64url(secrets.token_bytes(24)),
            verifier=make_verifier(),
            redirect_uri=self._redirect_uri,
            created_at=int(time.time()),
        )
        # Game export needs no scope; authenticating is purely for the higher
        # rate limit, so we ask for nothing the player has to think about.
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": challenge_for(pending.verifier),
                "state": pending.state,
            }
        )
        return f"{self._base_url}{AUTHORIZE_PATH}?{query}", pending

    def exchange(self, code: str, pending: PendingAuthorization) -> StoredToken:
        try:
            response = self._client.post(
                f"{self._base_url}{TOKEN_PATH}",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": pending.redirect_uri,
                    "client_id": self._client_id,
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach lichess: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise OAuthError(f"lichess rejected the code exchange ({response.status_code})")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise OAuthError("lichess returned no access token")

        expires_in = payload.get("expires_in")
        return StoredToken(
            access_token=access_token,
            token_type=payload.get("token_type", "Bearer"),
            expires_at=int(time.time()) + int(expires_in) if expires_in else None,
            username=self.whoami(access_token),
        )

    def whoami(self, access_token: str) -> str | None:
        try:
            response = self._client.get(
                f"{self._base_url}{ACCOUNT_PATH}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            return None
        if response.status_code != httpx.codes.OK:
            return None
        name = response.json().get("username")
        return str(name) if name else None

    def close(self) -> None:
        self._client.close()
