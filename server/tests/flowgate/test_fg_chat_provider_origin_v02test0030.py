"""a chat AI call says how the browser picked its AI.

On stg a chat run went to the system default AI although the user had picked their own, and the run row only
said ``selected_provider_source = 'request'``: the browser named that AI, but nothing told which screen path
made it do so. A chat /ai-invoke/start now carries ``client_provider_origin`` (user_pick / stored /
last_answered / default_fallback) and the finished run row keeps it (migration 193).

* the route accepts the four values for action_scope=chat only, refuses anything else with 422 before a run is
  started, and hands the value to start_run (it never changes which AI runs);
* finalize copies it from the run into the row;
* the row stores it when the column exists, and a DB before 193 still writes the row (without it).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CHAT = "flowgate.v02test.0030.0002-CH"
T = "2026-11-17T00:00:00.000+00:00"
_TEMPLATES: dict = {}


# -- the start route -------------------------------------------------------------

@pytest.fixture()
def route(monkeypatch):
    from modules.flow_gate.api.v1 import ai_invoke_routes
    from modules.flow_gate.services import chat_start_requests

    chat_start_requests.clear()
    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes, "db_projects", SimpleNamespace(get_by_id=lambda project: {"id": project}))
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "get_run_record", lambda run_id: {"status": "running"})
    calls: list[dict] = []

    def start_run(**kw):
        calls.append(kw)
        return {"ok": True, "run_id": f"aiv2_20261117_{len(calls):06d}", "status": "running"}

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def start(**extra):
        body = {"project": "flowgate", "module": "v02test", "group": "0030", "doc_ref": CHAT,
                "action_scope": "chat", "mode": "single", "provider_id": "aip_opus", **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers={"Authorization": "Bearer tok"})

    yield SimpleNamespace(start=start, calls=calls)
    chat_start_requests.clear()


@pytest.mark.parametrize("origin", ["user_pick", "stored", "last_answered", "default_fallback"])
def test_a_chat_start_hands_its_origin_to_start_run(route, origin):
    res = route.start(client_provider_origin=origin)
    assert res.status_code == 200, res.text
    assert route.calls[-1]["client_provider_origin"] == origin
    assert route.calls[-1]["provider_id"] == "aip_opus"   # the AI that runs is still the one named


def test_a_chat_start_without_an_origin_is_unchanged(route):
    res = route.start()
    assert res.status_code == 200, res.text
    assert route.calls[-1]["client_provider_origin"] is None


@pytest.mark.parametrize("extra", [
    {"client_provider_origin": "guess"},
    {"client_provider_origin": ""},
    {"client_provider_origin": "stored", "action_scope": "edit"},
])
def test_an_unknown_origin_or_another_scope_is_refused_before_any_run(route, extra):
    res = route.start(**extra)
    assert res.status_code == 422, res.text
    assert "client_provider_origin" in res.text
    assert route.calls == []


# -- finalize -> row -------------------------------------------------------------

def test_finalize_copies_the_origin_into_the_run_row(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs
    from modules.flow_gate.services import ai_invoke_service as svc

    rows: list[dict] = []
    monkeypatch.setattr(ai_invoke_runs, "upsert", lambda row: rows.append(row))
    base = {"run_id": "aiv2_20261117_000001", "group_id": "flowgate.v02test.0030", "project_id": "flowgate",
            "doc_ref": CHAT, "mode": "single", "outcome": "complete", "end_reason": "exited",
            "started_at": T, "finished_at": T}
    svc._persist_run_record({**base, "client_provider_origin": "last_answered"})
    svc._persist_run_record({**base, "run_id": "aiv2_20261117_000002"})
    assert [r.get("client_provider_origin") for r in rows] == ["last_answered", None]


# -- the row over a real SQLite DB -----------------------------------------------

def _template(upto):
    if upto not in _TEMPLATES:
        memory = sqlite3.connect(":memory:")
        for mig in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if upto is not None and mig.name > upto:
                break
            memory.executescript(mig.read_text(encoding="utf-8"))
        path = Path(tempfile.mkdtemp(prefix="fg0030_")) / "template.db"
        target = sqlite3.connect(str(path))
        memory.backup(target)
        target.close()
        memory.close()
        _TEMPLATES[upto] = str(path)
    return _TEMPLATES[upto]


def _open(tmp_path, monkeypatch, upto=None):
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.db import connection as conn_mod

    path = str(tmp_path / "runs.db")
    shutil.copyfile(_template(upto), path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES ('p1', 'p1', ?, ?)",
                 [T, T])
    conn.execute("INSERT INTO groups (group_id, project_id, title, created_at, updated_at) "
                 "VALUES ('g1', 'p1', 'g1', ?, ?)", [T, T])
    conn.commit()
    conn.close()
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    monkeypatch.setattr(conn_mod, "STORE", store)
    return path


def _rows(path, sql):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def _row(run_id, **extra):
    return {"run_id": run_id, "group_id": "g1", "project_id": "p1", "doc_ref": "d", "mode": "single",
            "started_at": T, "finished_at": T, "created_at": T, "updated_at": T, **extra}


def test_the_run_row_keeps_the_origin(tmp_path, monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs

    path = _open(tmp_path, monkeypatch)
    ai_invoke_runs.upsert(_row("told", client_provider_origin="default_fallback"))
    ai_invoke_runs.upsert(_row("untold"))
    rows = {r["run_id"]: r for r in _rows(path, "SELECT run_id, client_provider_origin FROM ai_invoke_runs")}
    assert rows["told"]["client_provider_origin"] == "default_fallback"
    assert rows["untold"]["client_provider_origin"] is None
    assert ai_invoke_runs.get("told")["client_provider_origin"] == "default_fallback"


def test_a_db_before_193_still_writes_the_run_row(tmp_path, monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs

    path = _open(tmp_path, monkeypatch, upto="192_zzz")
    ai_invoke_runs.upsert(_row("old", client_provider_origin="stored"))
    assert _rows(path, "SELECT run_id FROM ai_invoke_runs") == [{"run_id": "old"}]
