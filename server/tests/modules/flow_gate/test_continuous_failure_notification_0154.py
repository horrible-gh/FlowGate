"""R0001 group 0154 / NR0004 Gap A — surface a failed unmanned-chain test_run as a distinct notification.

NR0004 found the "테스트레포트에서 멈춤" R0001 reported was really two stops. The SUCCESS path already
self-continues (all-green auto-assembles + auto-approves the TSR) and the terminal completion surfaces as
`continuous_work_completed` (group 0135). The FAILURE path, by contrast, assembled no TSR and stopped with
NO persistent signal at all — only a transient SSE `test_run_finished` broadcast — so the unmanned chain
went silent and nobody knew until the run record was opened by hand (NR0004 §2.4).

This promotes ONE new terminal event, `continuous_work_failed`, to the 🔔 feed as the failure counterpart
of `continuous_work_ended`, reusing the same once-per-terminal-event discipline so it does not revive the
0118 per-transition noise.

These tests lock in:
  1. `continuous_work_failed` surfaces on the feed as a distinct `continuous_work_failed` activity,
     pointing at the TS document that failed so the user lands on it.
  2. It fires alongside — not instead of — normal inflow (`doc_created` still surfaces).
  3. The 0118 noise invariant still holds: `state_changed` / `work_started` never reach the feed.
  4. The event is a notification-feed-only surface: the dashboard recent-activity card excludes it.
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
def failed_chain_store(monkeypatch):
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
        CREATE TABLE document_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
            revision_no INTEGER, reviewer_id TEXT, verdict TEXT, findings TEXT,
            comment TEXT, reviewed_at TEXT, created_at TEXT, updated_at TEXT
        );
        INSERT INTO users VALUES ('u1', 'developer');
        INSERT INTO projects VALUES ('flowgate', 'FlowGate', 1);
        INSERT INTO groups VALUES ('flowgate.default.0154', 'flowgate', 'Chain stop investigation', NULL);
        -- The TS document whose unmanned-chain test_run went RED.
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0154.0006-TS', 'flowgate', 'flowgate.default.0154',
                'TS', 'Test spec', 'open', '2026-07-05T11:30:00Z');
        """
    )
    ts_pk = store._fetch_one(
        "SELECT id FROM documents WHERE doc_id = 'flowgate.default.0154.0006-TS'"
    )["id"]
    store.conn.executemany(
        """
        INSERT INTO workflow_events
            (event_type, project_id, group_id, document_id, actor_user_id,
             from_state, to_state, metadata, created_at)
        VALUES (?, 'flowgate', ?, ?, 'u1', ?, ?, ?, ?)
        """,
        [
            # chain start — state-board only, must NOT reach the feed (0118 invariant)
            ("work_started", None, None, "", "wf_in_progress", '{"to_state": "wf_in_progress"}',
             "2026-07-05T11:20:00Z"),
            # TS document creation — normal inflow, still surfaces
            ("doc_created", None, ts_pk, "draft", None, '{"doc_id": "flowgate.default.0154.0006-TS"}',
             "2026-07-05T11:25:00Z"),
            # per-transition noise — must NOT reach the feed
            ("state_changed", None, ts_pk, "review:", "review:pending_review",
             '{"from": "review:", "to": "review:pending_review", "action": "review_submit"}',
             "2026-07-05T11:29:00Z"),
            # THE terminal chain-FAILURE signal — must surface as continuous_work_failed
            ("continuous_work_failed", None, ts_pk, None, None,
             '{"doc_id": "flowgate.default.0154.0006-TS", "run_id": "trun_20260705_000002", '
             '"case_passed": 1, "case_failed": 4, "target_seq": 7}',
             "2026-07-05T11:32:50Z"),
        ],
    )
    store.conn.commit()
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def test_constants_and_whitelist():
    # The failure signal is a real event type, promoted to the feed but kept off the dashboard card.
    assert event_logger.EVT_CONTINUOUS_WORK_FAILED == "continuous_work_failed"
    assert "continuous_work_failed" in dashboard_service._NOTIFICATION_EVENT_TYPES
    assert "continuous_work_failed" not in dashboard_service._ACTIVITY_EVENT_TYPES


def test_failure_surfaces_distinctly(failed_chain_store):
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    items = result["recent_activities"]["items"]
    failed = [i for i in items if i["activity_type"] == "continuous_work_failed"]
    assert len(failed) == 1, "the once-per-run failure stop must appear exactly once"
    row = failed[0]
    # It points at the TS document that failed, so the user lands there to fix it.
    assert row["document"]["doc_id"] == "flowgate.default.0154.0006-TS"
    assert row["navigation"]["kind"] == "document"
    assert row["navigation"]["doc_id"] == "flowgate.default.0154.0006-TS"
    # Newest-first: the failure is the freshest event.
    assert items[0]["activity_type"] == "continuous_work_failed"


def test_normal_inflow_still_present(failed_chain_store):
    # The failure row is ADDED, not a replacement: the TS creation still surfaces.
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    created = [i for i in result["recent_activities"]["items"]
              if i["activity_type"] == "document_created"]
    created_ids = {i["document"]["doc_id"] for i in created}
    assert created_ids == {"flowgate.default.0154.0006-TS"}


def test_noise_invariant_preserved(failed_chain_store):
    # 0118 / group 0125 NR0003 권고 4: per-transition state_changed and present-tense work_started must
    # NEVER reach the feed even though the terminal failure now does.
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    types = {i["activity_type"] for i in result["recent_activities"]["items"]}
    assert "document_state_changed" not in types
    assert "workflow_state_changed" not in types
    assert types == {"document_created", "continuous_work_failed"}


def test_dashboard_card_excludes_failure_signal(failed_chain_store):
    # The recent-activity card uses _ACTIVITY_EVENT_TYPES (no continuous_work_failed), so the failure is
    # a notification-feed-only surface — the two stay decoupled (0118), mirroring continuous_work_ended.
    result = dashboard_service.list_recent_activities("flowgate", 50)
    types = {i["activity_type"] for i in result["items"]}
    assert "continuous_work_failed" not in types
