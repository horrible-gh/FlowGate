"""Request-scoped read cache and query accounting.

observed over 15 identical reads of a row within one request.
The cache in db/request_cache.py absorbs this duplication within request boundaries.

These tests protect correctness, not just performance. Most verify that caching
is bypassed where necessary: outside a scope (workers/startup), after writes,
inside transactions, with different parameters, and after callers mutate results.

A fake store counts actual database calls. Comparing SQLite return values alone
cannot distinguish a working cache from uncached reads that return correct data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import request_cache  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402
from modules.flow_gate.db import connection as _connection  # noqa: E402


class _FakeBackend:
    """Minimal backend counting calls.

    fetch_one/fetch_all select FlowGateStore's non-transaction path. Return a new
    dict on each call: reusing one backend object would invalidate copy-out evidence.
    """

    db_type = None  # The dialect property resolves to SQLITE (translation is a no-op)

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
    """Store and fake backend with caching enabled.

    TESTING=1 disables caching by default (request_cache policy 5). Enable it
    explicitly, as the 0282 meta_cache tests do.
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


# ── Cache hits ──

def test_same_read_twice_in_one_scope_hits_db_once(store):
    """Finding 5: identical queries within one request reach the DB only once."""
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
    """Include parameters in the cache key so different documents remain distinct."""
    s, backend = store
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d2"])
    assert len(backend.one_calls) == 2


def test_scopes_do_not_leak_into_each_other(store):
    """One request bounds cache lifetime and prevents cross-request staleness."""
    s, backend = store
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
    with request_cache.request_scope("GET /x"):
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2


# ── Cases that must bypass caching ──

def test_no_scope_means_no_caching(store):
    """Workers and startup open no scope, preserving their original behavior."""
    s, backend = store
    s._fetch_one(SQL_ONE, ["d1"])
    s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2
    assert request_cache.current() is None


def test_write_in_scope_invalidates_read_your_writes(store):
    """Rule 2: the most important case in this suite.

    Returning a cached pre-write value after a write is a data bug, not an optimization.
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
    """Pin the decision not to track per-table dependencies.

    Invalidate even queries apparently unrelated to the UPDATE: SQL text alone
    cannot establish independence, and a bad inference produces silent wrong answers.
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
    """Writes include more than _execute. Missing update_cas would leave only reads
    after CAS updates stale, producing a difficult-to-reproduce bug."""
    s, backend = store
    with request_cache.request_scope("POST /x") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s.update_cas("documents", "d1", "doc_id", "status", "open", {"status": "closed"})
        s._fetch_one(SQL_ONE, ["d1"])
    assert len(backend.one_calls) == 2
    assert scope.writes == 1


def test_reads_inside_a_transaction_are_not_cached(store, monkeypatch):
    """Rule 3: transactions are units of writing."""
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
    """Keep accounting active when caching is disabled.

    Counters must not depend on the cache setting: the review 4-8 requires the same
    measurement scale before and after P1–P3.
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
    """Skip shapes such as doc_id IN (?×900), where building the key costs more than
    caching saves. Results remain identical and accounting still records the call."""
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
    """Rule 4: get_rejected_documents_with_reasons() adds reject_events to its returned
    dict. Without copying, the next caller sees that mutation."""
    s, _ = store
    with request_cache.request_scope("GET /x"):
        first = s._fetch_one(SQL_ONE, ["d1"])
        first["injected"] = True
        first["tags"].append("mutated")       # Include nested structures: only deepcopy protects them
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
    """Absence is a valid result. Treating None as a cache miss repeats missing-row
    lookups, exactly as observed among the duplicates in finding 5."""
    s, backend = store
    backend.fetch_one = lambda sql, params: (backend.one_calls.append((sql, tuple(params))) or None)
    with request_cache.request_scope("GET /x") as scope:
        assert s._fetch_one(SQL_ONE, ["missing"]) is None
        assert s._fetch_one(SQL_ONE, ["missing"]) is None
    assert len(backend.one_calls) == 1
    assert scope.hits == 1


# ── Scope lifecycle ──

def test_nested_scope_joins_the_outer_one(store):
    """One scope represents one request. Nested scopes indicate imprecise boundaries;
    joining the existing scope is preferable to only partially effective caching."""
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
    """A request ending in an exception must not leave its scope behind, or the next
    request on that thread will read another request's cache."""
    s, _ = store
    with pytest.raises(RuntimeError):
        with request_cache.request_scope("GET /x"):
            s._fetch_one(SQL_ONE, ["d1"])
            raise RuntimeError("boom")
    assert request_cache.current() is None


def test_summary_reports_the_numbers_4_8_asked_for(store):
    """The per-endpoint query counts marked estimated in the review become measured
    values here because the scope coincides with the request boundary."""
    s, _ = store
    with request_cache.request_scope("GET /flowgate/api/v1/tree") as scope:
        s._fetch_one(SQL_ONE, ["d1"])
        s._fetch_one(SQL_ONE, ["d1"])
        s._execute("UPDATE documents SET title = ? WHERE doc_id = ?", ["t", "d1"])
    summary = scope.summary()
    assert "GET /flowgate/api/v1/tree" in summary
    assert "reads=2" in summary and "db=1" in summary
    assert "cached=1" in summary and "writes=1" in summary


# ── Middleware boundary ──
#
# Mount only the middleware on a minimal app without importing routers/main.py.
# Verify that the scope reaches the endpoint, not the application routing.

def _app_with_middleware():
    from fastapi import FastAPI
    from modules.flow_gate.api.request_scope_middleware import RequestScopeMiddleware

    app = FastAPI()

    @app.get("/sync")
    def sync_endpoint():          # noqa: ANN202 — the path FastAPI runs in its thread pool
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
    """Most endpoints are synchronous def functions (the review: 40 AnyIO threads
    compete for a semaphore of 20). Without contextvar propagation into the pool,
    every test could pass while no real endpoint benefits from caching."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/sync").json()["label"] == "GET /sync"


def test_scope_reaches_an_async_endpoint():
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/async").json()["label"] == "GET /async"


def test_sse_stream_gets_no_scope():
    """SSE lives for a session, not one request. Opening a cache scope would retain
    data until disconnect and reintroduce exactly the staleness risk being removed."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        assert client.get("/flowgate/api/v1/events").json()["label"] is None


def test_scope_does_not_survive_the_response():
    """End the scope with the request so the next request cannot reuse its cache."""
    from fastapi.testclient import TestClient

    with TestClient(_app_with_middleware()) as client:
        client.get("/async")
    assert request_cache.current() is None
