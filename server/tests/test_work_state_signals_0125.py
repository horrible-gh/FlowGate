"""Work-state signals + dashboard state-board aggregation (R0001 group 0125, NR0003 권고 1~4).

Covers the four NR0003 follow-up recommendations implemented as this work order's deliverable:
  1. New explicit backend signals work_started / continuous_work_ended exist.
  4. They are NEVER part of the notification-feed whitelist (no 0118 noise regression).
  2. The active-workflow stage badge can report a 'done' state.
  2/3. get_work_state_summary aggregates 작업중·작업완료·복사됨(통합)·연속작업종료 as scannable counts.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from modules.flow_gate.services import dashboard_service
from modules.flow_gate.workflow import event_logger


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
def store(monkeypatch):
    store = _Store()
    store.conn.executescript(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL
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
        CREATE TABLE document_mention_copies (
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            mention_kind TEXT NOT NULL,
            copied_at TEXT NOT NULL,
            PRIMARY KEY (user_id, doc_id)
        );

        INSERT INTO groups VALUES ('flowgate.default.0125', 'flowgate', 'State board', NULL);
        INSERT INTO users VALUES ('u1', 'developer');
        INSERT INTO documents
            (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES
            ('flowgate.default.0125.0001-R', 'flowgate', 'flowgate.default.0125',
             'R', 'Requirement', 'wf_in_progress', '2026-06-24T00:00:00Z'),
            ('flowgate.default.0125.0004-T', 'flowgate', 'flowgate.default.0125',
             'T', 'Work order', 'approved', '2026-06-24T00:01:00Z'),
            ('flowgate.default.0125.0010-R', 'flowgate', 'flowgate.default.0125',
             'R', 'Finished requirement', 'wf_done', '2026-06-24T00:02:00Z'),
            ('flowgate.other.0001-R', 'other', 'g.other',
             'R', 'Other project', 'wf_in_progress', '2026-06-24T00:03:00Z');
        INSERT INTO workflow_sequences (doc_id, updated_at)
        VALUES ('flowgate.default.0125.0001-R', '2026-06-24T00:01:00Z');
        INSERT INTO workflow_sequence_items
            (sequence_id, type, sort_order, result_doc_id, updated_at)
        VALUES
            (1, 'T', 1, 'flowgate.default.0125.0004-T', '2026-06-24T00:01:00Z');
        """
    )
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def _doc_pk(store: _Store, doc_id: str) -> int:
    return store._fetch_one("SELECT id FROM documents WHERE doc_id = ?", [doc_id])["id"]


def _add_event(store, event_type, doc_pk, when, metadata=None):
    store.conn.execute(
        "INSERT INTO workflow_events "
        "(event_type, project_id, group_id, document_id, actor_user_id, created_at, metadata) "
        "VALUES (?, 'flowgate', 'flowgate.default.0125', ?, 'u1', ?, ?)",
        [event_type, doc_pk, when, metadata],
    )


# ── NR0003 권고 1 & 4: new signals exist and stay OFF the notification whitelist ──────

def test_new_signal_constants_defined():
    assert event_logger.EVT_WORK_STARTED == "work_started"
    assert event_logger.EVT_CONTINUOUS_WORK_ENDED == "continuous_work_ended"


def test_new_signals_excluded_from_notification_whitelist():
    # NR0003 권고 4: state signals must never leak into the past-tense feed (0118 noise regression).
    assert event_logger.EVT_WORK_STARTED not in dashboard_service._NOTIFICATION_EVENT_TYPES
    assert (
        event_logger.EVT_CONTINUOUS_WORK_ENDED
        not in dashboard_service._NOTIFICATION_EVENT_TYPES
    )
    # ...and they are not even in the broader recent-activity stream.
    assert event_logger.EVT_WORK_STARTED not in dashboard_service._ACTIVITY_EVENT_TYPES
    assert (
        event_logger.EVT_CONTINUOUS_WORK_ENDED
        not in dashboard_service._ACTIVITY_EVENT_TYPES
    )


def test_signals_do_not_appear_in_notification_feed(store):
    r_pk = _doc_pk(store, "flowgate.default.0125.0001-R")
    _add_event(store, "work_started", r_pk, "2026-06-24T01:00:00Z")
    _add_event(store, "continuous_work_ended", r_pk, "2026-06-24T01:05:00Z")

    feed = dashboard_service.get_notification_feed("flowgate", None, 50)
    event_types = {item.get("event_type") for item in feed["recent_activities"]["items"]}
    assert "work_started" not in event_types
    assert "continuous_work_ended" not in event_types


# ── NR0003 권고 2 & 3: state-board aggregation counts ────────────────────────────────

def test_work_state_summary_counts_status(store):
    summary = dashboard_service.get_work_state_summary("flowgate")
    # Only the two flowgate-project docs in those states; the 'other' project is excluded.
    assert summary["in_progress"] == 1
    assert summary["done"] == 1


def test_work_state_summary_unifies_copied_sources(store):
    # NR0003 권고 3: a doc copied via the per-user state table AND a doc copied via the
    # prompt_copied event both count, deduped to distinct documents.
    r_pk = _doc_pk(store, "flowgate.default.0125.0001-R")
    t_pk = _doc_pk(store, "flowgate.default.0125.0004-T")

    # Doc R: recorded BOTH ways -> must count once (dedup).
    store.conn.execute(
        "INSERT INTO document_mention_copies VALUES ('u1', ?, 'edit', ?)",
        ["flowgate.default.0125.0001-R", "2026-06-24T02:00:00Z"],
    )
    _add_event(store, "prompt_copied", r_pk, "2026-06-24T02:01:00Z")
    # Doc T: recorded only via the event path.
    _add_event(store, "prompt_copied", t_pk, "2026-06-24T02:02:00Z")

    summary = dashboard_service.get_work_state_summary("flowgate")
    assert summary["copied"] == 2


def test_work_state_summary_counts_continuous_ended(store):
    r_pk = _doc_pk(store, "flowgate.default.0125.0001-R")
    _add_event(store, "continuous_work_ended", r_pk, "2026-06-24T03:00:00Z")
    # A second event on the same doc must not double-count (DISTINCT document).
    _add_event(store, "continuous_work_ended", r_pk, "2026-06-24T03:01:00Z")

    summary = dashboard_service.get_work_state_summary("flowgate")
    assert summary["continuous_ended"] == 1


def test_work_state_summary_resilient_without_optional_tables(monkeypatch):
    # When document_mention_copies is absent (lighter deployment), 'copied' degrades to 0
    # instead of failing the whole summary.
    bare = _Store()
    bare.conn.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, project_id TEXT,
            group_id TEXT, type_code TEXT, title TEXT, doc_review_status TEXT, updated_at TEXT
        );
        CREATE TABLE workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, project_id TEXT,
            group_id TEXT, document_id INTEGER, actor_user_id TEXT, created_at TEXT, metadata TEXT
        );
        INSERT INTO documents (doc_id, project_id, doc_review_status, type_code, title, updated_at)
        VALUES ('d1', 'flowgate', 'wf_in_progress', 'R', 't', 'now');
        """
    )
    monkeypatch.setattr(dashboard_service, "get_store", lambda: bare)
    summary = dashboard_service.get_work_state_summary("flowgate")
    assert summary["in_progress"] == 1
    assert summary["copied"] == 0
    assert summary["continuous_ended"] == 0


# ── NR0003 권고 2: active-workflow stage reports 'done' when the head is approved ─────

def test_active_workflow_stage_done_when_head_approved(store):
    # A head step whose own document is finished (wf_done) surfaces as 'done'. (An 'approved'
    # non-M head is excluded by eligible_heads because the workflow advances past it, so wf_done
    # is the head state that legitimately reaches the stage builder as a completed step.)
    store.conn.execute(
        "UPDATE documents SET doc_review_status = 'wf_done' "
        "WHERE doc_id = 'flowgate.default.0125.0004-T'"
    )
    workflows = dashboard_service.list_active_workflows("flowgate", 10)
    item = next(
        w for w in workflows["items"]
        if w["requirement"]["doc_id"] == "flowgate.default.0125.0001-R"
    )
    assert item["stage"]["state"] == "done"


def test_active_workflow_stage_in_progress_when_head_unapproved(store):
    store.conn.execute(
        "UPDATE documents SET doc_review_status = 'pending_review' "
        "WHERE doc_id = 'flowgate.default.0125.0004-T'"
    )
    workflows = dashboard_service.list_active_workflows("flowgate", 10)
    item = next(
        w for w in workflows["items"]
        if w["requirement"]["doc_id"] == "flowgate.default.0125.0001-R"
    )
    assert item["stage"]["state"] == "in_progress"


def test_dashboard_summary_includes_work_states(store):
    summary = dashboard_service.get_dashboard_summary("flowgate", 10, 10)
    assert "work_states" in summary
    assert summary["work_states"]["in_progress"] == 1
    assert summary["work_states"]["done"] == 1
