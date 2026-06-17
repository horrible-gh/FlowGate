from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import dashboard_routes
from modules.flow_gate.services import dashboard_service


class _Store:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self.conn.execute(sql, params or []).fetchall()]

    def _fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def dashboard_store(monkeypatch):
    store = _Store()
    store.conn.executescript(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL
        );
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            project_name TEXT,
            is_active INTEGER
        );
        CREATE TABLE groups (
            group_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE NOT NULL,
            project_id TEXT NOT NULL,
            group_id TEXT,
            type_code TEXT NOT NULL,
            title TEXT NOT NULL,
            doc_review_status TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            project_id TEXT NOT NULL,
            group_id TEXT,
            document_id INTEGER,
            actor_user_id TEXT,
            from_state TEXT,
            to_state TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE workflow_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE workflow_sequence_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sequence_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            result_doc_id TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def _seed_base(store: _Store):
    store.conn.executescript(
        """
        INSERT INTO users VALUES ('u1', 'developer');
        INSERT INTO projects VALUES ('flowgate', 'FlowGate', 1);
        INSERT INTO groups VALUES ('flowgate.default.0020', 'flowgate', 'Dashboard work', NULL);
        INSERT INTO documents
            (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES
            ('flowgate.default.0020.0001-R', 'flowgate', 'flowgate.default.0020',
             'R', 'Dashboard requirement', 'wf_in_progress', '2026-06-12T00:00:00Z'),
            ('flowgate.default.0020.0006-D', 'flowgate', 'flowgate.default.0020',
             'D', 'Dashboard design', 'pending_review', '2026-06-12T00:03:00Z'),
            ('flowgate.default.0020.0010-Q', 'flowgate', 'flowgate.default.0020',
             'Q', 'Question', 'approved', '2026-06-12T00:01:00Z'),
            ('flowgate.default.0020.0011-A', 'flowgate', 'flowgate.default.0020',
             'A', 'Answer', 'pending_review', '2026-06-12T00:02:00Z');
        INSERT INTO workflow_sequences (doc_id, updated_at)
        VALUES ('flowgate.default.0020.0001-R', '2026-06-12T00:01:00Z');
        INSERT INTO workflow_sequence_items
            (sequence_id, type, sort_order, result_doc_id, updated_at)
        VALUES
            (1, 'D', 1, 'flowgate.default.0020.0006-D', '2026-06-12T00:02:00Z'),
            (1, 'T', 2, NULL, '2026-06-12T00:00:00Z');
        """
    )
    document_ids = {
        row["doc_id"]: row["id"]
        for row in store._fetch_all("SELECT id, doc_id FROM documents")
    }
    store.conn.executemany(
        """
        INSERT INTO workflow_events
            (event_type, project_id, group_id, document_id, actor_user_id,
             from_state, to_state, metadata, created_at)
        VALUES (?, 'flowgate', 'flowgate.default.0020', ?, 'u1', ?, ?, ?, ?)
        """,
        [
            (
                "doc_created",
                document_ids["flowgate.default.0020.0006-D"],
                None,
                "open",
                None,
                "2026-06-12T00:01:00Z",
            ),
            (
                "doc_edited",
                None,
                None,
                None,
                '{"doc_id":"flowgate.default.0020.0006-D"}',
                "2026-06-12T00:02:00Z",
            ),
            (
                "state_changed",
                document_ids["flowgate.default.0020.0006-D"],
                "review:pending_review",
                "review:approved",
                '{"action":"review_approve"}',
                "2026-06-12T00:03:00Z",
            ),
            (
                "qna_answered",
                None,
                "open",
                "answered",
                '{"q_doc_id":"flowgate.default.0020.0010-Q",'
                '"a_doc_id":"flowgate.default.0020.0011-A"}',
                "2026-06-12T00:04:00Z",
            ),
            (
                "action_taken",
                None,
                None,
                None,
                '{"action_code":"ignored"}',
                "2026-06-12T00:05:00Z",
            ),
        ],
    )
    store.conn.commit()


def test_summary_normalizes_activities_and_effective_head(dashboard_store):
    _seed_base(dashboard_store)

    result = dashboard_service.get_dashboard_summary("flowgate", 3, 10)

    activities = result["recent_activities"]
    assert activities["total"] == 4
    assert activities["has_more"] is True
    assert [item["activity_type"] for item in activities["items"]] == [
        "question_answered",
        "workflow_state_changed",
        "document_edited",
    ]
    assert activities["items"][0]["document"]["type_code"] == "A"
    assert activities["items"][1]["transition"] == {
        "from_state": "pending_review",
        "to_state": "approved",
    }

    workflows = result["active_workflows"]
    assert workflows["total"] == 1
    workflow = workflows["items"][0]
    assert workflow["stage"]["state"] == "in_progress"
    assert workflow["stage"]["head_doc_id"] == "flowgate.default.0020.0006-D"
    assert workflow["progress"] == {
        "completed_steps": 0,
        "total_steps": 2,
        "percent": 0,
    }
    assert workflow["updated_at"] == "2026-06-12T00:03:00.000Z"


def test_summary_uses_pending_head_after_approved_result(dashboard_store):
    _seed_base(dashboard_store)
    dashboard_store.conn.execute(
        "UPDATE documents SET doc_review_status = 'approved' "
        "WHERE doc_id = 'flowgate.default.0020.0006-D'"
    )
    dashboard_store.conn.commit()

    workflow = dashboard_service.list_active_workflows("flowgate", 10)["items"][0]

    assert workflow["stage"] == {
        "state": "pending",
        "type_code": "T",
        "head_doc_id": None,
        "head_doc_title": None,
        "head_doc_review_status": None,
    }
    assert workflow["progress"] == {
        "completed_steps": 1,
        "total_steps": 2,
        "percent": 50,
    }
    assert workflow["navigation"]["doc_id"] == "flowgate.default.0020.0001-R"


def test_summary_uses_final_approval_after_all_sequence_slots_complete(
    dashboard_store,
):
    _seed_base(dashboard_store)
    dashboard_store.conn.execute(
        "UPDATE documents SET doc_review_status = 'approved' "
        "WHERE doc_id = 'flowgate.default.0020.0006-D'"
    )
    dashboard_store.conn.execute(
        "DELETE FROM workflow_sequence_items WHERE type = 'T'"
    )
    dashboard_store.conn.commit()

    workflow = dashboard_service.list_active_workflows("flowgate", 10)["items"][0]

    assert workflow["stage"] == {
        "state": "pending",
        "type_code": "AC",
        "head_doc_id": None,
        "head_doc_title": None,
        "head_doc_review_status": None,
    }
    assert workflow["progress"] == {
        "completed_steps": 1,
        "total_steps": 1,
        "percent": 100,
    }
    assert workflow["navigation"]["doc_id"] == "flowgate.default.0020.0001-R"


def test_summary_excludes_discarded_group_from_active_workflows(dashboard_store):
    # R0079.0001: discarding a group creates a file-less DC record but never flips
    # the requirement out of wf_in_progress, so the discarded group used to keep
    # showing in the dashboard "워크플로 현황" list. The DC record must drop it.
    _seed_base(dashboard_store)

    # Sanity: before discard the group is an active workflow.
    assert dashboard_service.list_active_workflows("flowgate", 10)["total"] == 1

    dashboard_store.conn.execute(
        """
        INSERT INTO documents
            (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES
            ('flowgate.default.0020.0012-DC', 'flowgate', 'flowgate.default.0020',
             'DC', 'Group Discard', NULL, '2026-06-12T00:06:00Z')
        """
    )
    dashboard_store.conn.commit()

    workflows = dashboard_service.list_active_workflows("flowgate", 10)
    assert workflows["total"] == 0
    assert workflows["items"] == []


def test_summary_rejects_active_requirement_without_sequence(dashboard_store):
    _seed_base(dashboard_store)
    dashboard_store.conn.execute("DELETE FROM workflow_sequences")
    dashboard_store.conn.commit()

    with pytest.raises(dashboard_service.DashboardDataError, match="sequence missing"):
        dashboard_service.list_active_workflows("flowgate", 10)


def _client(monkeypatch, service_result=None):
    app = FastAPI()
    app.include_router(dashboard_routes.router)
    app.dependency_overrides[dashboard_routes._dashboard_current_user] = (
        lambda: {"user_id": "u1", "is_active": 1}
    )
    monkeypatch.setattr(
        dashboard_routes.db_projects,
        "get_by_id",
        lambda project_id: {"project_id": project_id} if project_id == "flowgate" else None,
    )
    monkeypatch.setattr(dashboard_routes, "has_permission", lambda *_args: True)
    if service_result is not None:
        monkeypatch.setattr(
            dashboard_routes,
            "get_dashboard_summary",
            lambda *_args: service_result,
        )
    return TestClient(app)


def test_dashboard_route_returns_no_store_and_validates_limits(monkeypatch):
    result = {
        "ok": True,
        "project_id": "flowgate",
        "generated_at": "2026-06-12T00:00:00.000Z",
        "recent_activities": {"limit": 10, "total": 0, "has_more": False, "items": []},
        "active_workflows": {"limit": 10, "total": 0, "has_more": False, "items": []},
    }
    client = _client(monkeypatch, result)

    response = client.get("/api/v1/projects/flowgate/dashboard/summary")
    invalid = client.get(
        "/api/v1/projects/flowgate/dashboard/summary?activity_limit=0"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert invalid.status_code == 400
    assert invalid.headers["cache-control"] == "no-store"


def test_dashboard_route_enforces_permission(monkeypatch):
    client = _client(monkeypatch, {})
    monkeypatch.setattr(dashboard_routes, "has_permission", lambda *_args: False)

    response = client.get("/api/v1/projects/flowgate/dashboard/summary")

    assert response.status_code == 403
    assert "perm_document_read" in response.json()["error_message"]


def test_dashboard_route_auth_error_is_not_cached():
    app = FastAPI()
    app.include_router(dashboard_routes.router)
    response = TestClient(app).get("/api/v1/projects/flowgate/dashboard/summary")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
