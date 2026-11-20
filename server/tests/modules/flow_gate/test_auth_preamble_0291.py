"""One-query authentication preamble and TTL-cache single-flight (flowgate.default.0291 T1).

The CH0016 measurements showed repeated authentication reads: token_blacklist,
auth_sessions, and users separately for each request, four times in succession,
despite the five-second TTL cache introduced in 0276 T0009.

Pin the two causes independently:

1. Cold bursts miss the same key concurrently before the first loader finishes.
   utils/ttl_cache.py single-flight suppresses duplicate loads. Count loader calls,
   since the improvement concerns database trips rather than return values.
2. A cold miss costs three trips. auth/auth_preamble.py combines them into one statement.

For single-flight, verify that an in-flight load cannot undo invalidation. Logout and
session revocation must remain immediate, as promised in 0276, even though loaders
run outside the lock and revocation can occur during a load.

Run the combined preamble SQL against real SQLite. A fake call counter cannot prove
the derived table, scalar subqueries, and placeholder ordering form valid SQL.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.utils.ttl_cache import MISS, SingleFlight, TTLCache  # noqa: E402
from modules.flow_gate.auth import auth_cache, auth_preamble  # noqa: E402
from modules.flow_gate.db import connection as _connection  # noqa: E402
from modules.flow_gate.db import users as _db_users  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402


def _ttl(seconds: float):
    return lambda: seconds


# ── Single-flight: cold burst ──

class TestSingleFlightLoads:
    def test_concurrent_misses_run_the_loader_once(self):
        """N concurrent misses for one key reach the DB once.

        Hold the loader to deterministically reproduce the CH0016 users ×4 burst:
        followers arrive while the initial loader is still running.
        """
        cache = TTLCache(_ttl(60))
        started, release, calls = threading.Event(), threading.Event(), []

        def loader():
            calls.append(1)
            started.set()
            release.wait(5)
            return "row"

        results = {}
        threads = [
            threading.Thread(target=lambda i=i: results.__setitem__(i, cache.get_or_load("k", loader)))
            for i in range(6)
        ]
        threads[0].start()
        assert started.wait(5), "리더가 로더에 진입하지 않았다"
        for t in threads[1:]:
            t.start()
        # Allow followers to enter the state waiting for the leader.
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(5)

        assert len(calls) == 1, f"로더가 {len(calls)}회 돌았다 — single-flight 가 안 걸렸다"
        assert results == {i: "row" for i in range(6)}

    def test_follower_does_not_inherit_the_leaders_exception(self):
        """Followers obtain their own values even if the leader fails.

        One transient DB error must not turn into N simultaneous HTTP 500 responses.
        """
        cache = TTLCache(_ttl(60))
        started, release = threading.Event(), threading.Event()
        state = {"first": True}

        def loader():
            if state["first"]:
                state["first"] = False
                started.set()
                release.wait(5)
                raise RuntimeError("db down")
            return "recovered"

        leader_error, follower_result = [], []

        def run_leader():
            try:
                cache.get_or_load("k", loader)
            except RuntimeError as exc:
                leader_error.append(exc)

        leader = threading.Thread(target=run_leader)
        leader.start()
        assert started.wait(5)
        follower = threading.Thread(target=lambda: follower_result.append(cache.get_or_load("k", loader)))
        follower.start()
        time.sleep(0.2)
        release.set()
        leader.join(5)
        follower.join(5)

        assert len(leader_error) == 1, "리더는 자기 예외를 그대로 받아야 한다"
        assert follower_result == ["recovered"], "팔로워가 남의 예외를 물려받았다"

    def test_invalidate_during_a_load_is_not_undone_by_it(self):
        """A load result must not undo invalidation performed during that load.

        This is the race window affecting auth_cache immediate-revocation guarantees.
        """
        cache = TTLCache(_ttl(60))
        started, release = threading.Event(), threading.Event()

        def loader():
            started.set()
            release.wait(5)
            return "stale"

        done = []
        t = threading.Thread(target=lambda: done.append(cache.get_or_load("k", loader)))
        t.start()
        assert started.wait(5)
        cache.invalidate("k")   # Invalidate while the load is in progress
        release.set()
        t.join(5)

        assert done == ["stale"], "리더 자신은 방금 읽은 값을 쓴다"
        assert cache.get("k") is MISS, "무효화된 키가 진행 중이던 로드에 의해 되살아났다"

    def test_reentrant_loader_does_not_deadlock(self):
        """A loader reentering with the same key must not wait for itself."""
        cache = TTLCache(_ttl(60))
        depth = {"n": 0}

        def loader():
            depth["n"] += 1
            if depth["n"] == 1:
                return cache.get_or_load("k", loader)
            return "inner"

        finished = []
        t = threading.Thread(target=lambda: finished.append(cache.get_or_load("k", loader)))
        t.start()
        t.join(5)
        assert not t.is_alive(), "재진입에서 교착했다"
        assert finished == ["inner"]

    def test_ttl_zero_still_loads_every_time(self):
        """TTL=0 preserves pre-cache behavior; single-flight must not change that contract."""
        cache = TTLCache(_ttl(0))
        calls = []
        for _ in range(3):
            cache.get_or_load("k", lambda: calls.append(1) or "v")
        assert len(calls) == 3

    def test_single_flight_helper_dedupes_without_caching(self):
        """SingleFlight suppresses duplicates without retaining values."""
        flight = SingleFlight()
        started, release, calls = threading.Event(), threading.Event(), []

        def work():
            calls.append(1)
            started.set()
            release.wait(5)

        threads = [threading.Thread(target=lambda: flight.do("k", work)) for _ in range(4)]
        threads[0].start()
        assert started.wait(5)
        for t in threads[1:]:
            t.start()
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(5)
        assert len(calls) == 1

        # Nothing remains afterward; the next call loads again.
        flight.do("k", work)
        assert len(calls) == 2


# ── Combined authentication preamble ──

_SCHEMA = """
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, username TEXT, is_active INTEGER, is_admin INTEGER
);
CREATE TABLE token_blacklist (jti TEXT PRIMARY KEY, user_id TEXT);
CREATE TABLE auth_sessions (session_id TEXT PRIMARY KEY, user_id TEXT, revoked_at TEXT);
"""


class _SqliteBackend:
    """Real SQLite verifies both combined-SQL validity and round-trip count."""

    db_type = 1  # dialect.SQLITE: translation is a no-op

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.reads: list[str] = []

    def fetch_one(self, sql, params):
        self.reads.append(sql)
        row = self.conn.execute(sql, params or []).fetchone()
        return None if row is None else dict(row)

    def fetch_all(self, sql, params):
        self.reads.append(sql)
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or [])

    def commit(self):
        self.conn.commit()


@pytest.fixture
def backend(monkeypatch):
    be = _SqliteBackend()
    store = FlowGateStore.__new__(FlowGateStore)
    store._db = be
    store._sq = None
    # Prefetch uses db.users storage (auth_preamble._load). Make the verdict functions
    # use the same store through connection.get_store so the round-trip count
    # measures a single backend.
    monkeypatch.setattr(_db_users, "get_store", lambda: store)
    monkeypatch.setattr(_connection, "get_store", lambda: store)
    monkeypatch.setenv("FLOWGATE_AUTH_CACHE_TTL", "60")
    auth_cache.invalidate_everything()
    yield be
    auth_cache.invalidate_everything()


def _seed(be, *, user=True, revoked_jti=False, revoked_session=False):
    if user:
        be.conn.execute("INSERT INTO users VALUES ('u1','alice',1,0)")
    if revoked_jti:
        be.conn.execute("INSERT INTO token_blacklist VALUES ('j1','u1')")
    be.conn.execute(
        "INSERT INTO auth_sessions VALUES ('s1','u1',?)",
        ["2026-07-22T00:00:00Z" if revoked_session else None],
    )
    be.conn.commit()


class TestPreamblePrefetch:
    def test_three_lookups_collapse_into_one_query(self, backend):
        """All three caches cold: exactly one round trip, the T1 target."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다: {backend.reads}"
        assert auth_cache.blacklist_cache().get("j1") is False
        assert auth_cache.session_cache().get("s1") is True
        assert auth_cache.user_cache().get("u1")["username"] == "alice"

    def test_prefetched_caches_serve_the_real_call_sites(self, backend):
        """Verdict functions reuse prefetched values with zero additional round trips."""
        from modules.flow_gate.auth.session_store import is_session_active
        from modules.flow_gate.auth.token_store import is_blacklisted
        from modules.flow_gate.db import users as db_users

        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")
        backend.reads.clear()

        assert is_blacklisted("j1") is False
        assert is_session_active("s1") is True
        assert auth_cache.user_cache().get_or_load("u1", lambda: db_users.get_by_id("u1"))["username"] == "alice"
        assert backend.reads == [], "프리페치 뒤에도 DB 를 다시 쳤다"

    def test_revoked_token_and_session_survive_the_merge(self, backend):
        """Combining reads must preserve verdicts: revocation still means revoked."""
        _seed(backend, revoked_jti=True, revoked_session=True)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert auth_cache.blacklist_cache().get("j1") is True
        assert auth_cache.session_cache().get("s1") is False

    def test_missing_user_is_cached_as_absent_not_as_a_null_row(self, backend):
        """A missing user produces a LEFT JOIN row of nulls.

        Caching that dict would make get_current_user return 403 for missing is_active
        instead of User not found (401). Pin this boundary explicitly.
        """
        _seed(backend, user=False)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert auth_cache.user_cache().get("u1") is None
        # Blacklist/session results are included even when no user exists.
        assert auth_cache.session_cache().get("s1") is True

    def test_already_cached_entries_are_not_refetched(self, backend):
        """Do nothing when the caches are warm."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")
        backend.reads.clear()
        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == []

    def test_a_single_missing_entry_is_left_to_the_narrow_query(self, backend):
        """One cold cache gains no trip reduction; prefer the narrower query."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")
        auth_cache.invalidate_user("u1")
        backend.reads.clear()

        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == [], "하나 남았는데 합침 쿼리를 돌렸다"

    def test_disabled_cache_skips_the_prefetch_entirely(self, backend, monkeypatch):
        """With TTL=0 there is no cache to populate, so prefetch would only add a trip."""
        _seed(backend)
        monkeypatch.setenv("FLOWGATE_AUTH_CACHE_TTL", "0")
        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == []

    def test_a_broken_query_does_not_break_authentication(self, backend, monkeypatch):
        """A prefetch failure quietly uses the original path without introducing a failure point."""
        def boom(*_a, **_k):
            raise RuntimeError("no such table")

        monkeypatch.setattr(backend, "fetch_one", boom)
        auth_preamble.prefetch("j1", "s1", "u1")   # Any escaping exception fails the test

        assert auth_cache.user_cache().get("u1") is MISS, "실패했는데 뭔가 캐시했다"

    def test_concurrent_prefetch_issues_one_query(self, backend):
        """A cold burst also runs the combined query once: the N in the 3N-to-1 reduction."""
        _seed(backend)
        threads = [threading.Thread(target=lambda: auth_preamble.prefetch("j1", "s1", "u1"))
                   for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다"
