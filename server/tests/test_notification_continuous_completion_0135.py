"""R0001 group 0135 / N0008 — surface the terminal "연속작업 완료" signal as a distinct notification.

N0008 approved 시안 3 (live feed) and asked that a continuous (unmanned) run's FINAL completion read
differently from its intermediate per-step inflow, while single-mode work stays as-is. NR0009 found the
terminal signal (`continuous_work_ended`, group 0125) was already recorded but deliberately excluded from
the notification feed. This promotes ONLY that single terminal event to the feed as
`continuous_work_completed`, without reintroducing the 0118 per-transition noise.

These tests lock in:
  1. `continuous_work_ended` surfaces on the 🔔 feed as a distinct `continuous_work_completed` activity,
     pointing at the terminal document of the finished chain.
  2. A continuous run's intermediate `doc_created` rows still surface (single/intermediate inflow
     unchanged) — the terminal row is ADDED, not a replacement.
  3. The 0118 noise invariant holds: `state_changed` / `work_started` never reach the feed.
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
def chain_store(monkeypatch):
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
        INSERT INTO groups VALUES ('flowgate.default.0135', 'flowgate', 'Triage cockpit', NULL);
        -- Two documents from one unmanned chain: an intermediate step and the terminal step.
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0135.0009-NR', 'flowgate', 'flowgate.default.0135',
                'NR', 'Investigation', 'open', '2026-07-02T00:03:00Z');
        INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at)
        VALUES ('flowgate.default.0135.0010-TR', 'flowgate', 'flowgate.default.0135',
                'TR', 'Work report', 'open', '2026-07-02T00:05:00Z');
        """
    )
    mid_pk = store._fetch_one(
        "SELECT id FROM documents WHERE doc_id = 'flowgate.default.0135.0009-NR'"
    )["id"]
    end_pk = store._fetch_one(
        "SELECT id FROM documents WHERE doc_id = 'flowgate.default.0135.0010-TR'"
    )["id"]
    store.conn.executemany(
        """
        INSERT INTO workflow_events
            (event_type, project_id, group_id, document_id, actor_user_id,
             from_state, to_state, metadata, created_at)
        VALUES (?, 'flowgate', 'flowgate.default.0135', ?, 'u1', ?, ?, ?, ?)
        """,
        [
            # chain start — state-board only, must NOT reach the feed
            ("work_started", None, "", "wf_in_progress", '{"to_state": "wf_in_progress"}',
             "2026-07-02T00:00:00Z"),
            # intermediate step creation — normal inflow, still surfaces
            ("doc_created", mid_pk, "draft", None, '{"doc_id": "flowgate.default.0135.0009-NR"}',
             "2026-07-02T00:03:00Z"),
            # per-transition noise from the auto-approve — must NOT reach the feed (0118 invariant)
            ("state_changed", mid_pk, "review:", "review:pending_review",
             '{"from": "review:", "to": "review:pending_review", "action": "review_submit"}',
             "2026-07-02T00:04:00Z"),
            # terminal step creation — normal inflow
            ("doc_created", end_pk, "draft", None, '{"doc_id": "flowgate.default.0135.0010-TR"}',
             "2026-07-02T00:05:00Z"),
            # THE terminal chain-completion signal — must surface as continuous_work_completed
            ("continuous_work_ended", end_pk, None, None,
             '{"doc_id": "flowgate.default.0135.0010-TR", "target_seq": 10}',
             "2026-07-02T00:06:00Z"),
        ],
    )
    # R0001 group 0135 / N0008 (시안 3): an AI review whose verdict is `issues` on the terminal doc —
    # the feed must surface it as a 🔴 trust signal ("완료로 떴지만 사실 확인 필요").
    store.conn.execute(
        "INSERT INTO document_reviews (doc_id, revision_no, reviewer_id, verdict, findings, "
        "comment, reviewed_at, created_at, updated_at) VALUES "
        "('flowgate.default.0135.0010-TR', 1, 'ai', 'issues', "
        "'[{\"locus\": \"x\", \"note\": \"a\"}, {\"locus\": \"y\", \"note\": \"b\"}]', "
        "NULL, '2026-07-02T00:05:30Z', '2026-07-02T00:05:30Z', '2026-07-02T00:05:30Z')"
    )
    store.conn.commit()
    monkeypatch.setattr(dashboard_service, "get_store", lambda: store)
    return store


def test_terminal_completion_surfaces_distinctly(chain_store):
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    items = result["recent_activities"]["items"]
    terminal = [i for i in items if i["activity_type"] == "continuous_work_completed"]
    assert len(terminal) == 1, "the once-per-run terminal completion must appear exactly once"
    row = terminal[0]
    # It points at the terminal document of the finished chain, so the user lands there.
    assert row["document"]["doc_id"] == "flowgate.default.0135.0010-TR"
    assert row["navigation"]["kind"] == "document"
    assert row["navigation"]["doc_id"] == "flowgate.default.0135.0010-TR"
    # Newest-first: the completion is the freshest event.
    assert items[0]["activity_type"] == "continuous_work_completed"


def test_intermediate_inflow_still_present(chain_store):
    # The terminal row is ADDED, not a replacement: both step creations still surface.
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    created = [i for i in result["recent_activities"]["items"]
              if i["activity_type"] == "document_created"]
    created_ids = {i["document"]["doc_id"] for i in created}
    assert created_ids == {
        "flowgate.default.0135.0009-NR",
        "flowgate.default.0135.0010-TR",
    }


def test_noise_invariant_preserved(chain_store):
    # 0118 / group 0125 NR0003 권고 4: per-transition state_changed and present-tense work_started
    # must NEVER reach the feed even though the terminal completion now does.
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    types = {i["activity_type"] for i in result["recent_activities"]["items"]}
    assert "document_state_changed" not in types
    assert "workflow_state_changed" not in types
    # work_started has no feed activity mapping at all — it is dropped by the whitelist.
    assert types == {"document_created", "continuous_work_completed"}


def test_dashboard_card_excludes_terminal_signal(chain_store):
    # The recent-activity card uses _ACTIVITY_EVENT_TYPES (no continuous_work_ended), so the terminal
    # completion is a notification-feed-only surface — the two stay decoupled (0118).
    result = dashboard_service.list_recent_activities("flowgate", 50)
    types = {i["activity_type"] for i in result["items"]}
    assert "continuous_work_completed" not in types


def test_feed_rows_carry_ai_review_signals(chain_store):
    # 시안 3 trust colours: each row's document DTO carries the latest AI verdict + finding count so the
    # UI can paint 🟢🟡🔴 and the "완료로 떴지만 issues" warning without opening the document.
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    by_doc = {
        i["document"]["doc_id"]: i["document"]["review"]
        for i in result["recent_activities"]["items"]
        if i.get("document")
    }
    # The terminal doc was reviewed `issues` with 2 findings → surfaced verbatim for the red row.
    terminal = by_doc["flowgate.default.0135.0010-TR"]
    assert terminal["verdict"] == "issues"
    assert terminal["finding_count"] == 2
    assert terminal["status"] == "open"
    # The unreviewed intermediate doc carries a null verdict → the row renders neutral (no false 🟢).
    assert by_doc["flowgate.default.0135.0009-NR"]["verdict"] is None
    assert by_doc["flowgate.default.0135.0009-NR"]["finding_count"] == 0


def test_final_approved_group_suppresses_terminal_issues_row(chain_store):
    # Once the owning R workflow is wf_done, the notification center must stop surfacing the group's
    # stale unread/attention rows, including terminal docs whose latest AI verdict is `issues`.
    chain_store.conn.execute(
        "INSERT INTO documents (doc_id, project_id, group_id, type_code, title, doc_review_status, updated_at) "
        "VALUES ('flowgate.default.0135.0001-R', 'flowgate', 'flowgate.default.0135', "
        "'R', 'Requirement', 'wf_done', '2026-07-02T00:07:00Z')"
    )
    chain_store.conn.commit()

    result = dashboard_service.get_notification_feed("flowgate", None, 50)

    assert result["unread_count"] == 0
    assert result["recent_activities"]["items"] == []


def test_review_enrichment_degrades_without_reviews_table(chain_store):
    # Defensive: a minimal/legacy store lacking document_reviews must not crash the feed — rows just
    # render neutral (verdict null). Drop the table and confirm the feed still assembles.
    chain_store.conn.execute("DROP TABLE document_reviews")
    chain_store.conn.commit()
    result = dashboard_service.get_notification_feed("flowgate", None, 50)
    reviews = [
        i["document"]["review"]
        for i in result["recent_activities"]["items"]
        if i.get("document")
    ]
    assert reviews and all(r["verdict"] is None for r in reviews)
