"""flowgate.default.0517 T0026 §2 — a human's snapshot rejection carries a reason.

Drives the real FastAPI routes over a real sqlite schema built from the migrations
(115/116/118 + 120), so the reason is proven end to end: typed by a human on the reject
route -> durable column -> audit event metadata -> the worker's CLI status / API tool
answer, and nowhere outside the requesting run's lineage.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import snapshot_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import snapshot_requests as request_db
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import snapshot_access_service as access
from modules.flow_gate.services import snapshot_request_service as service

MIGRATIONS = Path(__file__).parents[1] / "sql" / "migrations"
SCHEMA = (
    "115_snapshot_requests.sql", "116_snapshot_materialization.sql",
    "118_snapshot_lineage.sql", "120_snapshot_rejection_reason.sql",
)

RUN = {
    "run_id": "run_owner", "chain_id": "run_owner", "token_id": "tok_owner",
    "project_id": "flowgate", "group_id": "flowgate.default.0517",
    "provider_id": "aip_claude", "action_scope": "new",
    "doc_ref": "flowgate.default.0517.0026-T",
}
OTHER_GROUP_RUN = RUN | {
    "run_id": "run_other_group", "chain_id": "run_other_group", "token_id": "tok_og",
    "group_id": "flowgate.default.0999",
}
OTHER_RUN_SAME_GROUP = RUN | {
    "run_id": "run_unrelated", "chain_id": "run_unrelated", "token_id": "tok_un",
}
RUNS = {r["run_id"]: r for r in (RUN, OTHER_GROUP_RUN, OTHER_RUN_SAME_GROUP)}


class _Store:
    """The four store calls snapshot_requests.py makes, plus transaction(), on one sqlite
    file. A fresh instance on the same file stands in for a server restart/reload."""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    @contextmanager
    def transaction(self):
        yield self

    def _execute(self, sql, params):
        self.connection.execute(sql, params)
        self.connection.commit()

    def _execute_affected(self, sql, params):
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor.rowcount

    def _fetch_one(self, sql, params):
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params):
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "snapshots.sqlite"
    with sqlite3.connect(path) as conn:
        for name in SCHEMA:
            conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    holder = {"store": _Store(path)}
    monkeypatch.setattr(request_db, "get_store", lambda: holder["store"])
    monkeypatch.setattr(service, "get_store", lambda: holder["store"])
    events: list[dict] = []
    monkeypatch.setattr(service.workflow_events, "create", events.append)
    monkeypatch.setattr(service, "_notify", lambda *args: None)

    # Worker-token boundary: the bearer names a run; the run record is the live one.
    monkeypatch.setattr(
        snapshot_routes.token_service, "verify",
        lambda raw: {"ai_run_id": raw, "token_id": RUNS[raw]["token_id"]},
    )
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: RUNS.get(run_id))
    monkeypatch.setattr(snapshot_routes.service, "validate_request_authority", lambda token, run: token)

    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_reviewer"}
    client = TestClient(app)

    def create(snapshot_id: str) -> dict:
        return request_db.create({
            "snapshot_id": snapshot_id, "project_id": RUN["project_id"],
            "group_id": RUN["group_id"], "run_id": RUN["run_id"], "chain_id": RUN["chain_id"],
            "token_id": RUN["token_id"], "provider_id": RUN["provider_id"],
            "reason": "needs a real tree", "scope": "single_file",
            "requested_paths": ["server/app.py"], "purpose": "run pytest",
            "source_kind": "current_worktree",
        })

    def reload() -> None:
        holder["store"] = _Store(path)

    return {"client": client, "events": events, "create": create, "reload": reload}


def _cli_status(client, run_id: str, snapshot_id: str):
    return client.get(
        f"/api/v1/snapshots/cli/{snapshot_id}/status",
        headers={"Authorization": f"Bearer {run_id}"},
    )


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_every_dialect_adds_the_rejection_reason_column(dialect):
    sql = (MIGRATIONS / dialect / "120_snapshot_rejection_reason.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE snapshot_requests ADD COLUMN rejection_reason" in sql


def test_reject_without_a_reason_is_refused_and_the_request_stays_pending(env):
    env["create"]("snap_empty")
    for body in ({}, {"rejection_reason": ""}, {"rejection_reason": "   \n "}):
        response = env["client"].post("/api/v1/snapshots/snap_empty/reject", json=body)
        assert response.status_code == 422, body
        assert response.json()["detail"]["code"] == "rejection_reason_required"
    too_long = env["client"].post(
        "/api/v1/snapshots/snap_empty/reject", json={"rejection_reason": "x" * 4001},
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "rejection_reason_too_long"

    row = request_db.get("snap_empty")
    assert row["status"] == "requested" and row["rejection_reason"] is None
    assert env["events"] == []
    pending = env["client"].get("/api/v1/snapshots/pending", params={"project_id": "flowgate"})
    assert [r["snapshot_id"] for r in pending.json()["requests"]] == ["snap_empty"]


def test_reason_is_stored_audited_and_survives_a_reload(env):
    env["create"]("snap_r")
    response = env["client"].post(
        "/api/v1/snapshots/snap_r/reject",
        json={"rejection_reason": "  전체 트리는 과합니다. server/app.py 하나만 요청하세요.  "},
    )
    assert response.status_code == 200, response.text
    body = response.json()["request"]
    assert body["status"] == "rejected"
    assert body["rejected_by"] == "usr_reviewer"
    assert body["rejection_reason"] == "전체 트리는 과합니다. server/app.py 하나만 요청하세요."

    [event] = env["events"]
    assert event["event_type"] == "snapshot_rejected"
    assert event["actor_user_id"] == "usr_reviewer"
    assert (event["from_state"], event["to_state"]) == ("requested", "rejected")
    assert json.loads(event["metadata"])["rejection_reason"] == body["rejection_reason"]

    env["reload"]()  # a fresh connection on the same file: new process / relogin
    detail = env["client"].get("/api/v1/snapshots/snap_r")
    assert detail.status_code == 200
    assert detail.json()["request"]["rejection_reason"] == body["rejection_reason"]
    pending = env["client"].get("/api/v1/snapshots/pending", params={"project_id": "flowgate"})
    assert pending.json()["requests"] == []


def test_repeated_reject_is_idempotent_and_keeps_the_first_reason(env):
    env["create"]("snap_twice")
    first = env["client"].post("/api/v1/snapshots/snap_twice/reject", json={"rejection_reason": "first"})
    assert first.status_code == 200
    again = env["client"].post("/api/v1/snapshots/snap_twice/reject", json={"rejection_reason": "second"})
    assert again.status_code == 200
    assert again.json()["request"]["rejection_reason"] == "first"
    # The pre-T0026 call shape (no reason) on an already-decided request answers as before.
    legacy = env["client"].post("/api/v1/snapshots/snap_twice/reject", json={})
    assert legacy.status_code == 200
    assert legacy.json()["request"]["status"] == "rejected"
    assert len(env["events"]) == 1
    missing = env["client"].post("/api/v1/snapshots/snap_nope/reject", json={})
    assert missing.status_code == 404


def test_rejected_request_still_cannot_be_approved_or_materialized(env):
    env["create"]("snap_block")
    env["client"].post("/api/v1/snapshots/snap_block/reject", json={"rejection_reason": "no"})
    approve = env["client"].post("/api/v1/snapshots/snap_block/approve", json={})
    assert approve.status_code == 409
    assert approve.json()["detail"]["code"] == "snapshot_not_approved"
    materialized = env["client"].post(
        "/api/v1/snapshots/cli/snap_block/materialize", headers={"Authorization": "Bearer run_owner"},
    )
    assert materialized.status_code == 409
    row = request_db.get("snap_block")
    assert row["status"] == "rejected" and row["rejection_reason"] == "no"


def test_approve_path_is_unchanged_and_carries_no_reason(env, monkeypatch):
    env["create"]("snap_ok")
    monkeypatch.setattr(
        snapshot_routes.materialization, "materialize",
        lambda snapshot_id, actor: request_db.get(snapshot_id),
    )
    response = env["client"].post("/api/v1/snapshots/snap_ok/approve", json={})
    assert response.status_code == 200
    row = response.json()["request"]
    assert row["status"] == "approved" and row["approved_by"] == "usr_reviewer"
    assert row["rejection_reason"] is None and row["rejected_at"] is None


def test_requesting_worker_reads_the_reason_through_cli_status_and_the_api_tool(env):
    env["create"]("snap_w")
    env["client"].post("/api/v1/snapshots/snap_w/reject", json={"rejection_reason": "범위를 좁혀 다시 요청하세요"})

    status = _cli_status(env["client"], "run_owner", "snap_w")
    assert status.status_code == 200, status.text
    snapshot = status.json()["snapshot"]
    assert snapshot["status"] == "rejected"
    assert snapshot["rejection_reason"] == "범위를 좁혀 다시 요청하세요"
    assert snapshot["rejected_at"]
    assert snapshot["current_worktree_validation_allowed"] is False

    # API-provider tool path (access_source_snapshot operation=status): refused as not
    # ready, and the refusal itself tells the worker why.
    with pytest.raises(access.SnapshotAccessError) as caught:
        access.access(RUN, {"operation": "status", "snapshot_id": "snap_w"})
    payload = caught.value.payload("status")
    assert payload["state"] == "rejected"
    assert payload["error"]["code"] == "snapshot_not_ready"
    assert payload["error"]["details"]["rejection_reason"] == "범위를 좁혀 다시 요청하세요"


@pytest.mark.parametrize("run_id", ["run_other_group", "run_unrelated"])
def test_unrelated_group_or_run_never_sees_the_reason(env, run_id):
    env["create"]("snap_private")
    env["client"].post("/api/v1/snapshots/snap_private/reject", json={"rejection_reason": "secret-ish reviewer note"})
    status = _cli_status(env["client"], run_id, "snap_private")
    assert status.status_code == 403
    assert "secret-ish reviewer note" not in status.text
    with pytest.raises(access.SnapshotAccessError) as caught:
        access.access(RUNS[run_id], {"operation": "status", "snapshot_id": "snap_private"})
    assert "secret-ish reviewer note" not in json.dumps(caught.value.payload("status"), ensure_ascii=False)


def test_worker_facing_reason_keeps_the_public_output_boundary(env):
    env["create"]("snap_path")
    typed = r"C:\storage\flowgate\work\secret\tok_x 경로 말고 상대경로로 요청하세요"
    env["client"].post("/api/v1/snapshots/snap_path/reject", json={"rejection_reason": typed})
    # Stored exactly as the human typed it — the server adds nothing of its own.
    assert request_db.get("snap_path")["rejection_reason"] == typed
    snapshot = _cli_status(env["client"], "run_owner", "snap_path").json()["snapshot"]
    assert "C:\\storage" not in snapshot["rejection_reason"]
    assert "상대경로로 요청하세요" in snapshot["rejection_reason"]


def test_lifecycle_close_is_a_system_rejection_without_a_reason(env):
    row = env["create"]("snap_life")
    request_db.close_unmaterialized_for_group(row["group_id"], "snapshot-group-cleanup")
    closed = request_db.get("snap_life")
    assert closed["status"] == "rejected" and closed["rejection_reason"] is None
    snapshot = _cli_status(env["client"], "run_owner", "snap_life").json()["snapshot"]
    assert snapshot["status"] == "rejected" and snapshot["rejection_reason"] is None
