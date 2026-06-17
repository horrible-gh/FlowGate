"""R0001 group 0045 — 🔔 notification center feed (NR0003 option A).

The feed reuses the dashboard's workflow_events normalization, so these tests focus on what is NEW:
the unread-count derivation against the per-user last-seen watermark, and that the feed payload
shape matches the dashboard recent-activities contract. Mirrors test_dashboard_summary's in-memory
store fixture.
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
    store.conn.executemany(
        """
        INSERT INTO workflow_events
            (event_type, project_id, group_id, document_id, actor_user_id,
             from_state, to_state, metadata, created_at)
        VALUES (?, 'flowgate', 'flowgate.default.0045', ?, 'u1', NULL, NULL, NULL, ?)
        """,
        [
            ("doc_created", doc_id, "2026-06-12T00:01:00Z"),
            ("doc_edited", doc_id, "2026-06-12T00:02:00Z"),
            ("doc_created", doc_id, "2026-06-12T00:03:00Z"),
        ],
    )
    store.conn.commit()
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def test_feed_all_unread_when_never_seen(feed_store):
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    assert result["ok"] is True
    assert result["last_seen_at"] is None
    assert result["unread_count"] == 3
    activities = result["recent_activities"]
    assert activities["total"] == 3
    assert activities["items"][0]["occurred_at"] == "2026-06-12T00:03:00.000Z"  # newest first


def test_feed_unread_counts_only_items_after_watermark(feed_store):
    result = dashboard_service.get_notification_feed(
        "flowgate", "2026-06-12T00:02:00Z", 50
    )
    # Strictly newer than the watermark → only the 00:03 event is unread.
    assert result["unread_count"] == 1
    assert result["last_seen_at"] == "2026-06-12T00:02:00.000Z"
    assert result["recent_activities"]["total"] == 3  # full history still returned


def test_feed_zero_unread_when_caught_up(feed_store):
    result = dashboard_service.get_notification_feed(
        "flowgate", "2026-06-12T23:59:00Z", 50
    )
    assert result["unread_count"] == 0


def test_feed_unread_counts_full_history_not_just_page(feed_store):
    # The unread badge must reflect ALL unread inflow, even when the returned page is truncated.
    result = dashboard_service.get_notification_feed("flowgate", None, 1)
    assert result["unread_count"] == 3
    assert len(result["recent_activities"]["items"]) == 1
    assert result["recent_activities"]["has_more"] is True
