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

The TTL-map mechanics live in utils/ttl_cache.py since 0282 (NR0003 발견 2), so
db/meta_cache.py can reuse them without a db↔auth circular import; this module
keeps the auth TTL policy and the invalidation hooks.
"""
from __future__ import annotations

import os

from ..utils.ttl_cache import MISS as _MISS, TTLCache as _TTLCache  # noqa: F401


def _configured_ttl() -> float:
    raw = os.environ.get("FLOWGATE_AUTH_CACHE_TTL")
    if raw is None or raw.strip() == "":
        return 5.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


def _token_ttl() -> float:
    """TTL for the worker-token lookup (0288 NR0003 권고 3).

    Separate from _configured_ttl for two reasons:

      * It is OFF under TESTING unless FLOWGATE_TOKEN_CACHE_TTL says otherwise.
        Worker tokens are single-use, and much of the suite issues a token,
        mutates the row (consume / revoke / expire — sometimes with raw SQL that
        cannot invalidate anything) and immediately asserts the 401. A stale
        window there would make those tests order-dependent. Same reasoning as
        db/meta_cache.py.
      * It is separately tunable in operation: this entry gates authentication
        for every worker call, so an operator may want it shorter than the rest.

    FLOWGATE_TOKEN_CACHE_TTL=0 disables it (every verify hits the DB).
    """
    raw = os.environ.get("FLOWGATE_TOKEN_CACHE_TTL")
    if raw is None or raw.strip() == "":
        return 0.0 if os.environ.get("TESTING") else _configured_ttl()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _configured_ttl()


# The two RBAC queries are NOT cached here: rbac/permission_service.py already
# caches the resolved permission set with its own TTL and invalidation, and
# rbac/decorators.py now delegates to it (0276 T0009).
_blacklist = _TTLCache(_configured_ttl)   # jti        -> bool (is the access token revoked?)
_sessions = _TTLCache(_configured_ttl)    # session_id -> bool (is the session still active?)
_users = _TTLCache(_configured_ttl)       # user_id    -> user row | None
_tokens = _TTLCache(_token_ttl)           # token hash -> tokens row | None


def caching_enabled() -> bool:
    """False when FLOWGATE_AUTH_CACHE_TTL=0 disables the three per-request caches.

    auth_preamble.prefetch() consults this: with caching off there is nowhere to
    put a prefetched row, so merging the lookups would add a round trip instead
    of removing two.
    """
    return _configured_ttl() > 0


def blacklist_cache() -> _TTLCache:
    return _blacklist


def session_cache() -> _TTLCache:
    return _sessions


def user_cache() -> _TTLCache:
    return _users


def token_cache() -> _TTLCache:
    return _tokens


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


def invalidate_tokens() -> None:
    """Any write to the tokens table happened (issue / consume / revoke / …).

    Clears the whole map rather than one key: the cache is keyed by token hash
    (that is what the lookup has) while every write path addresses the row by
    token_id, and mapping id -> hash would cost the very query being saved.
    Clearing is cheap and unconditionally correct — token writes are a handful
    per workflow step, while the reads happen on every authenticated call — and
    it keeps single-use semantics exact: a consumed token 401s on the very next
    request, not TTL seconds later.
    """
    _tokens.clear()


def invalidate_everything() -> None:
    """Full reset. Used by tests and by anything that rewrites auth state wholesale."""
    _blacklist.clear()
    _sessions.clear()
    _users.clear()
    _tokens.clear()
