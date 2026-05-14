"""Tests for the TTL cache."""

from __future__ import annotations

import time

from clockify_mcp.cache import TTLCache


def test_set_and_get() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60)
    cache.set("foo", 42)
    assert cache.get("foo") == 42


def test_missing_key_returns_none() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60)
    assert cache.get("absent") is None


def test_expired_entry_is_evicted(monkeypatch) -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=1)
    fake_now = [time.monotonic()]
    monkeypatch.setattr("clockify_mcp.cache.time.monotonic", lambda: fake_now[0])
    cache.set("foo", 1)
    assert cache.get("foo") == 1
    fake_now[0] += 2
    assert cache.get("foo") is None
    assert len(cache) == 0


def test_clear_removes_all() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert len(cache) == 0
