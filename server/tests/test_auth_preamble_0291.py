"""인증 프리앰블 1회화 + TTL 캐시 single-flight (flowgate.default.0291 T1).

CH0016 의 실측 덤프에서 지배적인 중복은 인증 프리앰블이었다. 요청 하나마다
``token_blacklist`` → ``auth_sessions`` → ``users`` 를 따로 읽고, 그것이 네 번 연달아
찍혔다. 0276 T0009 의 5초 TTL 캐시가 붙어 있는데도 그랬다.

원인이 둘이라 대응도 둘이고, 이 스위트는 둘을 따로 고정한다.

1. **콜드 버스트** — 동시에 들어온 요청들이 같은 순간에 같은 키를 미스한다. TTL 캐시는
   첫 로더가 끝나기 전에는 아무도 못 막는다. ``utils/ttl_cache.py`` 의 single-flight
   가 이 몫이다. 여기서 세는 것은 **로더 호출 횟수**다 — 값이 맞는지가 아니라 DB 를
   몇 번 갔는지가 이 변경의 전부이기 때문이다.
2. **콜드 미스가 3왕복** — ``auth/auth_preamble.py`` 가 셋을 한 문장으로 합친다.

single-flight 쪽은 정확성 위험이 하나뿐이라 그것만 집중해서 본다: **진행 중인 로드가
무효화를 되살리는가.** 로그아웃/세션 폐기는 "TTL 이 아니라 즉시" 라는 0276 의 보장이
걸려 있고, 로더가 락 밖에서 도는 이상 무효화가 로드 도중에 지나갈 수 있다.

프리앰블 쪽은 합침 SQL 이 **실제로 도는지**를 진짜 SQLite 에 대고 확인한다. 가짜 백엔드로
호출 횟수만 세면 "한 번 갔다" 는 증명되지만 "그 한 번이 유효한 SQL 이었다" 는 증명되지
않는다 — 이 변경에서 깨지기 가장 쉬운 부분이 정확히 거기다(파생 테이블 + 스칼라 서브쿼리
+ 플레이스홀더 순서).
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.utils.ttl_cache import MISS, SingleFlight, TTLCache  # noqa: E402
from modules.flow_gate.auth import auth_cache, auth_preamble  # noqa: E402
from modules.flow_gate.db import connection as _connection  # noqa: E402
from modules.flow_gate.db import users as _db_users  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402


def _ttl(seconds: float):
    return lambda: seconds


# ── single-flight: 콜드 버스트 ────────────────────────────────────────────────

class TestSingleFlightLoads:
    def test_concurrent_misses_run_the_loader_once(self):
        """N 개가 동시에 같은 키를 미스해도 DB 는 한 번만 간다.

        이것이 CH0016 덤프의 ``users`` ×4 를 만든 자리다. 로더를 일부러 붙잡아 두어
        '첫 로더가 도는 동안 나머지가 도착하는' 순간을 결정론적으로 만든다.
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
        # 팔로워들이 리더를 기다리는 상태로 들어갈 시간을 준다.
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(5)

        assert len(calls) == 1, f"로더가 {len(calls)}회 돌았다 — single-flight 가 안 걸렸다"
        assert results == {i: "row" for i in range(6)}

    def test_follower_does_not_inherit_the_leaders_exception(self):
        """리더가 죽어도 팔로워는 자기 값을 받는다.

        하나의 일시적 DB 오류를 동시 요청 N 개의 500 으로 증폭시키지 않는다는 규칙.
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
        """로드 도중의 무효화가 그 로드 결과에 의해 되살아나지 않는다.

        폐기가 즉시 반영된다는 auth_cache 의 보장이 걸려 있는 유일한 창(窓)이다.
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
        cache.invalidate("k")   # ← 로드가 도는 중에 폐기가 지나간다
        release.set()
        t.join(5)

        assert done == ["stale"], "리더 자신은 방금 읽은 값을 쓴다"
        assert cache.get("k") is MISS, "무효화된 키가 진행 중이던 로드에 의해 되살아났다"

    def test_reentrant_loader_does_not_deadlock(self):
        """로더가 같은 키로 재진입해도 자기 자신을 기다리지 않는다."""
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
        """TTL=0 은 '캐시 이전 동작' 이다 — single-flight 가 그 뜻을 바꾸지 않는다."""
        cache = TTLCache(_ttl(0))
        calls = []
        for _ in range(3):
            cache.get_or_load("k", lambda: calls.append(1) or "v")
        assert len(calls) == 3

    def test_single_flight_helper_dedupes_without_caching(self):
        """SingleFlight 는 중복만 없애고 값을 보관하지 않는다."""
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

        # 끝난 뒤에는 아무것도 남지 않는다 — 다음 호출은 다시 돈다.
        flight.do("k", work)
        assert len(calls) == 2


# ── 프리앰블 합침 ─────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, username TEXT, is_active INTEGER, is_admin INTEGER
);
CREATE TABLE token_blacklist (jti TEXT PRIMARY KEY, user_id TEXT);
CREATE TABLE auth_sessions (session_id TEXT PRIMARY KEY, user_id TEXT, revoked_at TEXT);
"""


class _SqliteBackend:
    """진짜 SQLite. 합침 SQL 이 유효한지 + 왕복이 몇 번인지 둘 다 본다."""

    db_type = 1  # dialect.SQLITE — translate 는 no-op

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
    # 프리페치는 db.users 의 store 를 쓴다 (auth_preamble._load 참조). 판정 함수들이
    # 쓰는 connection.get_store 도 같은 객체를 보게 해서, 이 스위트가 재는 왕복 수가
    # 한 백엔드의 수치가 되도록 한다.
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
        """세 캐시가 전부 비었을 때 왕복은 1회. 이것이 T1 의 수치 자체다."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다: {backend.reads}"
        assert auth_cache.blacklist_cache().get("j1") is False
        assert auth_cache.session_cache().get("s1") is True
        assert auth_cache.user_cache().get("u1")["username"] == "alice"

    def test_prefetched_caches_serve_the_real_call_sites(self, backend):
        """판정 함수들이 프리페치된 값을 그대로 쓴다 — 추가 왕복 0."""
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
        """합침이 판정을 바꾸지 않는다 — 폐기는 폐기로 읽혀야 한다."""
        _seed(backend, revoked_jti=True, revoked_session=True)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert auth_cache.blacklist_cache().get("j1") is True
        assert auth_cache.session_cache().get("s1") is False

    def test_missing_user_is_cached_as_absent_not_as_a_null_row(self, backend):
        """사용자가 없으면 LEFT JOIN 이 전부 NULL 인 행을 준다.

        그걸 그대로 캐시하면 ``get_current_user`` 가 'User not found'(401) 대신
        is_active 없는 dict 를 보고 403 으로 샌다. 경계가 여기 하나뿐이라 명시한다.
        """
        _seed(backend, user=False)
        auth_preamble.prefetch("j1", "s1", "u1")

        assert auth_cache.user_cache().get("u1") is None
        # blacklist/session 판정은 사용자가 없어도 함께 실려 온다.
        assert auth_cache.session_cache().get("s1") is True

    def test_already_cached_entries_are_not_refetched(self, backend):
        """웜 상태에서는 아무 일도 하지 않는다."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")
        backend.reads.clear()
        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == []

    def test_a_single_missing_entry_is_left_to_the_narrow_query(self, backend):
        """하나만 비었으면 합쳐도 왕복 수가 같다 — 좁은 쿼리 쪽이 낫다."""
        _seed(backend)
        auth_preamble.prefetch("j1", "s1", "u1")
        auth_cache.invalidate_user("u1")
        backend.reads.clear()

        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == [], "하나 남았는데 합침 쿼리를 돌렸다"

    def test_disabled_cache_skips_the_prefetch_entirely(self, backend, monkeypatch):
        """TTL=0 이면 채울 곳이 없다 — 왕복을 하나 더 늘리기만 한다."""
        _seed(backend)
        monkeypatch.setenv("FLOWGATE_AUTH_CACHE_TTL", "0")
        auth_preamble.prefetch("j1", "s1", "u1")
        assert backend.reads == []

    def test_a_broken_query_does_not_break_authentication(self, backend, monkeypatch):
        """프리페치 실패는 조용히 종전 경로로 떨어진다 — 새 실패 지점을 만들지 않는다."""
        def boom(*_a, **_k):
            raise RuntimeError("no such table")

        monkeypatch.setattr(backend, "fetch_one", boom)
        auth_preamble.prefetch("j1", "s1", "u1")   # 예외가 새어 나오면 실패

        assert auth_cache.user_cache().get("u1") is MISS, "실패했는데 뭔가 캐시했다"

    def test_concurrent_prefetch_issues_one_query(self, backend):
        """콜드 버스트에서도 합침 쿼리는 한 번. 3N → 1 의 N 쪽."""
        _seed(backend)
        threads = [threading.Thread(target=lambda: auth_preamble.prefetch("j1", "s1", "u1"))
                   for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다"
