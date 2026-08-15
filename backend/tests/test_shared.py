from __future__ import annotations

import time
from typing import Any

import pytest

from gtochess.api.auth import AuthError, SpendLimiter
from gtochess.config import Settings
from gtochess.ingest.oauth import PendingAuthorization, PendingStore
from gtochess.shared import MemoryState, RedisState, build_shared


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    def incr(self, key: str) -> None:
        self._queued.append(("incr", (key,)))

    def expire(self, key: str, ttl_s: int) -> None:
        self._queued.append(("expire", (key, ttl_s)))

    def execute(self) -> list[Any]:
        return [getattr(self._redis, name)(*args) for name, args in self._queued]


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def incr(self, key: str) -> int:
        count = int(self.values.get(key, "0")) + 1
        self.values[key] = str(count)
        return count

    def decr(self, key: str) -> int:
        count = int(self.values.get(key, "0")) - 1
        self.values[key] = str(count)
        return count

    def expire(self, key: str, ttl_s: int) -> bool:
        self.ttls[key] = ttl_s
        return True

    def delete(self, key: str) -> int:
        self.ttls.pop(key, None)
        return 1 if self.values.pop(key, None) is not None else 0

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, ttl_s: int, value: str) -> bool:
        self.values[key] = value
        self.ttls[key] = ttl_s
        return True

    def getdel(self, key: str) -> str | None:
        self.ttls.pop(key, None)
        return self.values.pop(key, None)


class BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        raise ConnectionError("redis is not answering")


class TestMemoryState:
    def test_a_bump_counts_up(self) -> None:
        state = MemoryState()
        assert state.bump("k", ttl_s=60) == 1
        assert state.bump("k", ttl_s=60) == 2
        assert state.count("k") == 2

    def test_an_unknown_key_counts_zero(self) -> None:
        assert MemoryState().count("k") == 0

    def test_easing_gives_one_back(self) -> None:
        state = MemoryState()
        state.bump("k", ttl_s=60)
        state.bump("k", ttl_s=60)
        state.ease("k")
        assert state.count("k") == 1

    def test_easing_past_zero_does_not_go_negative(self) -> None:
        state = MemoryState()
        state.ease("k")
        assert state.count("k") == 0

    def test_a_held_value_comes_back(self) -> None:
        state = MemoryState()
        state.hold("k", "value", ttl_s=60)
        assert state.take("k") == "value"

    def test_taking_is_a_one_shot(self) -> None:
        state = MemoryState()
        state.hold("k", "value", ttl_s=60)
        state.take("k")
        assert state.take("k") is None

    def test_an_expired_count_is_gone(self) -> None:
        state = MemoryState()
        state.bump("k", ttl_s=0)
        assert state.count("k") == 0

    def test_an_expired_value_is_gone(self) -> None:
        state = MemoryState()
        state.hold("k", "value", ttl_s=0)
        assert state.take("k") is None

    def test_a_second_bump_does_not_push_the_window_out(self) -> None:
        # A window that opened an hour ago has to close on time, or a caller who
        # keeps pressing never reaches the end of their day.
        state = MemoryState()
        state.bump("k", ttl_s=3600)
        before = state._values["k"][0]
        state.bump("k", ttl_s=3600)
        assert state._values["k"][0] == before


class TestRedisState:
    def test_a_bump_increments_and_sets_a_ttl(self) -> None:
        redis = FakeRedis()
        state = RedisState("redis://x", client=redis)
        assert state.bump("k", ttl_s=90) == 1
        assert redis.values["k"] == "1"
        assert redis.ttls["k"] == 90

    def test_easing_past_zero_drops_the_key(self) -> None:
        redis = FakeRedis()
        state = RedisState("redis://x", client=redis)
        state.ease("k")
        assert "k" not in redis.values
        assert state.count("k") == 0

    def test_a_value_round_trips_and_is_taken_once(self) -> None:
        state = RedisState("redis://x", client=FakeRedis())
        state.hold("k", "value", ttl_s=60)
        assert state.take("k") == "value"
        assert state.take("k") is None

    def test_a_live_redis_is_not_degraded(self) -> None:
        state = RedisState("redis://x", client=FakeRedis())
        state.bump("k", ttl_s=60)
        assert state.degraded is False
        assert state.label == "redis"

    def test_an_unreachable_redis_falls_back_to_this_process(self) -> None:
        state = RedisState("redis://x", client=BrokenRedis())
        assert state.bump("k", ttl_s=60) == 1
        assert state.count("k") == 1
        state.hold("v", "value", ttl_s=60)
        assert state.take("v") == "value"

    def test_the_fallback_says_so(self) -> None:
        state = RedisState("redis://x", client=BrokenRedis())
        state.count("k")
        assert state.degraded is True
        assert state.label == "redis (degraded to memory)"

    def test_it_stops_saying_so_once_redis_answers(self) -> None:
        state = RedisState("redis://x", client=BrokenRedis())
        state.count("k")
        state._client = FakeRedis()
        state.count("k")
        assert state.degraded is False


class TestBuildShared:
    def test_a_configured_redis_is_used(self) -> None:
        assert build_shared(Settings(redis_url="redis://localhost:6379/0")).label == "redis"

    def test_no_redis_url_stays_in_this_process(self) -> None:
        assert build_shared(Settings(redis_url="")).label == "memory"


class TestSharedCeiling:
    def test_two_containers_share_one_budget(self) -> None:
        # The regression this exists for: each process holding its own count
        # handed the same account the ceiling twice over.
        state = MemoryState()
        first = SpendLimiter(2, state)
        second = SpendLimiter(2, state)
        first.charge("u1")
        second.charge("u1")
        assert first.remaining("u1") == 0
        assert second.remaining("u1") == 0

    def test_a_refund_on_one_container_is_seen_on_the_other(self) -> None:
        state = MemoryState()
        first = SpendLimiter(1, state)
        second = SpendLimiter(1, state)
        first.charge("u1")
        first.refund("u1")
        second.charge("u1")

    def test_the_charge_is_keyed_by_the_day(self) -> None:
        state = MemoryState()
        limiter = SpendLimiter(5, state)
        now = time.time()
        limiter.charge("u1", now=now)
        assert limiter.remaining("u1", now=now + 86_400) == 5

    def test_a_ceiling_of_zero_never_touches_the_counter(self) -> None:
        state = MemoryState()
        limiter = SpendLimiter(0, state)
        with pytest.raises(AuthError):
            limiter.charge("u1")
        assert state._values == {}


class TestPendingStore:
    def make(self) -> tuple[PendingStore, PendingAuthorization]:
        pending = PendingAuthorization(
            state="s1", verifier="v1", redirect_uri="http://x/cb", created_at=0
        )
        return PendingStore(MemoryState(), ttl_s=600), pending

    def test_a_started_authorisation_comes_back(self) -> None:
        store, pending = self.make()
        store.hold(pending)
        held = store.take("s1")
        assert held is not None
        assert held.verifier == "v1"
        assert held.redirect_uri == "http://x/cb"

    def test_a_state_cannot_be_replayed(self) -> None:
        store, pending = self.make()
        store.hold(pending)
        store.take("s1")
        assert store.take("s1") is None

    def test_an_unknown_state_is_nothing(self) -> None:
        store, _ = self.make()
        assert store.take("never-issued") is None

    def test_a_callback_reaches_the_container_that_did_not_start_it(self) -> None:
        state = MemoryState()
        started = PendingStore(state, ttl_s=600)
        answered = PendingStore(state, ttl_s=600)
        pending = PendingAuthorization(
            state="s1", verifier="v1", redirect_uri="http://x/cb", created_at=0
        )
        started.hold(pending)
        held = answered.take("s1")
        assert held is not None
        assert held.verifier == "v1"

    def test_an_unreadable_record_is_nothing(self) -> None:
        state = MemoryState()
        state.hold("gtochess:oauth:lichess:s1", "{not json", ttl_s=600)
        assert PendingStore(state, ttl_s=600).take("s1") is None
