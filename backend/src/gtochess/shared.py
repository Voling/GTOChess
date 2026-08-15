from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from gtochess.config import Settings, get_settings

_shared: SharedState | None = None

CONNECT_TIMEOUT_S = 2.0


class SharedState(Protocol):
    @property
    def label(self) -> str: ...

    def bump(self, key: str, *, ttl_s: int) -> int: ...

    def ease(self, key: str) -> None: ...

    def count(self, key: str) -> int: ...

    def hold(self, key: str, value: str, *, ttl_s: int) -> None: ...

    def take(self, key: str) -> str | None: ...


class MemoryState:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}

    @property
    def label(self) -> str:
        return "memory"

    def _live(self, key: str) -> tuple[float, str] | None:
        held = self._values.get(key)
        if held is None:
            return None
        if held[0] <= time.time():
            del self._values[key]
            return None
        return held

    def bump(self, key: str, *, ttl_s: int) -> int:
        held = self._live(key)
        expires = held[0] if held else time.time() + ttl_s
        count = int(held[1]) + 1 if held else 1
        self._values[key] = (expires, str(count))
        return count

    def ease(self, key: str) -> None:
        held = self._live(key)
        if held is None:
            return
        count = int(held[1]) - 1
        if count <= 0:
            del self._values[key]
            return
        self._values[key] = (held[0], str(count))

    def count(self, key: str) -> int:
        held = self._live(key)
        return int(held[1]) if held else 0

    def hold(self, key: str, value: str, *, ttl_s: int) -> None:
        self._values[key] = (time.time() + ttl_s, value)

    def take(self, key: str) -> str | None:
        held = self._live(key)
        self._values.pop(key, None)
        return held[1] if held else None

    def clear(self) -> None:
        self._values.clear()


class RedisState:
    def __init__(self, url: str, *, client: Any | None = None) -> None:
        self._url = url
        self._client = client
        self._standby = MemoryState()
        self._degraded = False

    @property
    def label(self) -> str:
        return "redis (degraded to memory)" if self._degraded else "redis"

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=CONNECT_TIMEOUT_S,
                socket_timeout=CONNECT_TIMEOUT_S,
            )
        return self._client

    def _guard[T](self, live: Callable[[Any], T], standby: Callable[[], T]) -> T:
        try:
            value = live(self.client)
        except Exception:
            self._degraded = True
            return standby()
        self._degraded = False
        return value

    def bump(self, key: str, *, ttl_s: int) -> int:
        def _incr(client: Any) -> int:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_s)
            return int(pipe.execute()[0])

        return self._guard(_incr, lambda: self._standby.bump(key, ttl_s=ttl_s))

    def ease(self, key: str) -> None:
        def _decr(client: Any) -> None:
            if int(client.decr(key)) < 0:
                client.delete(key)

        self._guard(_decr, lambda: self._standby.ease(key))

    def count(self, key: str) -> int:
        def _get(client: Any) -> int:
            held = client.get(key)
            return int(held) if held else 0

        return self._guard(_get, lambda: self._standby.count(key))

    def hold(self, key: str, value: str, *, ttl_s: int) -> None:
        self._guard(
            lambda client: client.setex(key, ttl_s, value),
            lambda: self._standby.hold(key, value, ttl_s=ttl_s),
        )

    def take(self, key: str) -> str | None:
        def _getdel(client: Any) -> str | None:
            held = client.getdel(key)
            return str(held) if held is not None else None

        return self._guard(_getdel, lambda: self._standby.take(key))


def build_shared(settings: Settings | None = None) -> SharedState:
    settings = settings or get_settings()
    if not settings.redis_url:
        return MemoryState()
    return RedisState(settings.redis_url)


def get_shared() -> SharedState:
    global _shared
    if _shared is None:
        _shared = build_shared()
    return _shared


def set_shared(state: SharedState | None) -> None:
    global _shared
    _shared = state
