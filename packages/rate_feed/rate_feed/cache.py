"""In-process TTL cache that also retains expired entries.

Keeping the stale value after expiry is deliberate: when FRED is unreachable,
yesterday's real mortgage rate labelled "stale" is far more useful than a
synthetic one, so the service falls back through staleness before it falls back
to synthetic data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float
    stored_at: float


class TTLCache(Generic[T]):
    def __init__(self, max_entries: int = 512):
        self._data: dict[str, _Entry[T]] = {}
        self._max = max_entries

    def get_fresh(self, key: str) -> T | None:
        entry = self._data.get(key)
        if entry and time.time() < entry.expires_at:
            return entry.value
        return None

    def get_stale(self, key: str) -> T | None:
        """Any retained value, expired or not."""
        entry = self._data.get(key)
        return entry.value if entry else None

    def age_seconds(self, key: str) -> float | None:
        entry = self._data.get(key)
        return time.time() - entry.stored_at if entry else None

    def set(self, key: str, value: T, ttl: float) -> None:
        if len(self._data) >= self._max and key not in self._data:
            oldest = min(self._data, key=lambda k: self._data[k].stored_at)
            del self._data[oldest]
        now = time.time()
        self._data[key] = _Entry(value=value, expires_at=now + ttl, stored_at=now)

    def clear(self) -> None:
        self._data.clear()

    def stats(self) -> dict[str, Any]:
        now = time.time()
        return {
            "entries": len(self._data),
            "fresh": sum(1 for e in self._data.values() if now < e.expires_at),
        }
