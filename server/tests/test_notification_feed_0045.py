"""R0001 group 0045 — 🔔 notification center feed (NR0003 option A) + group 0118 noise reduction.

Group 0045 introduced the feed and its unread-count derivation against a per-user last-seen watermark.
Group 0118 (R0001 "쓸모없는 알림기능") then decoupled the feed from the dashboard recent-activity card:
a single document registration decomposed into 3~5 workflow_events (creation + review submit/approve +
parent cascade), each projected as its own notification. The feed now uses the quieter
_NOTIFICATION_EVENT_TYPES (creation + qna_answered + group_approved), dropping state_changed and
doc_edited noise, while the dashboard card keeps the full _ACTIVITY_EVENT_TYPES stream. These tests
cover both: the unread math AND the new event-type filtering / decoupling.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

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
def feed_store(monkeypatch):
    store = _Store()
    store.conn.executescript(
        """
        CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL);
        CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_name TEXT, is_active INTEGER);
        CREATE TABLE groups (
            group_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            title TEXT NOT NULL, deleted_at TEXT
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
        INSERT INTO users VALUES ('u1', 'developer');
        INSERT INTO projects VALUES ('flowgate', 'FlowGate', 1);
        INSERT INTO groups VALUES ('flowgate.default.0045', 'flowgate', 'SSE inflow', NULL);
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0045.0006-D', 'flowgate', 'flowgate.default.0045',
                'D', 'Design', 'pending_review', '2026-06-12T00:03:00Z');
        """
    )
    doc_id = store._fetch_one("SELECT id FROM documents")["id"]
    # One registration's worth of events, mirroring the live shape: a creation, the two review
    # micro-transitions it triggers, an edit, and a later group-approval milestone. Only the
    # creation and the group-approval are notification-worthy; the rest is the 0118 noise.
    store.conn.executemany(
        """
        INSERT INTO workflow_events
            (event_type, project_id, group_id, document_id, actor_user_id,
             from_state, to_state, metadata, created_at)
        VALUES (?, 'flowgate', 'flowgate.default.0045', ?, 'u1', ?, ?, ?, ?)
        """,
        [
            ("doc_created", doc_id, None, "draft", None, "2026-06-12T00:01:00Z"),
            # noise: review submit micro-transition (dropped from the feed)
            ("state_changed", doc_id, "review:", "review:pending_review",
             '{"from": "review:", "to": "review:pending_review", "action": "review_submit"}',
             "2026-06-12T00:02:00Z"),
            # noise: an edit (dropped from the feed)
            ("doc_edited", doc_id, None, None, None, "2026-06-12T00:03:00Z"),
            # milestone: group approved (kept in the feed)
            ("group_approved", None, None, None, '{"status": "approved"}', "2026-06-12T00:04:00Z"),
        ],
    )
    store.conn.commit()
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def test_feed_excludes_registration_noise(feed_store):
    """Core 0118 fix: state_changed (review submit/approve) and doc_edited never reach the feed."""
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    activities = result["recent_activities"]
    assert activities["total"] == 2  # only creation + group_approved survive the filter
    types = {item["activity_type"] for item in activities["items"]}
    assert types == {"document_created", "group_approved"}
    # The noisy per-document review/edit transitions are gone.
    assert "workflow_state_changed" not in types
    assert "document_state_changed" not in types
    assert "document_edited" not in types


def test_feed_excludes_final_approved_group(feed_store):
    """Completed groups must not keep unread/attention notification rows alive."""
    feed_store.conn.execute(
        """
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0045.0001-R', 'flowgate', 'flowgate.default.0045',
                'R', 'Requirement', 'wf_done', '2026-06-12T00:05:00Z')
        """
    )
    feed_store.conn.commit()

    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["unread_count"] == 0
    assert result["recent_activities"]["total"] == 0
    assert result["recent_activities"]["items"] == []


def test_feed_excludes_discarded_group(feed_store):
    """A DC discard record is also terminal for the notification feed."""
    feed_store.conn.execute(
        """
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0045.0005-DC', 'flowgate', 'flowgate.default.0045',
                'DC', 'Group Discard', NULL, '2026-06-12T00:05:00Z')
        """
    )
    feed_store.conn.commit()

    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["unread_count"] == 0
    assert result["recent_activities"]["total"] == 0
    assert result["recent_activities"]["items"] == []


def test_dashboard_card_keeps_terminal_group_history(feed_store):
    """The terminal filter is notification-only; dashboard recent activity remains an audit stream."""
    feed_store.conn.execute(
        """
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0045.0001-R', 'flowgate', 'flowgate.default.0045',
                'R', 'Requirement', 'wf_done', '2026-06-12T00:05:00Z')
        """
    )
    feed_store.conn.commit()

    result = dashboard_service.list_recent_activities("flowgate", 50)

    assert result["total"] == 4


def test_dashboard_card_still_shows_full_stream(feed_store):
    """Decoupling guard: the dashboard recent-activity card keeps the full event stream."""
    result = dashboard_service.list_recent_activities("flowgate", 50)
    # All four events normalize for the dashboard (creation, state_changed, edit, group_approved).
    assert result["total"] == 4
    types = {item["activity_type"] for item in result["items"]}
    assert "workflow_state_changed" in types
    assert "document_edited" in types


def test_feed_all_unread_when_never_seen(feed_store):
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    assert result["ok"] is True
    assert result["last_seen_at"] is None
    assert result["unread_count"] == 2
    activities = result["recent_activities"]
    assert activities["total"] == 2
    assert activities["items"][0]["occurred_at"] == "2026-06-12T00:04:00.000Z"  # newest first


def test_feed_unread_counts_only_items_after_watermark(feed_store):
    result = dashboard_service.get_notification_feed(
        "flowgate", "2026-06-12T00:01:00Z", 50
    )
    # Strictly newer than the watermark → only the 00:04 group_approved is unread
    # (the 00:01 creation is not strictly newer; the 00:02/00:03 noise is filtered out entirely).
    assert result["unread_count"] == 1
    assert result["last_seen_at"] == "2026-06-12T00:01:00.000Z"
    assert result["recent_activities"]["total"] == 2  # full (filtered) history still returned


def test_feed_zero_unread_when_caught_up(feed_store):
    result = dashboard_service.get_notification_feed(
        "flowgate", "2026-06-12T23:59:00Z", 50
    )
    assert result["unread_count"] == 0


def test_feed_unread_counts_full_history_not_just_page(feed_store):
    # The unread badge must reflect ALL unread inflow, even when the returned page is truncated.
    result = dashboard_service.get_notification_feed("flowgate", None, 1)
    assert result["unread_count"] == 2
    assert len(result["recent_activities"]["items"]) == 1
    assert result["recent_activities"]["has_more"] is True


def test_feed_includes_finished_ai_page_with_exact_contract(feed_store, monkeypatch):
    rows = [
        {
            "run_id": "run-002", "doc_ref": "flowgate.default.0045.0006-D",
            "doc_title": "Design", "doc_type_code": "D", "outcome": "complete",
            "docs_reached": 2, "docs_target": 2, "end_reason": "completed",
            "stop_code": None, "provider_name": "Codex",
            "finished_at": "2026-06-12T00:06:00Z", "last_message_excerpt": "all done",
        },
        {
            "run_id": "run-001", "doc_ref": "deleted-doc",
            "doc_title": None, "doc_type_code": None, "outcome": "partial",
            "docs_reached": 1, "docs_target": 2, "end_reason": "stopped",
            "stop_code": "worker_failed", "provider_name": "Claude",
            "finished_at": "2026-06-12T00:05:00Z", "last_message_excerpt": "failed",
        },
    ]
    monkeypatch.setattr(
        dashboard_service.db_ai_invoke_runs,
        "list_finished_for_notifications",
        lambda project_id, limit: (rows[:limit], 3),
    )

    result = dashboard_service.get_notification_feed("flowgate", None, 2)

    page = result["ai_invoke_runs"]
    assert page["limit"] == 2
    assert page["total"] == 3
    assert page["has_more"] is True
    assert [item["run_id"] for item in page["items"]] == ["run-002", "run-001"]
    assert page["items"][0]["succeeded"] is True
    assert page["items"][1]["succeeded"] is False
    assert page["items"][1]["doc_title"] is None
    assert set(page["items"][0]) == {
        "run_id", "doc_ref", "doc_title", "doc_type_code", "succeeded", "outcome",
        "docs_reached", "docs_target", "end_reason", "stop_code", "provider_name",
        "finished_at", "last_message_excerpt",
    }
    assert "last_message" not in page["items"][0]
    assert result["degraded_sections"] == []


def test_feed_degrades_only_ai_section(feed_store, monkeypatch):
    def fail(_project_id, _limit):
        raise RuntimeError("AI store unavailable")

    monkeypatch.setattr(
        dashboard_service.db_ai_invoke_runs,
        "list_finished_for_notifications",
        fail,
    )

    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["ok"] is True
    assert result["recent_activities"]["total"] == 2
    assert result["ai_invoke_runs"] == {
        "limit": 50, "total": 0, "has_more": False, "items": [],
    }
    assert result["degraded_sections"] == ["ai_runs"]
