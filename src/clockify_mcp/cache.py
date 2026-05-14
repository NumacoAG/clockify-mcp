"""Tiny TTL cache used for project/workspace/tag lookups."""

from __future__ import annotations

import time
from collections.abc import Hashable
from threading import Lock


class TTLCache[K: Hashable, V]:
    """Thread-safe TTL cache. Entries expire after `ttl_seconds` seconds."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._store: dict[K, tuple[float, V]] = {}
        self._lock = Lock()

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires, value = entry
            if time.monotonic() > expires:
                del self._store[key]
                return None
            return value

    def set(self, key: K, value: V) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
