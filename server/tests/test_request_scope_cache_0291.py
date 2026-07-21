"""Request-scoped read cache + query accounting (flowgate.default.0291, NR0003 P3-1 / 4-8).

NR0003 발견 5 는 한 요청 안에서 같은 행을 같은 파라미터로 15회 이상 다시 읽는다고
지적했다. ``db/request_cache.py`` 가 그 중복을 요청 경계 안에서 흡수한다.

이 스위트가 지키려는 것은 **성능이 아니라 정확성**이다. 캐시가 몇 번 맞았는지는 세기 쉽고,
캐시가 틀린 값을 준 순간은 세기 어렵다. 그래서 케이스의 대부분이 "캐시하지 **않아야** 하는
자리에서 캐시하지 않는가" 를 본다:

  - 스코프 밖(백그라운드 워커·부팅)
  - 쓰기가 끼어든 뒤 (read-your-writes)
  - 트랜잭션 안
  - 파라미터가 다를 때
  - 반환값을 호출부가 변형한 뒤 (copy-out)

가짜 store 를 써서 **DB 왕복 횟수를 직접 센다.** 실제 SQLite 를 놓고 결과값만 비교하면
"캐시가 안 도는데 답이 맞다" 와 "캐시가 도는데 답이 맞다" 를 구분할 수 없다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import request_cache  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402
from modules.flow_gate.db import connection as _connection  # noqa: E402


class _FakeBackend:
    """호출 횟수를 세는 최소 백엔드.

    ``fetch_one``/``fetch_all`` 을 갖고 있어 FlowGateStore 의 non-transaction 경로를
    탄다. 매 호출마다 **새 dict** 를 만든다 — 백엔드가 같은 객체를 돌려주면 copy-out
    테스트가 통과해도 아무것도 증명하지 못한다.
    """

    db_type = None  # dialect 프로퍼티가 SQLITE 로 떨어진다 (translate no-op)

    def __init__(self):
        self.one_calls: list[tuple[str, tuple]] = []
        self.all_calls: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple]] = []
        self.value = "v0"

    def fetch_one(self, sql, params):
        self.one_calls.append((sql, tuple(params)))
        return {"val": self.value, "tags": ["a"]}

    def fetch_all(self, sql, params):
        self.all_calls.append((sql, tuple(params)))
        return [{"val": self.value}]

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def commit(self):
        pass


@pytest.fixture
def store(monkeypatch):
    """캐시가 켜진 상태의 store + 가짜 백엔드.

    TESTING=1 이라 캐시는 기본 OFF 다(request_cache 정책 5). 명시적으로 켠다 —
    meta_cache 를 검증하는 0282 테스트와 같은 방식이다.
    """
    monkeypatch.setenv("FLOWGATE_REQUEST_CACHE", "1")
    s = FlowGateStore()
    backend = _FakeBackend()
    s._db = backend
    s._sq = None
    monkeypatch.setattr(_connection._tx_local, "txn", None, raising=False)
    return s, backend


SQL_ONE = "SELECT * FROM documents WHERE doc_id = ?"
SQL_ALL = "SELECT * FROM documents WHERE group_id = ?"


# ── 캐시가 도는가 ────────────────────────────────────────────────────────────

def test_same_read_twice_in_one_scope_hits_db_once(store):
    """발견 5 의 본체 — 같은 요청 안의 같은 조회는 한 번만 DB 를 친다."""
    s, backend = store
    with request_cache.request_scope("GET /x") as scope:
        first = s._fetch_one(SQL_ONE, ["d1"])
        second = s._fetch_one(SQL_ONE, ["d1"])
    assert first == second
    assert len(backend.one_calls) == 1
    assert (scope.queries, scope.hits) == (1, 1)


def test_fetch_all_is_cached_too(store):
    s, backend = store
    with request_cache.request_scope("GET /x"):
        assert s._fetch_all(SQL_ALL, ["g1"]) == s._fetch_all(SQL_ALL, ["g1"])
    assert len(backend.all_calls) == 1


def test_different_params_are_different_entries(store):
    """캐시 키에 파라미터가 들어간다. 빠지면 다른 문서를 같은 문서로 취급한다."""
    s, backend = store
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d2"])
    assert len(backend.one_calls) == 2


def test_scopes_do_not_leak_into_each_other(store):
    """캐시 수명의 상한이 요청 하나라는 것 — stale 위험이 없는 근거 그 자체다."""
    s, backend = store
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2


# ── 캐시하지 않아야 하는 자리 ────────────────────────────────────────────────

def test_no_scope_means_no_caching(store):
    """백그라운드 워커·부팅 경로. 스코프를 열지 않으므로 동작이 종전 그대로다."""
    s, backend = store
    s._fetch_one(SQL_ONE, ["d1"])
    s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2
    assert request_cache.current() is None


def test_write_in_scope_invalidates_read_your_writes(store):
    """규칙 2 — 이 스위트에서 가장 중요한 케이스.

    쓰기 뒤의 읽기가 캐시된 옛 값을 받으면, 그건 성능 개선이 아니라 데이터 버그다.
    """
    s, backend = store
    with request_cache.request_scope("POST /x") as scope:
        assert s._fetch_one(SQL_ONE, ["d1"])["val"] == "v0"
        backend.value = "v1"
        s._execute("UPDATE documents SET title = ? WHERE doc_id = ?", ["t", "d1"])
        assert s._fetch_one(SQL_ONE, ["d1"])["val"] == "v1"
    assert len(backend.one_calls) == 2
    assert scope.writes == 1


def test_write_clears_every_entry_not_just_the_written_one(store):
    """테이블별 의존성 추적을 하지 않는다는 결정을 고정한다.

    UPDATE 대상과 무관해 보이는 조회도 함께 버린다. SQL 문자열만으로는 무관함을
    증명할 수 없고, 틀린 추론은 조용한 오답으로 나타난다.
    """
    s, backend = store
    with request_cache.request_scope("POST /x"):
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_all(SQL_ALL, ["g1"])
        s._execute("UPDATE projects SET project_name = ? WHERE project_id = ?", ["n", "p"])
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_all(SQL_ALL, ["g1"])
    assert len(backend.one_calls) == 2
    assert len(backend.all_calls) == 2


def test_update_cas_also_invalidates(store):
    """쓰기 경로가 _execute 하나가 아니다. update_cas 를 빠뜨리면 CAS 갱신 뒤의
    읽기만 옛 값을 보는, 재현이 어려운 형태의 버그가 된다."""
    s, backend = store
    with request_cache.request_scope("POST /x") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s.update_cas("documents", "d1", "doc_id", "status", "open", {"status": "closed"})
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2
    assert scope.writes == 1


def test_reads_inside_a_transaction_are_not_cached(store, monkeypatch):
    """규칙 3 — 트랜잭션은 쓰기 단위다."""
    s, backend = store

    class _Txn:
        def __init__(self):
            self.n = 0

        def execute(self, sql, params=None):
            self.n += 1

        def fetchone(self):
            return {"val": backend.value}

    txn = _Txn()
    monkeypatch.setattr(_connection._tx_local, "txn", txn, raising=False)
    with request_cache.request_scope("POST /x") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d1"])
    assert txn.n == 2
    assert scope.hits == 0
    assert scope.uncacheable == 2


def test_disabled_cache_still_counts_but_never_serves(store, monkeypatch):
    """OFF 일 때 계측은 계속 돈다.

    카운터가 캐시 설정에 따라 나타났다 사라지면 P1~P3 전후 비교의 척도가 달라진다 —
    NR0003 4-8 이 원한 것은 같은 척도의 비교다.
    """
    monkeypatch.setenv("FLOWGATE_REQUEST_CACHE", "0")
    s, backend = store
    with request_cache.request_scope("GET /x") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2
    assert (scope.queries, scope.hits, scope.reads) == (2, 0, 2)
    assert scope.uncacheable == 2


def test_oversized_key_is_not_cached(store):
    """`doc_id IN (?×900)` 류. 키를 만드는 비용이 캐시 이득을 넘는 자리라 건너뛴다 —
    건너뛰어도 답은 같고, 계측에는 남는다."""
    s, backend = store
    params = [f"doc-{i}" for i in range(2000)]
    sql = "SELECT * FROM documents WHERE doc_id IN (" + ",".join("?" * 2000) + ")"
    with request_cache.request_scope("GET /x") as scope:
        s._fetch_all(sql, params)
        s._fetch_all(sql, params)
    assert len(backend.all_calls) == 2
    assert scope.uncacheable == 2


# ── copy-out ────────────────────────────────────────────────────────────────

def test_caller_mutation_does_not_poison_the_cache(store):
    """규칙 4. get_rejected_documents_with_reasons() 는 실제로 반환된 dict 에
    ``reject_events`` 를 붙인다. 복사하지 않으면 다음 호출자가 그 오염을 본다."""
    s, _ = store
    with request_cache.request_scope("GET /x"):
        first = s._fetch_one(SQL_ONE, ["d1"])
        first["injected"] = True
        first["tags"].append("mutated")       # 중첩 구조까지 — deepcopy 여야 막힌다
        second = s._fetch_one(SQL_ONE, ["d1"])
    assert "injected" not in second
    assert second["tags"] == ["a"]


def test_cached_list_is_copied_too(store):
    s, _ = store
    with request_cache.request_scope("GET /x"):
        first = s._fetch_all(SQL_ALL, ["g1"])
        first.append({"val": "injected"})
        first[0]["val"] = "mutated"
        second = s._fetch_all(SQL_ALL, ["g1"])
    assert second == [{"val": "v0"}]


def test_none_result_is_cached_as_none(store):
    """'없음' 도 답이다. None 을 미스로 취급하면 존재하지 않는 행을 요청마다 다시 찾는다 —
    발견 5 의 중복 중 일부가 정확히 그 형태다."""
    s, backend = store
    backend.fetch_one = lambda sql, params: (backend.one_calls.append((sql, tuple(params))) or None)
    with request_cache.request_scope("GET /x") as scope:
        assert s._fetch_one(SQL_ONE, ["missing"]) is None
        assert s._fetch_one(SQL_ONE, ["missing"]) is None
    assert len(backend.one_calls) == 1
    assert scope.hits == 1


# ── 스코프 자체 ──────────────────────────────────────────────────────────────

def test_nested_scope_joins_the_outer_one(store):
    """스코프는 요청 하나를 뜻한다. 중첩은 경계를 잘못 그은 것이고, 조용히 합류시키는 편이
    캐시가 절반만 도는 것보다 낫다."""
    s, backend = store
    with request_cache.request_scope("GET /x") as outer:
        s._fetch_one(SQL_ONE, ["d1"])
        with request_cache.request_scope("GET /inner") as inner:
            assert inner is outer
            s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 1
    assert outer.hits == 2


def test_scope_is_torn_down_even_when_the_request_raises(store):
    """예외로 끝난 요청이 스코프를 남기면, 그 스레드가 다음에 처리하는 요청이 남의 캐시를
    본다 — 요청 간 데이터 누출이다."""
    s, _ = store
    with pytest.raises(RuntimeError):
        with request_cache.request_scope("GET /x"):
            s._fetch_one(SQL_ONE, ["d1"])
            raise RuntimeError("boom")
    assert request_cache.current() is None


def test_summary_reports_the_numbers_4_8_asked_for(store):
    """NR0003 §1-2 가 '추정' 으로 남긴 엔드포인트별 쿼리 수. 스코프가 요청 경계 자체라
    여기서는 실측이 된다."""
    s, _ = store
    with request_cache.request_scope("GET /flowgate/api/v1/tree") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d1"])
        s._execute("UPDATE documents SET title = ? WHERE doc_id = ?", ["t", "d1"])
    summary = scope.summary()
    assert "GET /flowgate/api/v1/tree" in summary
    assert "reads=2" in summary and "db=1" in summary
    assert "cached=1" in summary and "writes=1" in summary


# ── 미들웨어 경계 ────────────────────────────────────────────────────────────
#
# routers/main.py 를 import 하지 않고 최소 앱에 미들웨어만 얹어 검증한다. 여기서 확인할
# 것은 앱의 라우팅이 아니라 **스코프가 엔드포인트 안까지 살아서 도달하는가** 하나다.

def _app_with_middleware():
    from fastapi import FastAPI
    from modules.flow_gate.api.request_scope_middleware import RequestScopeMiddleware

    app = FastAPI()

    @app.get("/sync")
    def sync_endpoint():          # noqa: ANN202 — FastAPI 가 스레드풀로 넘기는 경로
        scope = request_cache.current()
        return {"label": scope.label if scope else None}

    @app.get("/async")
    async def async_endpoint():   # noqa: ANN202
        scope = request_cache.current()
        return {"label": scope.label if scope else None}

    @app.get("/flowgate/api/v1/events")
    async def sse_endpoint():     # noqa: ANN202
        return {"label": (request_cache.current().label if request_cache.current() else None)}

    app.add_middleware(RequestScopeMiddleware)
    return app


def test_scope_reaches_a_sync_def_endpoint():
    """이 코드베이스의 엔드포인트는 대부분 sync ``def`` 다 (NR0003 발견 3: 그래서 AnyIO
    스레드풀 40개가 세마포어 20을 경합한다). contextvar 가 스레드풀로 복사되지 않으면
    캐시가 도는 곳이 한 군데도 없으면서 테스트는 전부 통과한다 — 그 실패 모드를 막는다."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/sync").json()["label"] == "GET /sync"


def test_scope_reaches_an_async_endpoint():
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/async").json()["label"] == "GET /async"


def test_sse_stream_gets_no_scope():
    """SSE 는 수명이 요청이 아니라 세션이다. 스코프를 열면 캐시가 접속이 끊길 때까지
    살아 있게 되어, 이 모듈이 없애려던 stale 위험을 정확히 되살린다."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/flowgate/api/v1/events").json()["label"] is None


def test_scope_does_not_survive_the_response():
    """요청이 끝나면 스코프도 끝난다. 남으면 다음 요청이 남의 캐시를 본다."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        client.get("/async")
    assert request_cache.current() is None
