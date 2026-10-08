"""T#3: legacy Snapshot retirement (0684 T#4 removed the read-only Bundle visibility)."""
import sqlite3
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from modules.flow_gate.api.v1 import snapshot_routes
from modules.flow_gate.db import snapshot_requests
from modules.flow_gate.services import (
    api_server_tools, help_catalog, snapshot_access_service,
    snapshot_materialization_service, snapshot_request_service,
)


class Store:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE snapshot_requests (snapshot_id TEXT PRIMARY KEY, project_id TEXT, "
            "group_id TEXT, run_id TEXT, chain_id TEXT, status TEXT, requested_at TEXT, "
            "requested_paths TEXT, rejected_at TEXT, failure_code TEXT, failure_reason TEXT, "
            "created_at TEXT, expires_at TEXT)"
        )
        for sid, status in [("requested", "requested"), ("approved", "approved"), ("created", "created")]:
            self.conn.execute(
                "INSERT INTO snapshot_requests "
                "(snapshot_id,project_id,group_id,run_id,status,requested_at,requested_paths,created_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, "p", "g", "r", status, "2026-09-01", "[]", "2026-09-01", "2026-09-02"),
            )
        self.conn.commit()

    def transaction(self):
        return nullcontext()

    def _fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params)]

    def _execute_affected(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount


def test_retirement_closes_requested_and_approved_once_without_touching_created(monkeypatch):
    store = Store()
    events = []
    monkeypatch.setattr(snapshot_requests, "get_store", lambda: store)
    monkeypatch.setattr(snapshot_request_service, "get_store", lambda: store)
    monkeypatch.setattr(snapshot_request_service.workflow_events, "create", events.append)
    closed = snapshot_request_service.retire_unmaterialized("u-system")
    assert {row["snapshot_id"] for row in closed} == {"requested", "approved"}
    assert {row["retired_from"] for row in closed} == {"requested", "approved"}
    assert all(row["failure_code"] == "snapshot_feature_retired" for row in closed)
    assert snapshot_requests.list_pending("p", "g") == []
    assert snapshot_requests.get("created")["status"] == "created"
    assert {event["from_state"] for event in events} == {"requested", "approved"}
    assert all(event["event_type"] == "snapshot_retired" for event in events)
    assert snapshot_request_service.retire_unmaterialized("u-system") == []
    assert len(events) == 2
    store.conn.close()


def test_creation_decision_materialization_and_execution_are_retired():
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as error:
        snapshot_request_service.create_request({}, "actor")
    assert (error.value.status, error.value.code) == (410, "snapshot_feature_retired")
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as error:
        snapshot_request_service.decide("s", "approved", "actor")
    assert error.value.status == 410
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as error:
        snapshot_materialization_service.materialize("s", "actor")
    assert error.value.status == 410
    with pytest.raises(snapshot_access_service.SnapshotAccessError) as error:
        snapshot_access_service.execute({}, {"snapshot_id": "s"}, remaining_sec=30)
    assert error.value.status == 410
    with pytest.raises(api_server_tools.ToolError) as error:
        api_server_tools.request_source_snapshot({}, "", {})
    assert error.value.status == 410
    with pytest.raises(api_server_tools.ToolError) as error:
        api_server_tools.run_source_snapshot({}, "", {}, 30)
    assert error.value.status == 410

    calls = [
        lambda: snapshot_routes.request_snapshot(None),
        lambda: snapshot_routes.cli_request_snapshot(None),
        lambda: snapshot_routes.approve("s", user={"user_id": "u"}),
        lambda: snapshot_routes.reject("s", user={"user_id": "u"}),
        lambda: snapshot_routes.materialize_snapshot("s", user={"user_id": "u"}),
        lambda: snapshot_routes.cli_materialize_snapshot("s", None),
        lambda: snapshot_routes.cli_run_snapshot("s", None),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 410
        assert error.value.detail["code"] == "snapshot_feature_retired"


def test_created_history_and_cleanup_contract_remain(monkeypatch):
    store = Store()
    monkeypatch.setattr(snapshot_requests, "get_store", lambda: store)
    monkeypatch.setattr(snapshot_routes.materialization, "refresh_stale", lambda sid, actor: snapshot_requests.get(sid))
    rows = snapshot_routes.active("p", "g", user={"user_id": "u"})
    assert [row["snapshot_id"] for row in rows["requests"]] == ["created"]
    cleaned = []
    monkeypatch.setattr(snapshot_materialization_service, "cleanup_orphans", lambda **kw: None)
    monkeypatch.setattr(snapshot_materialization_service, "cleanup",
                        lambda sid, actor, **kw: cleaned.append(sid) or {"status": "deleted"})
    summary = snapshot_materialization_service.sweep_expired()
    assert summary["deleted"] == 1
    assert cleaned == ["created"]
    store.conn.close()


def test_retired_http_routes_return_410_even_without_a_request_body():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u"}
    client = TestClient(app)
    for path in (
        "/api/v1/snapshots", "/api/v1/snapshots/cli/request",
        "/api/v1/snapshots/s/approve", "/api/v1/snapshots/s/reject",
        "/api/v1/snapshots/s/materialize", "/api/v1/snapshots/cli/s/materialize",
        "/api/v1/snapshots/cli/s/run",
    ):
        response = client.post(path)
        assert response.status_code == 410, path
        assert response.json()["detail"]["code"] == "snapshot_feature_retired"
