"""flowgate.default.0359 T0016 (4번 묶음): 끝난 실행을 나중에 다시 보기.

Until this bundle a finished run was only reachable by its run_id, and that id
lived nowhere a person could find it once the miniplayer card was gone (NR0003).
Bundles 1-3 already made the id findable again for the SAME chain (the paused
row) and gave every finished run a row in `ai_invoke_runs` (DB0008) — this
bundle adds the two read paths L0007 §2.10.2-3 promised on top of that data:

  * GET /ai-invoke/{run_id} falls back to the DB row once the run has left
    memory (a restarted process, or an id from days ago), tagging the answer
    ``persisted`` so a client knows which source it got.
  * GET /ai-invoke/runs lists recent runs for a group or a project, merging
    still-running rows (memory) with finished ones (DB), newest first.
  * Both routes now require `perm_document_read` on the run's project — before
    this bundle a persisted run_id alone was reachable by any signed-in user.

Service-layer tests exercise `list_live_runs` / `list_runs` / `get_run_detail`
directly against a fake `ai_invoke_runs` DB module (dict-backed, same contract
as db/ai_invoke_runs.py). Route-level tests pin the HTTP contract (422/403/404
ordering, and that "runs" is never swallowed by the {run_id} path) with a
FastAPI TestClient, mirroring test_ai_invoke_first_hop_mode_0317's harness.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.default.0359"
PROJECT = "flowgate"
DOC_REF = "flowgate.default.0359.0001-B"


def _live_run(run_id, *, group_id=GROUP, project_id=PROJECT, status="running",
              started_at="2026-07-31T10:00:00+09:00", pause_requested=False):
    return {
        "run_id": run_id, "status": status, "mode": "continuous",
        "project_id": project_id, "group_id": group_id, "doc_ref": DOC_REF,
        "docs_target": 3, "provider": {"id": "aip_1", "name": "cli-1"},
        "provider_id": "aip_1", "attempt_no": 1, "attempts_used": 0,
        "attempts_max": 3, "hop_item_seq": 2, "started_at": started_at,
        "started_mono": 0.0, "pause_requested": pause_requested,
        "timeout_sec": 3600, "deadline_at": "2026-07-31T11:00:00+09:00",
        "completion_oracle": None, "docs_reached": 0,
    }


def _stored_row(run_id, *, group_id=GROUP, project_id=PROJECT,
                 started_at="2026-07-31T09:00:00+09:00",
                 finished_at="2026-07-31T09:30:00+09:00",
                 stop_code="chain_completed", resumable=False):
    return {
        "run_id": run_id, "group_id": group_id, "project_id": project_id,
        "doc_ref": DOC_REF, "mode": "continuous", "status": "finished",
        "outcome": "complete", "docs_reached": 1, "docs_target": 3,
        "reached_doc_ids": [f"{DOC_REF.rsplit('.', 1)[0]}.0002-N"],
        "end_reason": "exited", "stop_code": stop_code,
        "stop_reason": "Target step reached; the chain is complete.",
        "resumable": resumable, "exit_code": 0,
        "last_message": "done", "last_message_excerpt": "done",
        "provider_id": "aip_1", "provider_name": "cli-1",
        "attempt_no": 1, "attempts_used": 1, "attempts_max": 3,
        "fallback_history": [], "register_errors": [],
        "tool_call_misses": 0, "turn_limit_exhausted": False,
        "oracle_mismatch": False, "source_dirty": False,
        "scratch_retained": None, "hop_item_seq": 2, "token_id": "tok_1",
        "issued_to": "usr_admin", "started_at": started_at,
        "finished_at": finished_at, "duration_ms": 1800000,
        "timeout_sec": 3600, "deadline_at": "2026-07-31T10:00:00+09:00",
    }


class FakeRunsStore:
    """Dict-backed stand-in honouring the ai_invoke_runs contract (DB0008)."""

    def __init__(self, rows=None):
        self.rows: dict[str, dict] = {r["run_id"]: dict(r) for r in (rows or [])}

    def get(self, run_id):
        row = self.rows.get(run_id)
        return dict(row) if row else None

    def list_by_group(self, group_id, limit):
        rows = [r for r in self.rows.values() if r["group_id"] == group_id]
        rows.sort(key=lambda r: (r["started_at"], r["run_id"]), reverse=True)
        return [dict(r) for r in rows[:limit]]

    def list_by_project(self, project_id, limit):
        rows = [r for r in self.rows.values() if r["project_id"] == project_id]
        rows.sort(key=lambda r: (r["started_at"], r["run_id"]), reverse=True)
        return [dict(r) for r in rows[:limit]]

    def count_by_group(self, group_id):
        return sum(1 for r in self.rows.values() if r["group_id"] == group_id)

    def count_by_project(self, project_id):
        return sum(1 for r in self.rows.values() if r["project_id"] == project_id)


@pytest.fixture
def fake_runs(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    store = FakeRunsStore()
    for name in ("get", "list_by_group", "list_by_project", "count_by_group", "count_by_project"):
        monkeypatch.setattr(db_runs, name, getattr(store, name))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    return store


# ── list_live_runs ────────────────────────────────────────────────────────────

def test_list_live_runs_scopes_by_group_and_excludes_finished(fake_runs, monkeypatch):
    monkeypatch.setattr(svc, "_runs", {
        "aiv_1": _live_run("aiv_1", group_id=GROUP),
        "aiv_2": _live_run("aiv_2", group_id="flowgate.default.9999"),
        "aiv_3": _live_run("aiv_3", group_id=GROUP, status="finished"),
    })
    items = svc.list_live_runs(group_id=GROUP)
    assert [i["run_id"] for i in items] == ["aiv_1"]
    assert items[0]["outcome"] is None and items[0]["finished_at"] is None


def test_list_live_runs_scopes_by_project(fake_runs, monkeypatch):
    monkeypatch.setattr(svc, "_runs", {
        "aiv_1": _live_run("aiv_1", project_id="flowgate"),
        "aiv_2": _live_run("aiv_2", project_id="otherproj", group_id="otherproj.default.1"),
    })
    items = svc.list_live_runs(project_id="flowgate")
    assert [i["run_id"] for i in items] == ["aiv_1"]


def test_list_live_runs_reports_pause_requested_status(fake_runs, monkeypatch):
    monkeypatch.setattr(svc, "_runs", {
        "aiv_1": _live_run("aiv_1", pause_requested=True),
    })
    items = svc.list_live_runs(group_id=GROUP)
    assert items[0]["status"] == "pause_requested"


# ── list_runs ─────────────────────────────────────────────────────────────────

def test_list_runs_requires_exactly_one_of_group_or_project(fake_runs):
    with pytest.raises(HTTPException) as exc_info:
        svc.list_runs()
    assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException) as exc_info:
        svc.list_runs(group_id=GROUP, project=PROJECT)
    assert exc_info.value.status_code == 422


def test_list_runs_unknown_project_is_404(fake_runs, monkeypatch):
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: None)
    with pytest.raises(HTTPException) as exc_info:
        svc.list_runs(project="ghost")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "project_not_found"


def test_list_runs_merges_live_and_stored_newest_first(fake_runs, monkeypatch):
    fake_runs.rows.update({
        r["run_id"]: r for r in [
            _stored_row("aiv_old", started_at="2026-07-31T08:00:00+09:00"),
            _stored_row("aiv_mid", started_at="2026-07-31T09:00:00+09:00"),
        ]
    })
    monkeypatch.setattr(svc, "_runs", {
        "aiv_new": _live_run("aiv_new", started_at="2026-07-31T10:00:00+09:00"),
    })
    result = svc.list_runs(group_id=GROUP)
    assert result["ok"] is True
    assert result["group_id"] == GROUP
    assert [i["run_id"] for i in result["items"]] == ["aiv_new", "aiv_mid", "aiv_old"]
    assert result["total"] == 3
    assert result["has_more"] is False


def test_list_runs_does_not_double_count_a_run_that_is_both_live_and_stored(fake_runs, monkeypatch):
    # The instant right after finalize persists the row: the run can still be in
    # `_runs` (not yet reaped) AND already have a DB row. It must appear ONCE.
    fake_runs.rows["aiv_1"] = _stored_row("aiv_1")
    monkeypatch.setattr(svc, "_runs", {"aiv_1": _live_run("aiv_1", status="finished")})
    # A "finished" live run is excluded from list_live_runs by construction, so the
    # DB row is the only copy that surfaces — no duplicate id in items.
    result = svc.list_runs(group_id=GROUP)
    ids = [i["run_id"] for i in result["items"]]
    assert ids.count("aiv_1") == 1


def test_list_runs_limit_is_clamped(fake_runs):
    fake_runs.rows.update({
        f"aiv_{i}": _stored_row(f"aiv_{i}", started_at=f"2026-07-31T0{i}:00:00+09:00")
        for i in range(5)
    })
    result = svc.list_runs(group_id=GROUP, limit=2)
    assert result["limit"] == 2
    assert len(result["items"]) == 2
    assert result["has_more"] is True

    result_over_cap = svc.list_runs(group_id=GROUP, limit=99999)
    assert result_over_cap["limit"] == svc.RUN_LIST_LIMIT_MAX

    result_zero = svc.list_runs(group_id=GROUP, limit=0)
    assert result_zero["limit"] == svc.RUN_LIST_LIMIT_DEFAULT


def test_list_runs_by_project_scopes_across_groups(fake_runs):
    fake_runs.rows.update({
        "aiv_a": _stored_row("aiv_a", group_id="flowgate.default.aaaa"),
        "aiv_b": _stored_row("aiv_b", group_id="flowgate.default.bbbb"),
    })
    result = svc.list_runs(project=PROJECT)
    assert result["project"] == PROJECT
    assert {i["run_id"] for i in result["items"]} == {"aiv_a", "aiv_b"}


def test_list_runs_unknown_group_is_empty_not_404(fake_runs):
    result = svc.list_runs(group_id="flowgate.default.0000")
    assert result["ok"] is True
    assert result["items"] == []
    assert result["total"] == 0


# ── get_run_detail ────────────────────────────────────────────────────────────

def test_get_run_detail_prefers_memory_and_marks_not_persisted(fake_runs, monkeypatch):
    monkeypatch.setattr(svc, "_runs", {"aiv_1": _live_run("aiv_1")})
    payload = svc.get_run_detail("aiv_1")
    assert payload["persisted"] is False
    assert payload["run_id"] == "aiv_1"
    assert payload["status"] == "running"
    # Existing status-payload fields (L0007 §2.13) must survive untouched.
    assert payload["timeout_sec"] == 3600
    assert payload["deadline_at"] == "2026-07-31T11:00:00+09:00"


def test_get_run_detail_falls_back_to_db_when_not_in_memory(fake_runs):
    fake_runs.rows["aiv_gone"] = _stored_row("aiv_gone")
    payload = svc.get_run_detail("aiv_gone")
    assert payload["persisted"] is True
    assert payload["status"] == "finished"
    assert payload["group_id"] == GROUP
    assert payload["stop_code"] == "chain_completed"
    assert payload["last_message_received"] is True
    assert payload["timeout_sec"] == 3600


def test_get_run_detail_memory_wins_over_a_stale_db_row(fake_runs, monkeypatch):
    fake_runs.rows["aiv_1"] = _stored_row("aiv_1", stop_code="timeout")
    monkeypatch.setattr(svc, "_runs", {"aiv_1": _live_run("aiv_1")})
    payload = svc.get_run_detail("aiv_1")
    assert payload["persisted"] is False
    assert payload["status"] == "running"


def test_get_run_detail_unknown_id_is_404(fake_runs):
    with pytest.raises(HTTPException) as exc_info:
        svc.get_run_detail("aiv_nope")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "run_not_found"


# ── HTTP route contract ────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)

    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_1", "is_admin": False},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    return TestClient(app, raise_server_exceptions=False)


def _get(client, path, **params):
    return client.get(path, params=params, headers={"Authorization": "Bearer tok"})


def test_runs_route_requires_exactly_one_scope(client):
    resp = _get(client, "/api/v1/ai-invoke/runs")
    assert resp.status_code == 422
    resp = _get(client, "/api/v1/ai-invoke/runs", group_id=GROUP, project=PROJECT)
    assert resp.status_code == 422


def test_runs_route_denies_without_permission(client, monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **kw: False)
    resp = _get(client, "/api/v1/ai-invoke/runs", group_id=GROUP)
    assert resp.status_code == 403
    assert resp.json()["code"] == "permission_denied"


def test_runs_route_is_not_shadowed_by_run_id_route(client, monkeypatch):
    """/runs must resolve to the list handler, not read "runs" as a run_id — this
    is the routing-order requirement L0007 §2.10.3 calls out by name."""
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **kw: True)
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "list_runs",
                        lambda **kw: {"ok": True, "group_id": kw.get("group_id"),
                                      "limit": 20, "total": 0, "has_more": False, "items": []})
    resp = _get(client, "/api/v1/ai-invoke/runs", group_id=GROUP)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "items" in body


def test_run_detail_route_denies_without_permission(client, monkeypatch):
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "get_run_detail",
                        lambda run_id: {"ok": True, "run_id": run_id, "group_id": GROUP,
                                        "status": "finished", "persisted": True})
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **kw: False)
    resp = _get(client, "/api/v1/ai-invoke/aiv_1")
    assert resp.status_code == 403


def test_run_detail_route_404_wins_over_permission(client, monkeypatch):
    """An unknown run id must 404 even for a caller who lacks the permission that
    would otherwise gate it — L0007 §2.10.2: "없는 번호는 권한과 무관하게 404"."""
    monkeypatch.setattr(
        ai_invoke_routes.ai_invoke_service, "get_run_detail",
        lambda run_id: (_ for _ in ()).throw(
            HTTPException(status_code=404, detail={"code": "run_not_found", "message": "x"})
        ),
    )
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **kw: False)
    resp = _get(client, "/api/v1/ai-invoke/aiv_missing")
    assert resp.status_code == 404


def test_run_detail_route_returns_persisted_payload(client, monkeypatch):
    monkeypatch.setattr(
        ai_invoke_routes.ai_invoke_service, "get_run_detail",
        lambda run_id: {"ok": True, "run_id": run_id, "group_id": GROUP,
                        "status": "finished", "persisted": True, "stop_code": "chain_completed"},
    )
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **kw: True)
    resp = _get(client, "/api/v1/ai-invoke/aiv_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["persisted"] is True
    assert body["stop_code"] == "chain_completed"
