"""TS group 0471 (target R0001) — server scenarios for the 🔔 notification feed:

1. the 3-section response shape (recent_activities / ai_invoke_runs / open_questions),
2. the AI section's final-state derivation (ai_run_succeeded),
3. the Q&A unanswered-question list (open_questions), and
4. permission denial and empty-list boundary values on the HTTP route.

test_notification_feed_0045.py already covers the service-level activity-noise
filtering and most open_questions SQL semantics; this file adds what neither it nor
test_dashboard_summary.py exercises: the *whole-feed-empty* boundary (no events, no
AI runs, no questions at all), each section paging independently, the exact
ai_run_succeeded truth table, and route-level 403/404/401/limit-boundary behaviour
for GET /notifications and POST /notifications/seen (test_dashboard_summary.py only
covers /dashboard/summary's route wiring, never the notification endpoints).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import dashboard_routes
from modules.flow_gate.services import ai_invoke_service, dashboard_service


# ── 2. AI section final-state derivation ──────────────────────────────────────

@pytest.mark.parametrize(
    "outcome,stop_code,end_reason,expected",
    [
        ("complete", None, "completed", True),
        ("complete", None, None, True),
        ("COMPLETE", "", "", True),  # case/whitespace-insensitive on both fields
        ("partial", None, "completed", False),  # outcome must be exactly 'complete'
        (None, None, "completed", False),  # missing outcome
        ("complete", "worker_failed", "completed", False),  # any stop_code blocks success
        ("complete", None, "cancelled", False),
        ("complete", None, "canceled", False),
        ("complete", None, "failed", False),
        ("complete", None, "error", False),
        ("complete", None, "stopped", False),
        ("complete", None, "timeout", False),
        ("complete", None, "TIMEOUT", False),  # end_reason check is also case-insensitive
        (" complete ", None, "completed", True),  # outcome whitespace is stripped, not just cased
        ("complete", None, " timeout ", False),  # end_reason whitespace is stripped before the failure-set check
        ("complete", "   ", "completed", True),  # a whitespace-only stop_code strips to empty: does not block success
    ],
)
def test_ai_run_succeeded_matrix(outcome, stop_code, end_reason, expected):
    row = {"outcome": outcome, "stop_code": stop_code, "end_reason": end_reason}
    assert ai_invoke_service.ai_run_succeeded(row) is expected


# ── shared sqlite-backed store (mirrors test_notification_feed_0045.py) ───────

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
def empty_store(monkeypatch):
    """A project with zero workflow_events, zero AI runs, zero open questions —
    the genuine empty-feed boundary."""
    store = _Store()
    store.conn.executescript(
        """
        CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL);
        CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_name TEXT, is_active INTEGER);
        CREATE TABLE groups (
            group_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            title TEXT NOT NULL, status TEXT, closed_at TEXT, deleted_at TEXT
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE NOT NULL,
            project_id TEXT NOT NULL, group_id TEXT, type_code TEXT NOT NULL,
            title TEXT NOT NULL, doc_review_status TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
            project_id TEXT NOT NULL, group_id TEXT, document_id INTEGER,
            actor_user_id TEXT, from_state TEXT, to_state TEXT, metadata TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO projects VALUES ('flowgate', 'FlowGate', 1);
        """
    )
    store.conn.commit()
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    monkeypatch.setattr(dashboard_service.db_questions, "list_open_items", lambda _project_id: [])
    monkeypatch.setattr(
        dashboard_service.db_ai_invoke_runs,
        "list_finished_for_notifications",
        lambda _project_id, _limit: ([], 0),
    )
    return store


# ── 1. the 3-section response shape ────────────────────────────────────────────

def test_feed_three_sections_present_and_empty_on_a_fresh_project(empty_store):
    """recent_activities / ai_invoke_runs / open_questions must ALL be present, each
    shaped {limit, total, has_more, items}, even when the project has nothing yet."""
    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["ok"] is True
    assert set(result) >= {
        "recent_activities", "ai_invoke_runs", "open_questions",
        "unread_count", "badge_count", "degraded_sections", "last_seen_at",
    }
    for section in ("recent_activities", "ai_invoke_runs", "open_questions"):
        assert result[section] == {"limit": 50, "total": 0, "has_more": False, "items": []}
    assert result["unread_count"] == 0
    assert result["badge_count"] == 0
    assert result["degraded_sections"] == []
    assert result["last_seen_at"] is None


def test_feed_three_sections_page_independently(empty_store, monkeypatch):
    """Each section pages on its own limit/total/has_more/items — a full recent_activities
    page must not starve, inflate, or bleed content into the other two sections."""
    empty_store.conn.execute(
        """
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0471.0001-D', 'flowgate', NULL, 'D', 'Design', 'draft', '2026-06-12T00:01:00Z')
        """
    )
    doc_id = empty_store._fetch_one("SELECT id FROM documents")["id"]
    empty_store.conn.executemany(
        """
        INSERT INTO workflow_events (event_type, project_id, group_id, document_id, actor_user_id,
            from_state, to_state, metadata, created_at)
        VALUES ('doc_created', 'flowgate', NULL, ?, 'u1', NULL, 'draft', NULL, ?)
        """,
        [(doc_id, f"2026-06-12T00:0{i}:00Z") for i in range(1, 4)],
    )
    empty_store.conn.commit()

    # AI section: the DB layer itself truncates to `limit` and reports the untruncated total
    # separately, so the stub mirrors that contract — 2 real rows back, 5 as the true total.
    ai_row_1 = {
        "run_id": "run-1", "doc_ref": "flowgate.default.0471.0003-T", "doc_title": "Run 1",
        "doc_type_code": "T", "outcome": "complete", "docs_reached": 3, "docs_target": 3,
        "end_reason": "completed", "stop_code": None, "provider_name": "anthropic",
        "finished_at": "2026-06-12T00:05:00Z", "last_message_excerpt": "done",
    }
    ai_row_2 = {
        "run_id": "run-2", "doc_ref": "flowgate.default.0471.0004-T", "doc_title": "Run 2",
        "doc_type_code": "T", "outcome": "partial", "docs_reached": 1, "docs_target": 3,
        "end_reason": "timeout", "stop_code": None, "provider_name": "anthropic",
        "finished_at": "2026-06-12T00:04:00Z", "last_message_excerpt": "stalled",
    }
    monkeypatch.setattr(
        dashboard_service.db_ai_invoke_runs,
        "list_finished_for_notifications",
        lambda _project_id, _limit: ([ai_row_1, ai_row_2], 5),
    )

    # open_questions: list_open_items is NOT limit-aware — _open_question_page does its own
    # in-memory slicing — so the stub returns all 3 unanswered docs and the service must cut it.
    monkeypatch.setattr(
        dashboard_service.db_questions,
        "list_open_items",
        lambda _project_id: [
            {"doc_id": "flowgate.default.0471.0002-T", "title": "q", "type_code": "T"},
            {"doc_id": "flowgate.default.0471.0003-T", "title": "q", "type_code": "T"},
            {"doc_id": "flowgate.default.0471.0004-T", "title": "q", "type_code": "T"},
        ],
    )
    monkeypatch.setattr(dashboard_service.db_documents, "get_by_id", lambda _doc_id: {"title": "q"})

    result = dashboard_service.get_notification_feed("flowgate", None, 2)

    assert result["recent_activities"]["limit"] == 2
    assert result["recent_activities"]["total"] == 3
    assert result["recent_activities"]["has_more"] is True
    assert len(result["recent_activities"]["items"]) == 2

    assert result["ai_invoke_runs"] == {
        "limit": 2, "total": 5, "has_more": True,
        "items": [
            {
                "run_id": "run-1", "doc_ref": "flowgate.default.0471.0003-T", "doc_title": "Run 1",
                "doc_type_code": "T", "succeeded": True, "outcome": "complete", "docs_reached": 3,
                "docs_target": 3, "end_reason": "completed", "stop_code": None,
                "provider_name": "anthropic", "finished_at": "2026-06-12T00:05:00.000Z",
                "last_message_excerpt": "done",
            },
            {
                "run_id": "run-2", "doc_ref": "flowgate.default.0471.0004-T", "doc_title": "Run 2",
                "doc_type_code": "T", "succeeded": False, "outcome": "partial", "docs_reached": 1,
                "docs_target": 3, "end_reason": "timeout", "stop_code": None,
                "provider_name": "anthropic", "finished_at": "2026-06-12T00:04:00.000Z",
                "last_message_excerpt": "stalled",
            },
        ],
    }

    assert result["open_questions"] == {
        "limit": 2, "total": 3, "has_more": True,
        "items": [
            {"doc_id": "flowgate.default.0471.0002-T", "title": "q", "type_code": "T"},
            {"doc_id": "flowgate.default.0471.0003-T", "title": "q", "type_code": "T"},
        ],
    }


# ── 3. Q&A unanswered-question list ────────────────────────────────────────────

def test_open_questions_empty_when_project_has_no_pending_questions(empty_store):
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    assert result["open_questions"] == {"limit": 50, "total": 0, "has_more": False, "items": []}
    assert result["badge_count"] == 0


def test_open_questions_degrades_alone_without_touching_the_other_two_sections(
    empty_store, monkeypatch
):
    empty_store.conn.execute(
        """
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0471.0001-D', 'flowgate', NULL, 'D', 'Design', 'draft', '2026-06-12T00:01:00Z')
        """
    )
    doc_id = empty_store._fetch_one("SELECT id FROM documents")["id"]
    empty_store.conn.execute(
        """
        INSERT INTO workflow_events (event_type, project_id, group_id, document_id, actor_user_id,
            from_state, to_state, metadata, created_at)
        VALUES ('doc_created', 'flowgate', NULL, ?, 'u1', NULL, 'draft', NULL, '2026-06-12T00:01:00Z')
        """,
        [doc_id],
    )
    empty_store.conn.commit()
    monkeypatch.setattr(
        dashboard_service.db_questions,
        "list_open_items",
        lambda _project_id: (_ for _ in ()).throw(RuntimeError("questions store down")),
    )

    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["ok"] is True
    assert result["recent_activities"]["total"] == 1
    assert result["ai_invoke_runs"] == {"limit": 50, "total": 0, "has_more": False, "items": []}
    assert result["degraded_sections"] == ["open_questions"]
    assert result["badge_count"] == result["unread_count"]


# ── 4. permission denial + empty-list + limit boundary, at the HTTP route ─────

_EMPTY_FEED = {
    "ok": True, "project_id": "flowgate", "generated_at": "2026-06-12T00:00:00.000Z",
    "last_seen_at": None, "unread_count": 0, "badge_count": 0,
    "recent_activities": {"limit": 50, "total": 0, "has_more": False, "items": []},
    "ai_invoke_runs": {"limit": 50, "total": 0, "has_more": False, "items": []},
    "open_questions": {"limit": 50, "total": 0, "has_more": False, "items": []},
    "degraded_sections": [],
}


def _route_client(monkeypatch, *, project_exists=True, permitted=True):
    app = FastAPI()
    app.include_router(dashboard_routes.router)
    app.dependency_overrides[dashboard_routes._dashboard_current_user] = (
        lambda: {"user_id": "u1", "is_active": 1}
    )
    monkeypatch.setattr(
        dashboard_routes.db_projects,
        "get_by_id",
        lambda project_id: ({"project_id": project_id} if project_exists else None),
    )
    monkeypatch.setattr(dashboard_routes, "has_permission", lambda *_args: permitted)
    monkeypatch.setattr(dashboard_routes, "get_notification_feed", lambda *_args: dict(_EMPTY_FEED))
    monkeypatch.setattr(
        dashboard_routes.db_notification_seen, "get_last_seen", lambda _user_id, _project_id: None
    )
    monkeypatch.setattr(
        dashboard_routes.db_notification_seen,
        "mark_seen",
        lambda _user_id, _project_id: "2026-06-12T00:00:00.000Z",
    )
    return TestClient(app)


def test_route_notifications_denies_without_permission(monkeypatch):
    client = _route_client(monkeypatch, permitted=False)

    response = client.get("/api/v1/projects/flowgate/notifications")

    assert response.status_code == 403
    assert "perm_document_read" in response.json()["error_message"]
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_seen_denies_without_permission(monkeypatch):
    client = _route_client(monkeypatch, permitted=False)

    response = client.post("/api/v1/projects/flowgate/notifications/seen")

    assert response.status_code == 403
    assert "perm_document_read" in response.json()["error_message"]
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_404s_for_missing_project(monkeypatch):
    client = _route_client(monkeypatch, project_exists=False)

    response = client.get("/api/v1/projects/flowgate/notifications")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_404s_for_system_pseudo_project(monkeypatch):
    # __SYSTEM__ is rejected before the project lookup or the permission check —
    # both project_exists=True and permitted=True stay in effect here on purpose.
    client = _route_client(monkeypatch)

    response = client.get("/api/v1/projects/__SYSTEM__/notifications")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_401s_when_unauthenticated():
    app = FastAPI()
    app.include_router(dashboard_routes.router)

    response = TestClient(app).get("/api/v1/projects/flowgate/notifications")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_seen_404s_for_missing_project(monkeypatch):
    client = _route_client(monkeypatch, project_exists=False)

    response = client.post("/api/v1/projects/flowgate/notifications/seen")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_seen_404s_for_system_pseudo_project(monkeypatch):
    # Same __SYSTEM__ short-circuit as the GET route — project_exists=True and
    # permitted=True stay in effect on purpose, to prove __SYSTEM__ wins first.
    client = _route_client(monkeypatch)

    response = client.post("/api/v1/projects/__SYSTEM__/notifications/seen")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_seen_401s_when_unauthenticated():
    app = FastAPI()
    app.include_router(dashboard_routes.router)

    response = TestClient(app).post("/api/v1/projects/flowgate/notifications/seen")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("limit", ["1", "50"])
def test_route_notifications_accepts_limit_boundaries(monkeypatch, limit):
    client = _route_client(monkeypatch)

    response = client.get(f"/api/v1/projects/flowgate/notifications?limit={limit}")

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize("limit", ["0", "51", "abc"])
def test_route_notifications_rejects_limit_outside_1_to_50(monkeypatch, limit):
    client = _route_client(monkeypatch)

    response = client.get(f"/api/v1/projects/flowgate/notifications?limit={limit}")

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"


def test_route_notifications_returns_empty_shaped_sections_when_permitted(monkeypatch):
    client = _route_client(monkeypatch)

    response = client.get("/api/v1/projects/flowgate/notifications")
    body = response.json()

    assert response.status_code == 200
    for section in ("recent_activities", "ai_invoke_runs", "open_questions"):
        assert body[section] == {"limit": 50, "total": 0, "has_more": False, "items": []}
    assert body["unread_count"] == 0
    assert body["badge_count"] == 0


def test_route_notifications_seen_clears_unread_and_is_not_cached(monkeypatch):
    client = _route_client(monkeypatch)

    response = client.post("/api/v1/projects/flowgate/notifications/seen")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "project_id": "flowgate",
        "last_seen_at": "2026-06-12T00:00:00.000Z", "unread_count": 0,
    }
    assert response.headers["cache-control"] == "no-store"
