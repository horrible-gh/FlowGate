"""Fold the per-request auth preamble into a single query (0291 T1, CH0016).

Problem
----

One authenticated request spends a fixed three queries before the handler even starts::

    SELECT jti FROM token_blacklist WHERE jti=?      -- has this access token been revoked
    SELECT * FROM auth_sessions WHERE session_id=?   -- is this session still alive
    SELECT * FROM users WHERE user_id=?              -- is this user active

0276 T0009 added a 5-second TTL cache here, yet the measured dump posted in CH0016 still
shows each of the three queries four times. There are two reasons; this file and
``utils/ttl_cache.py`` each take one.

1. **Cold burst.** Drawing one screen, the front end fires several requests at once. They
   all miss the same key at the same instant, so the rest are already on their way to the
   DB before the first loader returns. A TTL cache cannot stop this in principle —
   single-flight in ``ttl_cache.SingleFlight`` / ``TTLCache.get_or_load`` handles that part.
2. **A cold miss is itself three round trips.** With an empty cache (just after process
   start, TTL expiry or invalidation) it goes three separate times. The three are independent
   single-row lookups, so there is no reason not to read them at once. That is this file.

Design
----

This module is a **prefetch**. It does not add another cache.

``prefetch()`` only fills ``auth_cache``'s existing three maps (blacklist / sessions /
users); the decisions are still made by ``token_store.is_blacklisted`` /
``session_store.is_session_active`` / ``middleware.get_current_user`` through those maps.
That matters because **no invalidation path has to be touched at all**. Logout, session
revocation and user changes already invalidate those three maps, and since the prefetch
keeps no separate copy, 0276's guarantee that "revocation is immediate, not TTL-bound" holds.
Had the merged result been cached separately, no hook could have cleared that copy.

It is therefore safe to fail. If the prefetch ends in an exception it fills nothing and
returns quietly, and callers query three times as before. No new failure point is added to the auth path.

Numbers (from the CH0016 dump)
-----------------------

* One cold request: 3 queries → 1.
* N concurrent requests (N=4 in the dump): 3N → 1. Single-flight turns N into 1, and this
  merge turns 3 into 1.
* A warm request: 0 queries (true since 0276 T0009, unchanged here).
"""
from __future__ import annotations

from ..utils.ttl_cache import MISS as _MISS, SingleFlight as _SingleFlight
from . import auth_cache as _auth_cache

# Guards the merged query's own cold burst. It deduplicates rather than caches — why it must
# not be a cache is explained in SingleFlight's docstring.
_flight = _SingleFlight()

# LEFT JOIN from a one-row anchor onto users. The result must be one row even with no users
# row, so the blacklist/session facts come back too — a plain `FROM users` would return zero
# rows for a deleted user and lose the other two facts.
#
# Written strictly within standard SQL-92 (scalar subqueries + a derived table). db/dialect.py
# only swaps placeholders, so the same statement runs on SQLite / MySQL / PostgreSQL.
_ANCHOR = "FROM (SELECT 1 AS fg_anchor) fg_a"
_USER_JOIN = " LEFT JOIN users u ON u.user_id = ?"
_BLACKLIST_COL = "(SELECT COUNT(*) FROM token_blacklist tb WHERE tb.jti = ?) AS fg_jti_revoked"
_SESSION_COL = (
    "(SELECT COUNT(*) FROM auth_sessions s WHERE s.session_id = ? AND s.revoked_at IS NULL)"
    " AS fg_session_active"
)


def _missing(jti: str | None, sid: str | None, user_id: str | None) -> tuple[bool, bool, bool]:
    """Which of the three caches are empty right now; only those join the merged query."""
    need_jti = bool(jti) and _auth_cache.blacklist_cache().get(jti) is _MISS
    need_sid = bool(sid) and _auth_cache.session_cache().get(sid) is _MISS
    need_user = bool(user_id) and _auth_cache.user_cache().get(user_id) is _MISS
    return need_jti, need_sid, need_user


def _load(jti: str | None, sid: str | None, user_id: str,
          need_jti: bool, need_sid: bool, need_user: bool) -> None:
    # Take the store via ``db.users``. The statement's primary table is users, and it must be
    # read through the **same store** that module uses. Calling connection.get_store directly
    # would make this prefetch see a different DB wherever a caller swapped the users store
    # (test harnesses do exactly that), and it would then cache "no such user" and produce a
    # 401 — the one way a prefetch could change a decision, so it is blocked here.
    from modules.flow_gate.db.users import get_store

    columns: list[str] = []
    params: list = []
    if need_user:
        columns.append("u.*")
    if need_jti:
        columns.append(_BLACKLIST_COL)
    if need_sid:
        columns.append(_SESSION_COL)
    # Stack the parameters in the same order so the anchor's `?` comes after the SELECT list's.
    if need_jti:
        params.append(jti)
    if need_sid:
        params.append(sid)
    join = ""
    if need_user:
        join = _USER_JOIN
        params.append(user_id)

    row = get_store()._fetch_one(f"SELECT {', '.join(columns)} {_ANCHOR}{join}", params)
    if row is None:
        # With a one-row anchor this should not happen. If it does, fill nothing and leave it
        # to the previous path — better than filling the cache on a guess.
        return

    if need_jti:
        _auth_cache.blacklist_cache().set(jti, bool(row.get("fg_jti_revoked")))
    if need_sid:
        _auth_cache.session_cache().set(sid, bool(row.get("fg_session_active")))
    if need_user:
        user = {k: v for k, v in row.items() if not k.startswith("fg_")}
        # An unmatched LEFT JOIN leaves every user column NULL = there is no such user.
        _auth_cache.user_cache().set(user_id, user if user.get("user_id") is not None else None)


def prefetch(jti: str | None, sid: str | None, user_id: str | None) -> None:
    """Fill whichever of the three caches are empty in one query. Failures are ignored.

    It acts only when two or more are empty. With just one, merging saves no round trip and
    the caller's own narrow query (one index lookup) is the better choice.
    """
    if not user_id or not _auth_cache.caching_enabled():
        # TTL=0 means "give me the pre-cache behaviour". The prefetch then has nowhere to fill
        # and would only add one more round trip.
        return
    need_jti, need_sid, need_user = _missing(jti, sid, user_id)
    if (need_jti + need_sid + need_user) < 2:
        return
    try:
        _flight.do(
            ("preamble", jti, sid, user_id),
            lambda: _load(jti, sid, user_id, need_jti, need_sid, need_user),
        )
    except Exception:
        # No new failure point on the auth path: if nothing was filled the caller queries three
        # times as before, and the result is exactly identical.
        return
