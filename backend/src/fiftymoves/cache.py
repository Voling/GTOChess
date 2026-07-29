from __future__ import annotations

from collections import OrderedDict


class LruCache[T]:
    def __init__(self, max_entries: int = 512) -> None:
        self._entries: OrderedDict[str, T] = OrderedDict()
        self._max_entries = max_entries

    def get(self, key: str) -> T | None:
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: T) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
