import json
import os
from contextlib import contextmanager
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "api")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.db import project_ai_leases
from modules.flow_gate.services import git_service
from modules.flow_gate.api.v1 import ai_invoke_routes


@pytest.fixture(autouse=True)
def _isolated_project_ai_lease(monkeypatch):
    # Regression guard: a resolve_base_dirty start here actually activates a project lease.
    # Without forcing memory mode, that lease used to persist in the REAL store for the
    # "flowgate" project across the whole pytest session, so a later, unrelated test that
    # also starts a resolve_base_dirty run for "flowgate" (e.g. test_ai_run_owner_all_
    # issuers_0393.py) could 409 on base_dirty_run_in_progress depending on file run order.
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    yield
    project_ai_leases._memory.clear()


def test_terminal_cleanup_persists_snapshot_and_residual_reasons(monkeypatch):
    rows = [
        {"group_id": "p.none.0001", "status": "merged"},
        {"group_id": "p.none.0002", "status": "pushed"},
        {"group_id": "p.none.0003", "status": "waiting"},
        {"group_id": "p.none.0004", "status": "waiting"},
    ]
    monkeypatch.setattr(git_service, "_require_enabled_config", lambda _p: {})
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *_: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _p: rows)
    monkeypatch.setattr(git_service, "_is_group_disposed", lambda gid: gid == "p.none.0003")
    monkeypatch.setattr(git_service, "tr_conflict_session", lambda gid: {"id": 1} if gid == "p.none.0002" else None)
    monkeypatch.setattr(git_service, "_cleanup_group_slot", lambda _p, gid: gid == "p.none.0001")
    saved = {}
    monkeypatch.setattr(git_service.db_terminal_cleanup, "put", lambda p, s, n, pending: saved.update(project=p, status=s, count=n, pending=pending) or {"last_run_at": "now", "last_run_status": s, "last_cleaned_count": n, "pending": pending})

    result = git_service.cleanup_terminal_slots("p")
    assert result["result"] == {"cleaned": ["p.none.0001"], "failed": ["p.none.0003"]}
    assert saved["status"] == "partial" and saved["count"] == 1
    assert saved["pending"] == [
        {"group_id": "p.none.0002", "reason": "revert_conflict"},
        {"group_id": "p.none.0003", "reason": "teardown_failed"},
    ]


class _Request:
    headers = {"x-locale": "ko"}
    url = type("URL", (), {"scheme": "http", "hostname": "localhost", "port": 80})()


def test_resolve_base_dirty_starts_without_group_and_normalizes_envelope(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    captured = {}
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", lambda **kw: captured.update(kw) or {"ok": True, "run_id": "aiv_1"})
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")
    body = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single")
    response = ai_invoke_routes.start_ai_invoke(body, _Request())
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["group_id"] is None and payload["doc_ref"] == "" and payload["docs_target"] == 0
    assert captured["action_scope"] == "resolve_base_dirty"


def test_resolve_base_dirty_empty_precedes_start(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": []}}})
    body = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single")
    response = ai_invoke_routes.start_ai_invoke(body, _Request())
    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "base_dirty_empty"


def test_project_lease_activate_rejects_replaced_acquiring_owner(monkeypatch):
    """A zero-row CAS must not treat a newer owner's lease as this run's success."""
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: False)

    class Store:
        @contextmanager
        def transaction(self):
            yield self

        def _execute_affected(self, _sql, _params):
            # Our acquiring row expired; another request now owns the project row.
            return 0

        def _fetch_one(self, *_args):
            raise AssertionError("activate must not read another owner's lease after a failed CAS")

    monkeypatch.setattr(project_ai_leases, "get_store", lambda: Store())
    assert project_ai_leases.activate("flowgate", "expired-owner", "aiv_old") is None


def test_terminal_cleanup_zero_candidates_persists_ok_snapshot(monkeypatch):
    monkeypatch.setattr(git_service, "_require_enabled_config", lambda _p: {})
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *_: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _p: [])
    saved = {}
    monkeypatch.setattr(git_service.db_terminal_cleanup, "put", lambda p, s, n, pending: saved.update(status=s, count=n, pending=pending) or {"last_run_status": s})

    result = git_service.cleanup_terminal_slots("p")
    assert result["result"] == {"cleaned": [], "failed": []}
    assert saved == {"status": "ok", "count": 0, "pending": []}


def test_terminal_cleanup_all_failures_persist_failed_snapshot(monkeypatch):
    monkeypatch.setattr(git_service, "_require_enabled_config", lambda _p: {})
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *_: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _p: [{"group_id": "p.none.0001", "status": "merged"}])
    monkeypatch.setattr(git_service, "_cleanup_group_slot", lambda *_: False)
    saved = {}
    monkeypatch.setattr(git_service.db_terminal_cleanup, "put", lambda p, s, n, pending: saved.update(status=s, count=n, pending=pending) or {})

    assert git_service.cleanup_terminal_slots("p")["result"]["failed"] == ["p.none.0001"]
    assert saved == {"status": "failed", "count": 0, "pending": [{"group_id": "p.none.0001", "reason": "teardown_failed"}]}


def test_terminal_cleanup_busy_does_not_write_snapshot(monkeypatch):
    monkeypatch.setattr(git_service, "_require_enabled_config", lambda _p: {})
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_: False)
    writes = []
    monkeypatch.setattr(git_service.db_terminal_cleanup, "put", lambda *args: writes.append(args))

    with pytest.raises(Exception) as exc:
        git_service.cleanup_terminal_slots("p")
    assert getattr(exc.value, "code", None) == "git_busy"
    assert writes == []


def test_snapshot_memory_round_trip_is_atomic_shape(monkeypatch):
    from modules.flow_gate.db import terminal_cleanup_snapshots as snapshots
    monkeypatch.setattr(snapshots, "_using_memory", lambda: True)
    snapshots._memory.clear()
    assert snapshots.get("p") == snapshots.empty()
    written = snapshots.put("p", "partial", 1, [{"group_id": "p.none.1", "reason": "revert_conflict"}])
    assert snapshots.get("p") == written


def test_project_lease_acquiring_and_active_ttl_are_reclaimed(monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    project_ai_leases._memory["p"] = {"project_id": "p", "run_id": "old", "state": "acquiring", "expires_at": past}
    assert project_ai_leases.acquire("p", "new")["run_id"] == "new"
    project_ai_leases._memory["p"]["expires_at"] = past
    assert project_ai_leases.get_active("p") is None


def test_base_dirty_start_rejects_continuous_and_keeps_other_scope_group_required(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    continuous = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="continuous")
    response = ai_invoke_routes.start_ai_invoke(continuous, _Request())
    assert response.status_code == 422
    assert json.loads(response.body)["code"] == "validation_failed"
    ordinary = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="edit", mode="single")
    response = ai_invoke_routes.start_ai_invoke(ordinary, _Request())
    assert response.status_code == 422


def test_base_dirty_project_admission_blocks_same_project_but_allows_other(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": _p})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", lambda **kw: {"run_id": "run-" + kw["project_id"]})
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    first = ai_invoke_routes.start_ai_invoke(ai_invoke_routes.AiInvokeStartRequest(project="a", module="none", action_scope="resolve_base_dirty", mode="single"), _Request())
    again = ai_invoke_routes.start_ai_invoke(ai_invoke_routes.AiInvokeStartRequest(project="a", module="none", action_scope="resolve_base_dirty", mode="single"), _Request())
    other = ai_invoke_routes.start_ai_invoke(ai_invoke_routes.AiInvokeStartRequest(project="b", module="none", action_scope="resolve_base_dirty", mode="single"), _Request())
    assert first.status_code == 200 and other.status_code == 200
    assert again.status_code == 409 and json.loads(again.body)["code"] == "base_dirty_run_in_progress"


def test_base_dirty_mention_unavailable_releases_the_project_lease(monkeypatch):
    """T0011 완료 기준 2: mention_builder가 빈 값을 반환하면(engine이 HTTPException(409,
    mention_unavailable)로 신호) 토큰 회수는 ai_invoke_service.start_run 내부 책임이고,
    이 라우트는 project lease를 명시적으로 release한 뒤 같은 409를 그대로 전달해야 한다."""
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")

    def _raise_mention_unavailable(**_kw):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail={"code": "mention_unavailable", "message": "empty mention"})

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _raise_mention_unavailable)
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()

    body = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single")
    response = ai_invoke_routes.start_ai_invoke(body, _Request())

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "mention_unavailable"
    # The acquiring row must be gone — release() ran, not merely skipped.
    assert project_ai_leases.get_active("flowgate") is None


def test_base_dirty_activate_null_cancels_the_run_without_releasing_a_stolen_row(monkeypatch):
    """T0011 완료 기준 2: activate()가 null이면(acquiring 행이 이미 다른 소유자로 교체됨)
    이 자리는 release를 호출하지 않고 방금 시작된 run만 취소한 뒤 409 run_lease_lost를
    반환해야 한다 — 남의 리스를 지우면 그 리스가 막으려는 동시성 보장이 깨진다."""
    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", lambda **kw: {"run_id": "aiv_started"})
    cancelled = []
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "cancel_run", lambda run_id, **kw: cancelled.append(run_id))
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    released = []
    monkeypatch.setattr(project_ai_leases, "release", lambda *a: released.append(a))
    # activate() finds no matching acquiring row (another owner already replaced it).
    monkeypatch.setattr(project_ai_leases, "activate", lambda *a: None)

    body = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single")
    response = ai_invoke_routes.start_ai_invoke(body, _Request())

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "run_lease_lost"
    assert cancelled == ["aiv_started"]
    assert released == []   # must not release a row this owner no longer holds