"""Tiny thread-safe TTL map, shared by the per-request caches.

Extracted from auth/auth_cache.py (0276 T0009) so db/meta_cache.py (0282 NR0003
발견 2) can reuse it without importing the auth package — auth's __init__ pulls
in middleware/auth_api, which import db modules, and db importing auth back
would be a circular import at package-init time.

Each instance carries its own ``ttl_fn`` so every consumer keeps its own TTL
policy (env var, disable switch) while sharing the mechanics: monotonic clock
(system time changes cannot extend an entry's life), expired-entry eviction on
read, and a loader helper that runs outside the lock.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

MISS = object()


class TTLCache:
    def __init__(self, ttl_fn: Callable[[], float]) -> None:
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._ttl_fn = ttl_fn

    def get(self, key: Any) -> Any:
        ttl = self._ttl_fn()
        if ttl <= 0:
            return MISS
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return MISS
            expires_at, value = entry
            if expires_at <= now:
                # Expired: drop it so the map cannot grow without bound.
                self._entries.pop(key, None)
                return MISS
            return value

    def set(self, key: Any, value: Any) -> Any:
        ttl = self._ttl_fn()
        if ttl > 0:
            with self._lock:
                self._entries[key] = (time.monotonic() + ttl, value)
        return value

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_load(self, key: Any, loader: Callable[[], Any]) -> Any:
        """Return the cached value, or call loader() and cache its result.

        The loader runs outside the lock: it performs a DB query, and holding the
        lock across it would serialise every request behind one slow query. A
        concurrent duplicate load is harmless — both compute the same value.
        """
        cached = self.get(key)
        if cached is not MISS:
            return cached
        return self.set(key, loader())
