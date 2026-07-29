from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any

import httpx

from fiftymoves.config import Settings, get_settings

NDJSON = "application/x-ndjson"


class LichessError(RuntimeError):
    pass


class RateLimited(LichessError):
    pass


class LichessClient:
    def __init__(
        self,
        *,
        base_url: str = "https://lichess.org",
        token: str | None = None,
        user_agent: str = "FiftyMoves/0.1",
        timeout_s: float = 60.0,
        max_retries: int = 4,
        backoff_s: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._authenticated = bool(token)
        headers = {"User-Agent": user_agent}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout_s,
            follow_redirects=True,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LichessClient:
        from fiftymoves.ingest.tokens import resolve_token

        settings = settings or get_settings()
        return cls(
            base_url=settings.lichess_base_url,
            token=resolve_token(settings),
            user_agent=settings.user_agent,
            timeout_s=settings.lichess_timeout_s,
            max_retries=settings.lichess_max_retries,
            backoff_s=settings.lichess_backoff_s,
        )

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LichessClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _sleep_for(self, response: httpx.Response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return self._backoff_s

    def account(self, username: str) -> dict[str, Any]:
        response = self._client.get(f"/api/user/{username}")
        if response.status_code == 404:
            raise LichessError(f"no such account: {username}")
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    def export_user_games(
        self,
        username: str,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        max_games: int | None = None,
        rated: bool | None = None,
        perf_types: Sequence[str] = (),
        sort: str = "dateDesc",
        with_clocks: bool = True,
        with_evals: bool = True,
        analysed_only: bool | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "moves": "true",
            "tags": "false",
            "pgnInJson": "false",
            "opening": "true",
            "accuracy": "true",
            "lastFen": "true",
            "clocks": "true" if with_clocks else "false",
            "evals": "true" if with_evals else "false",
            "sort": sort,
        }
        if since_ms is not None:
            params["since"] = since_ms
        if until_ms is not None:
            params["until"] = until_ms
        if max_games is not None:
            params["max"] = max_games
        if rated is not None:
            params["rated"] = "true" if rated else "false"
        if perf_types:
            params["perfType"] = ",".join(perf_types)
        if analysed_only is not None:
            params["analysed"] = "true" if analysed_only else "false"

        yield from self._stream_ndjson(f"/api/games/user/{username}", params)

    def _stream_ndjson(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        attempt = 0
        while True:
            with self._client.stream(
                "GET", path, params=params, headers={"Accept": NDJSON}
            ) as response:
                if response.status_code == 429:
                    attempt += 1
                    if attempt > self._max_retries:
                        raise RateLimited(
                            f"rate limited by lichess after {self._max_retries} retries"
                        )
                    response.read()
                    time.sleep(self._sleep_for(response))
                    continue
                if response.status_code == 404:
                    raise LichessError(f"not found: {path}")
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.strip():
                        yield json.loads(line)
                return
