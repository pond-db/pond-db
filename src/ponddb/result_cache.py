"""ResultCache — in-memory query result cache with TTL."""

import hashlib
import time
from typing import Any, Optional


class ResultCache:
    """In-memory cache for query results, keyed by SQL + dataset version."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        # key -> (data, expires_at_monotonic)
        self._store: dict[str, tuple[Any, float]] = {}

    def make_key(self, sql: str, dataset_version: str) -> str:
        """Return a deterministic hash key for the given SQL and dataset version."""
        raw = f"{sql}\x00{dataset_version}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Return cached data, or None if missing or expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        data, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: Any) -> None:
        """Store data under key with TTL."""
        expires_at = time.monotonic() + self.ttl_seconds
        self._store[key] = (data, expires_at)

    def invalidate(self, key: str) -> None:
        """Remove a specific entry (no-op if absent)."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Evict all cached entries."""
        self._store.clear()
