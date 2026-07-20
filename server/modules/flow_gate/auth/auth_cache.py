"""Short-lived in-process cache for the per-request authentication lookups.

0276 NR0003 발견 2: every authenticated request ran a fixed five queries before
the handler even started — token_blacklist, auth_sessions, users, and (on
permission-protected routes) user_project_roles + role_permissions. The last two
are handled by rbac/permission_service.py instead of this module; see T0009. The UI
refreshes the tree, dashboard and notifications constantly, so these repeat once
per API call and dominate the "same query over and over" pattern in the log.

Policy (0276 CH0004 delegated the choice; T0009 fixes it here):

  * TTL is 5 seconds — long enough to collapse a burst of UI calls, short enough
    that any staleness this cache can produce is bounded by five seconds.
  * Every revocation path invalidates explicitly, so within a process a logout,
    a session revoke, a token blacklist or a user/permission change takes effect
    *immediately* rather than after the TTL. The TTL is the backstop, not the
    mechanism.
  * The cache is per process. With multiple workers, a revocation performed on
    worker A is invalidated immediately on A and expires within the TTL on the
    others. This is the one accepted trade-off; see the task report.

Set FLOWGATE_AUTH_CACHE_TTL=0 to disable caching entirely (every lookup goes to
the DB, i.e. the pre-0276 behaviour).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

_MISS = object()


def _configured_ttl() -> float:
    raw = os.environ.get("FLOWGATE_AUTH_CACHE_TTL")
    if raw is None or raw.strip() == "":
        return 5.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


class _TTLCache:
    """Tiny thread-safe TTL map. Monotonic clock, so system time changes cannot
    extend an entry's life."""

    def __init__(self) -> None:
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        ttl = _configured_ttl()
        if ttl <= 0:
            return _MISS
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISS
            expires_at, value = entry
            if expires_at <= now:
                # Expired: drop it so the map cannot grow without bound.
                self._entries.pop(key, None)
                return _MISS
            return value

    def set(self, key: Any, value: Any) -> Any:
        ttl = _configured_ttl()
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
        if cached is not _MISS:
            return cached
        return self.set(key, loader())


# The two RBAC queries are NOT cached here: rbac/permission_service.py already
# caches the resolved permission set with its own TTL and invalidation, and
# rbac/decorators.py now delegates to it (0276 T0009).
_blacklist = _TTLCache()   # jti        -> bool (is the access token revoked?)
_sessions = _TTLCache()    # session_id -> bool (is the session still active?)
_users = _TTLCache()       # user_id    -> user row | None


def blacklist_cache() -> _TTLCache:
    return _blacklist


def session_cache() -> _TTLCache:
    return _sessions


def user_cache() -> _TTLCache:
    return _users


# ── Invalidation hooks (called from the write paths) ─────────────────────────

def invalidate_token(jti: str) -> None:
    """A token was blacklisted: forget any cached 'not revoked' answer."""
    _blacklist.invalidate(jti)


def invalidate_session(session_id: str) -> None:
    """One session was revoked."""
    _sessions.invalidate(session_id)


def invalidate_all_sessions() -> None:
    """A bulk revoke (logout-others / revoke-all) happened.

    Those statements revoke by user_id, and the cache is keyed by session_id, so
    there is no cheap way to map user -> sessions here. Clearing the whole map is
    correct and costs nothing: bulk revokes are rare, and the only penalty is
    that the next request re-reads its session row.
    """
    _sessions.clear()


def invalidate_user(user_id: str | None = None) -> None:
    """A user row changed (created / updated / deleted).

    is_admin and is_active live on this row and both gate access, so the entry
    must go immediately rather than at TTL expiry.
    """
    if user_id is None:
        _users.clear()
        return
    _users.invalidate(user_id)


def invalidate_everything() -> None:
    """Full reset. Used by tests and by anything that rewrites auth state wholesale."""
    _blacklist.clear()
    _sessions.clear()
    _users.clear()
