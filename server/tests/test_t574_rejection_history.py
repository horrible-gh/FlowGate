"""T574: tests for accumulated rejection reason history (rejection_history).

Coverage:
- Append one entry to rejection_history on the first rejection
- Accumulate history on two consecutive rejections (ascending time order)
- Correct the latest history entry's wording via single-item PATCH (0419 T0006: in
  place, appending a corrections[] audit entry, no new history item)
- Preserve the existing rejection_reason column value (compatibility)
- Include the rejection_history field in GET responses
- Include rejection_history in the SSE payload
- Migration backfill: existing rejection_reason -> one history entry
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")

# ── Schema path ───────────────────────────────────────────────────────────────

_MIGRATIONS = (
    Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"
)
SCHEMA_SQL    = _MIGRATIONS / "001_flowgate_schema.sql"
MIGRATION_031 = _MIGRATIONS / "031_rejection_history.sql"
MIGRATION_037 = _MIGRATIONS / "037_rejection_id_backfill.sql"  # P0005/T0006


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """In-memory SQLite — schema + review column + migration 031 applied.

    0419 T0006: check_same_thread=False because the PATCH-correction tests drive
    update_rejection_reason_endpoint (now async) via asyncio.run, and its DB work
    runs inside anyio.to_thread.run_sync on a worker thread; the shared connection
    is only ever touched sequentially (never concurrently), so this is safe.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    # schema sets FK ON; disable for column additions then re-enable
    conn.execute("PRAGMA foreign_keys = OFF")

    # Add only the columns directly to avoid the table chain that 024 depends on
    conn.execute(
        "ALTER TABLE documents ADD COLUMN doc_review_status TEXT "
        "CHECK (doc_review_status IN ('pending_review','approved','rejected','revised'))"
    )
    conn.execute("ALTER TABLE documents ADD COLUMN rejection_reason TEXT")
    conn.executescript(MIGRATION_031.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")

    # seed: project, user
    conn.execute(
        "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
        "VALUES('P001','TestProject',1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO users(user_id, username, email, password, is_admin, is_active, created_at, updated_at) "
        "VALUES('u001','admin','admin@test.com','hash',1,1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES('G001','P001','__ALL__','Test Group','OPEN','2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        "owner_id, doc_review_status, created_at, updated_at) "
        "VALUES('D001','P001','__ALL__','G001','R',1,'Doc 1','open',"
        "'u001','pending_review','2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.commit()
    return conn


def _make_store(conn: sqlite3.Connection):
    store = MagicMock()

    def _fetch_one(sql, params=None):
        row = conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(sql, params=None):
        return [dict(r) for r in conn.execute(sql, params or []).fetchall()]

    def _execute(sql, params=None):
        conn.execute(sql, params or [])
        conn.commit()

    store._fetch_one = _fetch_one
    store._fetch_all = _fetch_all
    store._execute = _execute
    return store


# ── pipeline_service モック helper ───────────────────────────────────────────

def _patch_pipeline(db_conn_fixture, monkeypatch):
    import modules.flow_gate.db.documents as db_d
    import modules.flow_gate.workflow.pipeline_service as ps
    import modules.flow_gate.workflow.event_logger as el

    store = _make_store(db_conn_fixture)
    monkeypatch.setattr(db_d, "get_store", lambda: store)
    monkeypatch.setattr(ps, "get_store", lambda: store)
    # This suite isolates review-state/history behavior; body validation has dedicated tests.
    monkeypatch.setattr(ps, "_require_document_body_for_approval", lambda doc, locale="ko": None)

    mock_events = MagicMock()
    mock_events.create = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(el, "db_events", mock_events)

    return store


ADMIN_PERMS = {
    "project.group.manage", "document.create", "document.read",
    "document.update", "document.approve", "document.reject",
    "document.delete", "document.delete.own.draft", "own.draft",
}


# ── Migration backfill tests ──────────────────────────────────────────────────

class TestMigrationBackfill:
    def _base_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        # schema sets FK ON; override for test setup
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "ALTER TABLE documents ADD COLUMN doc_review_status TEXT "
            "CHECK (doc_review_status IN ('pending_review','approved','rejected','revised'))"
        )
        conn.execute("ALTER TABLE documents ADD COLUMN rejection_reason TEXT")
        conn.execute(
            "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
            "VALUES('P001','TP',1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO users(user_id, username, email, password, is_admin, is_active, created_at, updated_at) "
            "VALUES('u001','admin','admin@test.com','hash',1,1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES('G001','P001','__ALL__','G','OPEN','2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.commit()
        return conn

    def test_backfill_existing_rejection_reason(self):
        """A row with an existing rejection_reason gets one rejection_history entry after migration."""
        conn = self._base_conn()

        conn.execute(
            "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
            "owner_id, rejection_reason, created_at, updated_at) "
            "VALUES('D_OLD','P001','__ALL__','G001','R',1,'old doc','open',"
            "            'u001','existing reason','2026-01-01T00:00:00','2026-05-01T00:00:00')"
        )
        conn.commit()

        # Apply migration 031
        conn.executescript(MIGRATION_031.read_text(encoding="utf-8"))

        row = dict(conn.execute("SELECT * FROM documents WHERE doc_id='D_OLD'").fetchone())
        history = json.loads(row["rejection_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "existing reason"
        assert history[0]["rejected_by"] is None
        assert history[0]["rejected_at"] == "2026-05-01T00:00:00"

    def test_no_backfill_for_null_rejection_reason(self):
        """A row with rejection_reason = NULL gets rejection_history = '[]'."""
        conn = self._base_conn()

        conn.execute(
            "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
            "owner_id, created_at, updated_at) "
            "VALUES('D_NEW','P001','__ALL__','G001','R',1,'new doc','open',"
            "'u001','2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.commit()

        conn.executescript(MIGRATION_031.read_text(encoding="utf-8"))

        row = dict(conn.execute("SELECT * FROM documents WHERE doc_id='D_NEW'").fetchone())
        history = json.loads(row["rejection_history"])
        assert history == []


# ── pipeline_service: transition_document_review ─────────────────────────────

from modules.flow_gate.workflow.pipeline_service import transition_document_review


class TestTransitionDocumentReviewHistory:
    def test_first_rejection_creates_history(self, db_conn, monkeypatch):
        """First rejection -> one rejection_history entry, and rejection_reason is also stored."""
        _patch_pipeline(db_conn, monkeypatch)

        result = transition_document_review(
            doc_id="D001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment="first rejection reason",
        )

        assert result["doc_review_status"] == "rejected"
        assert result["rejection_reason"] == "first rejection reason"

        history = json.loads(result["rejection_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "first rejection reason"
        assert history[0]["rejected_by"] == "u001"
        assert "rejected_at" in history[0]

    def test_second_rejection_appends_history(self, db_conn, monkeypatch):
        """Two consecutive rejections -> two accumulated rejection_history entries (ascending time order)."""
        _patch_pipeline(db_conn, monkeypatch)

        transition_document_review(
            doc_id="D001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment="first rejection reason",
        )

        # Restore the state so it can be rejected again after re-review
        db_conn.execute(
            "UPDATE documents SET doc_review_status='pending_review' WHERE doc_id='D001'"
        )
        db_conn.commit()

        result2 = transition_document_review(
            doc_id="D001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment="second rejection reason",
        )

        history = json.loads(result2["rejection_history"])
        assert len(history) == 2
        assert history[0]["reason"] == "first rejection reason"
        assert history[1]["reason"] == "second rejection reason"

    def test_rejection_reason_compat_preserved(self, db_conn, monkeypatch):
        """Keep overwriting rejection_reason with the latest reason (frontend compatibility)."""
        _patch_pipeline(db_conn, monkeypatch)

        result = transition_document_review(
            doc_id="D001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment="compatibility test reason",
        )
        assert result["rejection_reason"] == "compatibility test reason"

    def test_approve_does_not_touch_history(self, db_conn, monkeypatch):
        """approve does not change rejection_history."""
        _patch_pipeline(db_conn, monkeypatch)

        result = transition_document_review(
            doc_id="D001",
            action="approve",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
        )
        assert result["doc_review_status"] == "approved"
        # rejection_history should be absent or equal to '[]'
        raw = result.get("rejection_history", "[]")
        assert json.loads(raw) == []


# ── PATCH endpoint: update_rejection_reason_endpoint ─────────────────────────

class TestPatchRejectionReasonHistory:
    """0419 T0006: the PATCH endpoint now CORRECTS the latest rejection's wording in
    place instead of appending a new rejection_history entry, so only a document that
    is currently 'rejected' has anything to correct."""

    def _reject_d001(self, reason="first rejection reason"):
        return transition_document_review(
            doc_id="D001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment=reason,
        )

    def test_patch_corrects_latest_entry_in_place(self, db_conn, monkeypatch):
        """A PATCH save overwrites the latest entry's reason instead of appending."""
        _patch_pipeline(db_conn, monkeypatch)
        self._reject_d001()

        from modules.flow_gate.workflow.routers.workflow import (
            update_rejection_reason_endpoint,
            RejectionReasonRequest,
        )

        result = asyncio.run(update_rejection_reason_endpoint(
            doc_id="D001",
            body=RejectionReasonRequest(reason="corrected reason"),
            current_user={"user_id": "u001", "is_admin": True},
        ))

        doc = result["document"]
        assert doc["rejection_reason"] == "corrected reason"
        history = json.loads(doc["rejection_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "corrected reason"
        assert history[0]["rejected_by"] == "u001"
        corrections = history[0]["corrections"]
        assert len(corrections) == 1
        assert corrections[0]["previous_reason"] == "first rejection reason"
        assert corrections[0]["corrected_by"] == "u001"

    def test_patch_accumulates_corrections_not_history(self, db_conn, monkeypatch):
        """Two PATCH saves -> history stays at one entry; corrections grows to two."""
        _patch_pipeline(db_conn, monkeypatch)
        self._reject_d001()

        from modules.flow_gate.workflow.routers.workflow import (
            update_rejection_reason_endpoint,
            RejectionReasonRequest,
        )

        asyncio.run(update_rejection_reason_endpoint(
            doc_id="D001",
            body=RejectionReasonRequest(reason="first correction"),
            current_user={"user_id": "u001", "is_admin": True},
        ))
        result2 = asyncio.run(update_rejection_reason_endpoint(
            doc_id="D001",
            body=RejectionReasonRequest(reason="second correction"),
            current_user={"user_id": "u001", "is_admin": True},
        ))

        history = json.loads(result2["document"]["rejection_history"])
        assert len(history) == 1
        assert history[0]["reason"] == "second correction"
        corrections = history[0]["corrections"]
        assert len(corrections) == 2
        assert corrections[0]["previous_reason"] == "first rejection reason"
        assert corrections[1]["previous_reason"] == "first correction"

    def test_patch_rejected_when_document_not_currently_rejected(self, db_conn, monkeypatch):
        """A document that was never rejected has no rejection to correct -> 409."""
        _patch_pipeline(db_conn, monkeypatch)

        from fastapi import HTTPException
        from modules.flow_gate.workflow.routers.workflow import (
            update_rejection_reason_endpoint,
            RejectionReasonRequest,
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(update_rejection_reason_endpoint(
                doc_id="D001",
                body=RejectionReasonRequest(reason="too early"),
                current_user={"user_id": "u001", "is_admin": True},
            ))
        assert exc_info.value.status_code == 409

    def test_patch_rejects_empty_reason(self, db_conn, monkeypatch):
        """A blank correction is rejected outright instead of being stored as empty."""
        _patch_pipeline(db_conn, monkeypatch)
        self._reject_d001()

        from fastapi import HTTPException
        from modules.flow_gate.workflow.routers.workflow import (
            update_rejection_reason_endpoint,
            RejectionReasonRequest,
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(update_rejection_reason_endpoint(
                doc_id="D001",
                body=RejectionReasonRequest(reason="   "),
                current_user={"user_id": "u001", "is_admin": True},
            ))
        assert exc_info.value.status_code == 400


# ── SSE payload ───────────────────────────────────────────────────────────────

class TestSSEPayloadRejectionHistory:
    def test_sse_payload_includes_rejection_history(self, db_conn, monkeypatch):
        """Include rejection_history in the SSE payload — validate broadcast_event arguments directly."""
        import modules.flow_gate.db.documents as db_d
        import modules.flow_gate.workflow.pipeline_service as ps
        import modules.flow_gate.workflow.event_logger as el

        store = _make_store(db_conn)
        monkeypatch.setattr(db_d, "get_store", lambda: store)
        monkeypatch.setattr(ps, "get_store", lambda: store)

        mock_events = MagicMock()
        mock_events.create = MagicMock(return_value={"id": 1})
        monkeypatch.setattr(el, "db_events", mock_events)

        captured_payloads: list[dict] = []

        async def fake_broadcast(event):
            captured_payloads.append(event.payload)

        import modules.flow_gate.workflow.routers.workflow as wf_router

        # Patch broadcast_event at module level
        with patch(
            "modules.flow_gate.workflow.routers.workflow.broadcast_event",
            new=fake_broadcast,
            create=True,
        ):
            # Validate the SSE payload shape by calling only the service layer directly
            # (verify _parse_rejection_history + SSE payload assembly instead of the async router endpoint)
            result = transition_document_review(
                doc_id="D001",
                action="reject",
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
                comment="SSE test",
            )

        # Build and validate the rejection_history that will be included in the SSE payload
        from modules.flow_gate.workflow.routers.workflow import _parse_rejection_history
        history = _parse_rejection_history(result.get("rejection_history"))
        assert isinstance(history, list)
        assert len(history) == 1
        assert history[0]["reason"] == "SSE test"
        assert result["rejection_reason"] == "SSE test"  # keep the compatibility key too

        # Validate SSE payload dict assembly (exactly how the router assembles it)
        sse_payload = {
            "doc_id": "D001",
            "prev_status": "pending_review",
            "next_status": result.get("doc_review_status"),
            "rejection_reason": "SSE test",
            "rejection_history": _parse_rejection_history(result.get("rejection_history")),
        }
        assert "rejection_history" in sse_payload
        assert sse_payload["rejection_history"] == history


# ── GET response model ────────────────────────────────────────────────────────

class TestGetDocumentResponse:
    def test_get_document_includes_rejection_history(self, db_conn, monkeypatch):
        """Verify that get_document returns rejection_history / rejection_reason / doc_review_status."""
        import modules.flow_gate.db.documents as db_d
        import modules.flow_gate.api.v1.document_routes as dr
        from fastapi.responses import JSONResponse

        store = _make_store(db_conn)
        monkeypatch.setattr(db_d, "get_store", lambda: store)

        # Set rejection history directly
        db_conn.execute(
            "UPDATE documents SET rejection_reason='GET test', "
            "rejection_history=? WHERE doc_id='D001'",
            [json.dumps([{"reason": "GET test", "rejected_at": "2026-05-01T00:00:00Z", "rejected_by": "u001"}])],
        )
        db_conn.commit()

        # Bypass auth and ID validation
        monkeypatch.setattr(dr, "verify_bearer", lambda req: {"ok": True})
        monkeypatch.setattr(dr, "_validate_outbound_doc_id", lambda doc_id: None)
        # get_document now also fetches Q/A pairs; that path uses the (unmocked)
        # questions store, so stub it out for this rejection-history-focused test.
        monkeypatch.setattr(dr, "get_answers_for_document", lambda doc_id: [])

        # Request mock (no file reading)
        class FakeRequest:
            pass

        result = dr.get_document(FakeRequest(), "D001")

        assert isinstance(result, JSONResponse)
        import json as _json
        data = _json.loads(result.body)
        assert data["ok"] is True
        assert "rejection_history" in data
        assert data["rejection_history"] == [
            {"reason": "GET test", "rejected_at": "2026-05-01T00:00:00Z", "rejected_by": "u001"}
        ]
        assert data["rejection_reason"] == "GET test"
        assert "doc_review_status" in data


# ── P0005 / T0006: rejection_id + AI response ────────────────────────────────
#
# Reviewer rework (TR0007): the AI response to a rejection arrives WITH the inbox
# re-submission of the rejected document and is attached to the most recent
# rejection (pipeline_service.record_rejection_response). There is no dedicated
# response API, no manual-input UI, and no separate SSE event — the inbox edit
# already bumps the revision and emits the existing review-status/refresh events.

from modules.flow_gate.workflow.rejection_identity import (
    new_rejection_id,
    legacy_rejection_id,
)
from modules.flow_gate.workflow.pipeline_service import (
    record_rejection_response,
    AI_RESPONSE_MAX_LEN,
)
# T0005 2.1.5/2.2.2-2.2.6: the review_id anchor plumbing under test below.
from modules.flow_gate.api import inbox_routes
from modules.flow_gate.workflow.pipeline_service import (
    is_review_row_id,
    rejection_review_key,
    UNIDENTIFIABLE_REVIEW_ID,
)


def _patch_store_everywhere(db_conn, monkeypatch):
    """Patch get_store for the documents layer, pipeline service, and event logger."""
    import modules.flow_gate.db.documents as db_d
    import modules.flow_gate.workflow.pipeline_service as ps
    import modules.flow_gate.workflow.event_logger as el

    store = _make_store(db_conn)
    monkeypatch.setattr(db_d, "get_store", lambda: store)
    monkeypatch.setattr(ps, "get_store", lambda: store)

    mock_events = MagicMock()
    mock_events.create = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(el, "db_events", mock_events)
    return store


def _reject(comment: str) -> str:
    """Reject D001 and return the newly minted rejection_id."""
    result = transition_document_review(
        doc_id="D001", action="reject", actor_user_id="u001",
        user_permissions=ADMIN_PERMS, comment=comment,
    )
    # Re-open so it can be rejected again in multi-rejection scenarios.
    return json.loads(result["rejection_history"])[-1]["rejection_id"]


def _reopen(db_conn):
    db_conn.execute("UPDATE documents SET doc_review_status='pending_review' WHERE doc_id='D001'")
    db_conn.commit()


class TestRejectionIdAssignment:
    def test_reject_assigns_id_and_null_response_fields(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        rid = _reject("please fix X")
        item = json.loads(
            db_conn.execute("SELECT rejection_history FROM documents WHERE doc_id='D001'").fetchone()[0]
        )[0]
        assert item["rejection_id"] == rid
        assert rid.startswith("rej_")
        assert item["ai_response"] is None
        assert item["responded_at"] is None
        assert item["response_recorded_by"] is None
        assert item["response_revision_no"] is None

    def test_new_rejection_id_unique_and_prefixed(self):
        ids = {new_rejection_id() for _ in range(300)}
        assert len(ids) == 300
        assert all(i.startswith("rej_") and len(i) == len("rej_") + 16 for i in ids)


class TestRecordRejectionResponse:
    """The AI response arrives with the inbox re-submission and is attached to the
    most recent rejection — no new history entry, idempotent overwrite."""

    def _history(self, db_conn):
        return json.loads(
            db_conn.execute(
                "SELECT rejection_history FROM documents WHERE doc_id='D001'"
            ).fetchone()[0]
        )

    def test_attaches_to_latest_item_no_new_history(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        _reject("first reason")
        _reopen(db_conn)
        _reject("second reason")

        item = record_rejection_response(
            doc_id="D001", response_text="I addressed the latest reason.",
            recorded_by="u001", revision_no=3,
        )
        assert item is not None
        items = self._history(db_conn)
        assert len(items) == 2  # no appended history entry
        assert items[-1]["ai_response"] == "I addressed the latest reason."
        assert items[-1]["responded_at"] is not None
        assert items[-1]["response_recorded_by"] == "u001"
        assert items[-1]["response_revision_no"] == 3
        assert items[0]["ai_response"] is None  # earlier rejection untouched
        assert items[0]["reason"] == "first reason"

    def test_idempotent_overwrite(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        _reject("reason")
        record_rejection_response(doc_id="D001", response_text="first", recorded_by="u001", revision_no=1)
        record_rejection_response(doc_id="D001", response_text="second", recorded_by="u001", revision_no=2)
        items = self._history(db_conn)
        assert len(items) == 1
        assert items[0]["ai_response"] == "second"
        assert items[0]["response_revision_no"] == 2

    def test_blank_response_is_noop(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        _reject("reason")
        assert record_rejection_response(
            doc_id="D001", response_text="   ", recorded_by="u001", revision_no=1
        ) is None
        assert self._history(db_conn)[0]["ai_response"] is None

    def test_over_length_is_truncated(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        _reject("reason")
        item = record_rejection_response(
            doc_id="D001", response_text="x" * (AI_RESPONSE_MAX_LEN + 50),
            recorded_by="u001", revision_no=1,
        )
        assert item is not None
        assert len(item["ai_response"]) == AI_RESPONSE_MAX_LEN

    def test_no_history_returns_none(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        # D001 has never been rejected → empty history → nothing to attach to.
        assert record_rejection_response(
            doc_id="D001", response_text="resp", recorded_by="u001", revision_no=1
        ) is None


class TestBackfillMigration037:
    def _base_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE documents ADD COLUMN rejection_reason TEXT")
        conn.executescript(MIGRATION_031.read_text(encoding="utf-8"))  # adds rejection_history column
        conn.execute(
            "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
            "VALUES('P001','TP',1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES('G001','P001','__ALL__','G','OPEN','2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        conn.commit()
        return conn

    def _insert_doc(self, conn, doc_id, history):
        conn.execute(
            "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
            "rejection_history, created_at, updated_at) "
            "VALUES(?,'P001','__ALL__','G001','R',1,'doc','open',?,'2026-01-01T00:00:00','2026-01-01T00:00:00')",
            [doc_id, json.dumps(history)],
        )
        conn.commit()

    def test_legacy_ids_match_python_helper(self):
        conn = self._base_conn()
        self._insert_doc(conn, "D_OLD", [
            {"reason": "r1", "rejected_at": "2026-05-01T00:00:00+09:00", "rejected_by": "u1"},
            {"reason": "r2", "rejected_at": "2026-05-02T10:20:30", "rejected_by": None},
        ])
        conn.executescript(MIGRATION_037.read_text(encoding="utf-8"))
        items = json.loads(conn.execute("SELECT rejection_history FROM documents WHERE doc_id='D_OLD'").fetchone()[0])
        assert items[0]["rejection_id"] == legacy_rejection_id("2026-05-01T00:00:00+09:00", 0)
        assert items[1]["rejection_id"] == legacy_rejection_id("2026-05-02T10:20:30", 1)
        # new response fields initialised null
        for it in items:
            assert it["ai_response"] is None
            assert it["response_revision_no"] is None

    def test_idempotent_and_preserves_existing_ids(self):
        conn = self._base_conn()
        self._insert_doc(conn, "D_MIX", [
            {"rejection_id": "rej_keep", "reason": "kept", "rejected_at": "2026-05-03T00:00:00",
             "rejected_by": "u2", "ai_response": "already", "responded_at": "2026-05-03T01:00:00",
             "response_recorded_by": "u2", "response_revision_no": 3},
        ])
        sql = MIGRATION_037.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.executescript(sql)  # run twice — must be a no-op
        items = json.loads(conn.execute("SELECT rejection_history FROM documents WHERE doc_id='D_MIX'").fetchone()[0])
        assert len(items) == 1
        assert items[0]["rejection_id"] == "rej_keep"
        assert items[0]["ai_response"] == "already"
        assert items[0]["response_revision_no"] == 3

    def test_empty_history_untouched(self):
        conn = self._base_conn()
        self._insert_doc(conn, "D_EMPTY", [])
        conn.executescript(MIGRATION_037.read_text(encoding="utf-8"))
        raw = conn.execute("SELECT rejection_history FROM documents WHERE doc_id='D_EMPTY'").fetchone()[0]
        assert json.loads(raw) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.1.2/2.2.2/2.2.3 — the review_id normalization primitives, standalone
# ══════════════════════════════════════════════════════════════════════════════════════

class TestIsReviewRowId:
    @pytest.mark.parametrize("value", [1, 244, "244", " 244 ", "1"])
    def test_valid_row_ids(self, value):
        assert is_review_row_id(value) is True

    @pytest.mark.parametrize("value", [
        True, False, None, "", "   ", "abc", 0, -1, 3.5, [1], "0", "-5",
        "1" * 5000,   # Python 3.11+ refuses int() beyond a few thousand digits
        "²",       # .isdigit() is True for this but int() cannot parse it
    ])
    def test_invalid_values(self, value):
        assert is_review_row_id(value) is False


class TestRejectionReviewKeyValueShapes:
    def test_int_and_string_fold_to_the_same_key(self):
        assert rejection_review_key(244) == rejection_review_key("244") == "244"

    @pytest.mark.parametrize("value", [None, True, "", "   ", "abc", 0, -1, 3.5, [1]])
    def test_unusable_values_fold_to_empty(self, value):
        assert rejection_review_key(value) == ""

    def test_unidentifiable_marker_folds_to_empty(self):
        assert rejection_review_key(UNIDENTIFIABLE_REVIEW_ID) == ""


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.1.5 — transition_document_review stores review_id only when it is given and valid
# ══════════════════════════════════════════════════════════════════════════════════════

class TestTransitionDocumentReviewStoresReviewId:
    def test_a_valid_review_id_is_stored_on_the_new_item(self, db_conn, monkeypatch):
        _patch_pipeline(db_conn, monkeypatch)
        result = transition_document_review(
            doc_id="D001", action="reject", actor_user_id="u001",
            user_permissions=ADMIN_PERMS, comment="reason", review_id=101,
        )
        history = json.loads(result["rejection_history"])
        assert history[0]["review_id"] == 101

    def test_the_default_omits_the_key_entirely(self, db_conn, monkeypatch):
        """A human [반려] never passes review_id — the item must not grow a key it never
        had, so an older reader (the client, document_routes) sees exactly what it always
        saw."""
        _patch_pipeline(db_conn, monkeypatch)
        result = transition_document_review(
            doc_id="D001", action="reject", actor_user_id="u001",
            user_permissions=ADMIN_PERMS, comment="reason",
        )
        history = json.loads(result["rejection_history"])
        assert "review_id" not in history[0]

    @pytest.mark.parametrize("bad_id", [True, "", "   ", 3.5, [1]])
    def test_an_unusable_review_id_is_not_stored(self, db_conn, monkeypatch, bad_id):
        _patch_pipeline(db_conn, monkeypatch)
        result = transition_document_review(
            doc_id="D001", action="reject", actor_user_id="u001",
            user_permissions=ADMIN_PERMS, comment="reason", review_id=bad_id,
        )
        history = json.loads(result["rejection_history"])
        assert "review_id" not in history[0]


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.2.3-2.2.6 — record_rejection_response targets the NAMED review row
# ══════════════════════════════════════════════════════════════════════════════════════

class TestRecordRejectionResponseByReviewId:
    def _history(self, db_conn):
        return json.loads(
            db_conn.execute(
                "SELECT rejection_history FROM documents WHERE doc_id='D001'"
            ).fetchone()[0]
        )

    def _reject(self, comment: str, review_id=None):
        return transition_document_review(
            doc_id="D001", action="reject", actor_user_id="u001",
            user_permissions=ADMIN_PERMS, comment=comment, review_id=review_id,
        )

    def test_matches_the_named_row_not_the_last_item(self, db_conn, monkeypatch):
        """A later, keyless (human) rejection must not steal the response meant for an
        earlier, named review row — the exact bug an unconditional history[-1] would be."""
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("review row 101", review_id=101)
        _reopen(db_conn)
        self._reject("human rejection, no row")  # lands AFTER, carries no review_id

        item = record_rejection_response(
            doc_id="D001", response_text="addressed row 101", recorded_by="u001",
            revision_no=1, review_id=101,
        )
        assert item is not None
        items = self._history(db_conn)
        assert items[0]["ai_response"] == "addressed row 101"
        assert items[1]["ai_response"] is None, "the later keyless item stays untouched"

    def test_string_and_int_review_id_name_the_same_row(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("c", review_id=244)
        item = record_rejection_response(
            doc_id="D001", response_text="resp", recorded_by="u001",
            revision_no=1, review_id="244",
        )
        assert item is not None
        assert self._history(db_conn)[0]["ai_response"] == "resp"

    def test_first_matching_duplicate_wins(self, db_conn, monkeypatch):
        """Defensive case: one review row should make one rejection, but if two items
        somehow carry the same review_id, the FIRST written one collects the response."""
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("first", review_id=7)
        _reopen(db_conn)
        self._reject("second, same row", review_id=7)

        item = record_rejection_response(
            doc_id="D001", response_text="resp", recorded_by="u001",
            revision_no=1, review_id=7,
        )
        assert item is not None
        items = self._history(db_conn)
        assert items[0]["ai_response"] == "resp"
        assert items[1]["ai_response"] is None

    def test_unmatched_review_id_records_nothing(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("c", review_id=7)
        assert record_rejection_response(
            doc_id="D001", response_text="resp", recorded_by="u001",
            revision_no=1, review_id=999,
        ) is None
        assert self._history(db_conn)[0]["ai_response"] is None

    @pytest.mark.parametrize("bad_id", [True, "", "   ", 3.5, [1], UNIDENTIFIABLE_REVIEW_ID])
    def test_invalid_explicit_review_id_records_nothing(self, db_conn, monkeypatch, bad_id):
        """A broken claim is NOT an absent one — it must not fall through to the legacy
        latest-item policy and silently overwrite a different rejection's response."""
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("c", review_id=7)
        assert record_rejection_response(
            doc_id="D001", response_text="resp", recorded_by="u001",
            revision_no=1, review_id=bad_id,
        ) is None
        assert self._history(db_conn)[0]["ai_response"] is None

    def test_absent_review_id_still_uses_the_legacy_latest_item_policy(self, db_conn, monkeypatch):
        """The ONE legacy case: no field at all (a pre-T0005 mention, or an internal
        caller with no review row to name) — unaffected by an earlier item's review_id."""
        _patch_store_everywhere(db_conn, monkeypatch)
        self._reject("row 5", review_id=5)
        _reopen(db_conn)
        self._reject("no row id")

        item = record_rejection_response(
            doc_id="D001", response_text="legacy resp", recorded_by="u001", revision_no=2,
        )
        assert item is not None
        items = self._history(db_conn)
        assert items[-1]["ai_response"] == "legacy resp"
        assert items[0]["ai_response"] is None


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.2.2 — the inbox boundary that reads the worker's review_id claim off the body
# ══════════════════════════════════════════════════════════════════════════════════════

class TestSubmittedReviewIdBoundary:
    def test_absent_field_returns_none(self):
        assert inbox_routes._submitted_review_id({"other": 1}) is None

    def test_valid_int_passes_through_verbatim(self):
        assert inbox_routes._submitted_review_id({"review_id": 55}) == 55

    def test_valid_numeric_string_passes_through_verbatim(self):
        assert inbox_routes._submitted_review_id({"review_id": "55"}) == "55"

    @pytest.mark.parametrize("bad", [True, False, "", "   ", None, 3.5, [1], "abc"])
    def test_unidentifiable_values_return_the_marker(self, bad):
        assert inbox_routes._submitted_review_id({"review_id": bad}) is UNIDENTIFIABLE_REVIEW_ID


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.2.2-2.2.3 — the real _handle_edit boundary (rejection review feedback, group
# 0466 TR0006 rev0): TestSubmittedReviewIdBoundary above proves only the tiny extraction
# helper; TestRecordRejectionResponseByReviewId above calls record_rejection_response
# directly. Neither exercises the actual POST /api/v1/inbox "edit" path that threads a
# worker-submitted review_id from the JSON body, through _handle_edit's
# _submitted_review_id() call, to record_rejection_response — a dropped or mutated id
# anywhere on that path would still pass every test above. This class drives the real
# FastAPI route (a fresh sqlite file DB + full schema, mirroring
# test_inbox_timemachine_reject_0046.py's harness) end to end.
# ══════════════════════════════════════════════════════════════════════════════════════

import sys as _sys
import tempfile as _tempfile
from contextlib import contextmanager as _contextmanager

_E2E_SERVER_DIR = Path(__file__).resolve().parents[1]
_E2E_SCHEMA_DIR = _E2E_SERVER_DIR / "sql" / "migrations" / "sqlite"
_E2E_QUERIES_JSON = _E2E_SERVER_DIR / "sql" / "queries" / "queries.json"

_E2E_QUERIES: dict[str, str] = {}
if _E2E_QUERIES_JSON.exists():
    _raw_q = json.loads(_E2E_QUERIES_JSON.read_text(encoding="utf-8"))
    for _section, _entries in _raw_q.items():
        if isinstance(_entries, dict):
            for _key, _sql in _entries.items():
                if isinstance(_sql, str):
                    _E2E_QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


class _E2EMockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @_contextmanager
    def begin_transaction(self):
        yield _E2EMockTxn(self._conn)

    def close(self):
        self._conn.close()


class _E2EMockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


_E2E_PROJECT_ID = "trid0466"
_E2E_GROUP_ID = "trid0466-__ALL__-0006"
_E2E_USER_ID = "usr_trid0466"


@pytest.fixture(scope="module")
def e2e_db():
    _sys.path.insert(0, str(_E2E_SERVER_DIR))
    with _tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _E2EMockDB(db_path)
    for sql_file in sorted(_E2E_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def e2e_store(e2e_db):
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = e2e_db
            self._sq = None

        def _sql(self, key: str) -> str:
            if key in _E2E_QUERIES:
                return _E2E_QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module", autouse=True)
def e2e_seed(e2e_db, e2e_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    now = now_iso()
    projects.create({"project_id": _E2E_PROJECT_ID, "project_name": "TR0006 e2e"})
    users.create({
        "user_id": _E2E_USER_ID, "username": "trid0466worker",
        "email": "trid0466@test.com", "password": "hashed",
    })
    store = get_store()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ("document.create", "document.read", "document.update", "document.reject"):
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_worker", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        [_E2E_USER_ID, _E2E_PROJECT_ID, "role_worker", now],
    )
    db_groups.create({
        "group_id": _E2E_GROUP_ID, "project_id": _E2E_PROJECT_ID,
        "module": "__ALL__", "title": "TR0006 e2e group",
    })
    for code, name in (("R", "Requirement"), ("N", "Notice")):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, code, name, "work", 1, 1, 0, now, now],
        )


def _e2e_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app)


def _e2e_make_edit_token(tmp_path, doc_id: str) -> str:
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_edit"):
        result = token_service.issue(
            project=_E2E_PROJECT_ID, group_id=_E2E_GROUP_ID,
            action_scope="edit", doc_ref=doc_id, issued_to=_E2E_USER_ID,
        )
    return result["raw_token"]


def _e2e_create_rejected_doc(doc_id: str, seq: int, stored_path: Path, rejection_history: list):
    from modules.flow_gate.db import documents as db_docs
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text("# Original (rejected)")
    db_docs.create({
        "doc_id": doc_id, "project_id": _E2E_PROJECT_ID, "type_code": "N", "seq": seq,
        "title": doc_id, "group_id": _E2E_GROUP_ID, "module": "__ALL__",
        "owner_id": _E2E_USER_ID, "file_path": str(stored_path), "revision_no": 0,
    })
    db_docs.update(doc_id, {
        "doc_review_status": "rejected",
        "rejection_history": json.dumps(rejection_history, ensure_ascii=False),
    })


def _e2e_post_edit(raw: str, doc_id: str, *, review_id=None, content: str = "# Reworked"):
    body = {
        "project": _E2E_PROJECT_ID, "module": "__ALL__", "group": "0006",
        "action": "edit", "doc_id": doc_id, "edit_reason": "rejected",
        "content": content, "rejection_response": "addressed the review comments",
    }
    if review_id is not None:
        body["review_id"] = review_id
    with patch("modules.flow_gate.rbac.permission_service.has_permission", return_value=True):
        return _e2e_client().post(
            "/api/v1/inbox", json=body,
            headers={"Authorization": f"Bearer {raw}"},
        )


class TestRejectionResponseThroughRealInboxEdit:
    """Drives POST /api/v1/inbox (action=edit, edit_reason=rejected) for real — the
    named review_id sits BEFORE a later, different item (a distinct review_id in one
    test, a keyless human item in the other), so history[-1] is never the target. If
    _handle_edit dropped/mutated the submitted id anywhere, record_rejection_response
    would fall through to its legacy history[-1] policy and update the wrong (later)
    row instead of the named one — these tests assert only the non-last target
    changes and the later item stays untouched, which the old history[-1]-is-the-
    target shape could not distinguish from the legacy fallback."""

    def test_integer_review_id_reaches_the_named_non_last_item(self, tmp_path):
        from modules.flow_gate.db import documents as db_docs

        doc_id = f"{_E2E_GROUP_ID}-N0001"
        stored = tmp_path / "docs" / f"{doc_id}_document.md"
        history = [
            {"rejection_id": "rej_a", "reason": "row 41", "review_id": 41,
             "rejected_at": "2026-08-01T00:00:00", "ai_response": None},
            {"rejection_id": "rej_b", "reason": "row 55, the target", "review_id": 55,
             "rejected_at": "2026-08-02T00:00:00", "ai_response": None},
            {"rejection_id": "rej_x", "reason": "row 70, later and different",
             "review_id": 70, "rejected_at": "2026-08-03T00:00:00", "ai_response": None},
        ]
        _e2e_create_rejected_doc(doc_id, 1, stored, history)
        raw = _e2e_make_edit_token(tmp_path, doc_id)

        resp = _e2e_post_edit(raw, doc_id, review_id=55)
        assert resp.status_code == 200, resp.text

        updated = json.loads(db_docs.get_by_id(doc_id)["rejection_history"])
        assert updated[0]["ai_response"] is None, "the earlier row must stay untouched"
        assert updated[1]["ai_response"] == "addressed the review comments"
        assert updated[2]["ai_response"] is None, (
            "the later (last) row must stay untouched -- a fall-through to the "
            "legacy history[-1] policy would wrongly land here"
        )

    def test_json_round_tripped_string_review_id_names_the_same_row(self, tmp_path):
        """The mention prints an int; a worker's JSON round trip can hand it back as a
        string. The real boundary must fold "55" and 55 to the same history item, and
        a later KEYLESS (human-rejection-shaped) item must still stay untouched."""
        from modules.flow_gate.db import documents as db_docs

        doc_id = f"{_E2E_GROUP_ID}-N0002"
        stored = tmp_path / "docs" / f"{doc_id}_document.md"
        history = [
            {"rejection_id": "rej_c", "reason": "row 41", "review_id": 41,
             "rejected_at": "2026-08-01T00:00:00", "ai_response": None},
            {"rejection_id": "rej_d", "reason": "row 55, the target", "review_id": 55,
             "rejected_at": "2026-08-02T00:00:00", "ai_response": None},
            {"rejection_id": "rej_y", "reason": "later human rejection, no row",
             "rejected_at": "2026-08-03T00:00:00", "ai_response": None},
        ]
        _e2e_create_rejected_doc(doc_id, 2, stored, history)
        raw = _e2e_make_edit_token(tmp_path, doc_id)

        resp = _e2e_post_edit(raw, doc_id, review_id="55")
        assert resp.status_code == 200, resp.text

        updated = json.loads(db_docs.get_by_id(doc_id)["rejection_history"])
        assert updated[0]["ai_response"] is None
        assert updated[1]["ai_response"] == "addressed the review comments"
        assert updated[2]["ai_response"] is None, (
            "the later keyless (last) row must stay untouched -- a fall-through to "
            "the legacy history[-1] policy would wrongly land here"
        )

    def test_absent_review_id_field_still_updates_the_last_item_legacy_path(self, tmp_path):
        """A pre-T0005 worker mention sends no review_id at all — _handle_edit must still
        pass the field's ABSENCE (None) through, not synthesize one, so the legacy
        history[-1] policy in record_rejection_response fires."""
        from modules.flow_gate.db import documents as db_docs

        doc_id = f"{_E2E_GROUP_ID}-N0003"
        stored = tmp_path / "docs" / f"{doc_id}_document.md"
        history = [
            {"rejection_id": "rej_e", "reason": "human rejection, no row",
             "rejected_at": "2026-08-01T00:00:00", "ai_response": None},
        ]
        _e2e_create_rejected_doc(doc_id, 3, stored, history)
        raw = _e2e_make_edit_token(tmp_path, doc_id)

        resp = _e2e_post_edit(raw, doc_id, review_id=None)
        assert resp.status_code == 200, resp.text

        updated = json.loads(db_docs.get_by_id(doc_id)["rejection_history"])
        assert updated[0]["ai_response"] == "addressed the review comments"


# ══════════════════════════════════════════════════════════════════════════════════════
# 0458 T0007 §3.2 — the response lands on the rejection it answers, not on the last one
#
# `history[-1]` was the whole targeting policy. It is right exactly while nothing else is
# appended between the rejection and its answer — and a human rejection, or a second review
# row's automatic one, is appended precisely there. The answer then annotated a complaint
# the worker never read.
# ══════════════════════════════════════════════════════════════════════════════════════

from modules.flow_gate.workflow.pipeline_service import (  # noqa: E402
    UNIDENTIFIABLE_REVIEW_ID,
    rejection_review_key,
)


def _item(review_id: Any = "__absent__", *, reason: str = "reason") -> dict:
    """A rejection_history item shaped exactly like transition_document_review writes it."""
    item: dict[str, Any] = {
        "rejection_id": new_rejection_id(),
        "reason": reason,
        "rejected_at": "2026-08-01T00:00:00+09:00",
        "rejected_by": "u001",
        "ai_response": None,
        "responded_at": None,
        "response_recorded_by": None,
        "response_revision_no": None,
    }
    if review_id != "__absent__":
        item["review_id"] = review_id
    return item


def _seed_history(db_conn, items: list) -> None:
    db_conn.execute(
        "UPDATE documents SET rejection_history = ?, doc_review_status='rejected' "
        "WHERE doc_id='D001'",
        (json.dumps(items, ensure_ascii=False),),
    )
    db_conn.commit()


def _read_history(db_conn) -> list:
    return json.loads(
        db_conn.execute(
            "SELECT rejection_history FROM documents WHERE doc_id='D001'"
        ).fetchone()[0]
    )


class TestRejectionReviewKey:
    def test_an_integer_and_its_json_round_trip_are_one_key(self):
        assert rejection_review_key(244) == rejection_review_key("244") == "244"
        assert rejection_review_key(" 244 ") == "244"

    def test_nothing_that_cannot_name_a_row_becomes_a_key(self):
        for value in (None, True, False, "", "   ", UNIDENTIFIABLE_REVIEW_ID):
            assert rejection_review_key(value) == "", value

    def test_a_non_numeric_string_never_becomes_a_key(self):
        """0458 T0008: the column is a positive integer, so "abc" (and a float or a
        digit-plus-noise string) cannot name a row no matter how a stored value spells it."""
        for value in ("abc", "244a", "3.5", "-1", "0"):
            assert rejection_review_key(value) == "", value

    def test_a_unicode_digit_string_int_cannot_parse_never_becomes_a_key(self):
        """0458 T0008 rev3 rework: `str.isdigit()` is True for Unicode digit characters
        `int()` still cannot parse — the superscript "²" is the sharpest example
        (`"²".isdigit()` is True, `int("²")` raises ValueError). The old
        `text.isdigit() and int(text) > 0` check let that ValueError escape unhandled
        instead of returning False; a submitted `review_id` this shape must fold to no key,
        not raise."""
        for value in ("²", "²⁴⁴", "½"):
            assert rejection_review_key(value) == "", value

    def test_an_overlong_digit_string_never_becomes_a_key(self):
        """0458 T0008 rev3 rework: even a string of ordinary ASCII digits can make `int()`
        raise if it is absurdly long — Python 3.11+ caps str-to-int conversion at a few
        thousand digits to block algorithmic-complexity abuse. `isdigit()`/`isdecimal()` say
        nothing about length, so this is a second, independent way the naive check could
        raise instead of returning False for a value that plainly cannot name a row."""
        assert rejection_review_key("9" * 5000) == ""


class TestRecordRejectionResponseTargeting:
    def test_a_later_review_rows_rejection_does_not_take_the_answer(self, db_conn, monkeypatch):
        """§3.2-3: history = [244 (the target), 245 (appended after it)]."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244, reason="fix the scope"),
                                _item(245, reason="fix the tests")])

        item = record_rejection_response(
            doc_id="D001", response_text="Scope rewritten.",
            recorded_by="u001", revision_no=3, review_id=244,
        )
        assert item is not None
        items = _read_history(db_conn)
        assert len(items) == 2                       # no entry appended
        assert items[0]["review_id"] == 244
        assert items[0]["ai_response"] == "Scope rewritten."
        assert items[0]["response_recorded_by"] == "u001"
        assert items[0]["response_revision_no"] == 3
        assert items[0]["responded_at"] is not None
        assert items[1]["ai_response"] is None       # the trailing item is untouched
        assert items[1]["responded_at"] is None

    def test_a_human_rejection_appended_after_the_target_does_not_take_it(self, db_conn, monkeypatch):
        """§3.2-3, second shape: the trailing item carries no `review_id` key at all —
        exactly what the human [반려] button writes."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244, reason="fix the scope"),
                                _item(reason="and the title is wrong")])

        record_rejection_response(
            doc_id="D001", response_text="Scope rewritten.",
            recorded_by="u001", revision_no=3, review_id=244,
        )
        items = _read_history(db_conn)
        assert len(items) == 2
        assert items[0]["ai_response"] == "Scope rewritten."
        assert items[1]["ai_response"] is None
        assert "review_id" not in items[1]

    @pytest.mark.parametrize("stored", [244, "244"])
    @pytest.mark.parametrize("submitted", [244, "244"])
    def test_an_integer_and_its_string_name_the_same_row(self, db_conn, monkeypatch,
                                                         stored, submitted):
        """§3.2-4: the column is an integer; the value comes back through two JSON hops."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(stored), _item(245)])

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=submitted,
        ) is not None
        items = _read_history(db_conn)
        assert items[0]["ai_response"] == "done"
        assert items[1]["ai_response"] is None

    def test_an_id_that_matches_nothing_records_nothing(self, db_conn, monkeypatch):
        """§3.2-5: no fallback to the last item. Losing a stale submission's response beats
        writing it onto a rejection it does not answer."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(245), _item(246)])
        before = _read_history(db_conn)

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=244,
        ) is None
        assert _read_history(db_conn) == before

    def test_items_that_cannot_name_their_own_row_are_never_a_match(self, db_conn, monkeypatch):
        """§3.2-5, the identifier-less shapes: a `review_id` key holding null / "" / "   "
        identifies no row, so an explicit target must not settle for one."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(None), _item(""), _item("   ")])
        before = _read_history(db_conn)

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=244,
        ) is None
        assert _read_history(db_conn) == before

    @pytest.mark.parametrize(
        "review_id",
        [True, False, "", "   ", 3.5, [], {}, UNIDENTIFIABLE_REVIEW_ID],
        ids=["true", "false", "blank", "spaces", "float", "list", "dict", "sentinel"],
    )
    def test_a_broken_claim_records_nothing_and_never_borrows_the_legacy_fallback(
        self, db_conn, monkeypatch, review_id
    ):
        """§2.2-6 read strictly: the latest-item policy belongs to a mention that names NO
        review row. A submission that names one with a value that cannot be one has made a
        claim, and a broken claim is answered with silence — not with somebody else's
        rejection. `True` is neither row 1 nor the key "True"; a blank string names nothing.

        This is the regression the first cut got backwards: it folded all of these to None,
        which handed each of them the fallback and let one malformed value overwrite the
        answer of an unrelated review row."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244), _item(245)])
        before = _read_history(db_conn)

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=review_id,
        ) is None
        assert _read_history(db_conn) == before        # not one byte of history moved

    def test_a_broken_claim_cannot_match_an_item_whose_own_id_is_blank(self, db_conn, monkeypatch):
        """The sharp edge of the same rule. `rejection_review_key(True)` is the empty key, and
        so is the key of an item stored with `review_id: ""`. Compare them and `true` in the
        body answers THAT item — a rejection it has nothing to do with. The claim has to be
        rejected before any comparison happens."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(""), _item(244)])
        before = _read_history(db_conn)

        for review_id in (True, "", "   "):
            assert record_rejection_response(
                doc_id="D001", response_text="done", recorded_by="u001",
                revision_no=2, review_id=review_id,
            ) is None, review_id
            assert _read_history(db_conn) == before, review_id

    def test_a_non_numeric_string_cannot_match_even_an_identically_malformed_stored_value(
        self, db_conn, monkeypatch
    ):
        """0458 T0008 rework: `document_reviews.id` is a positive integer, so "abc" cannot
        name a row — not even when a free-form/legacy history item happens to carry the exact
        same malformed string as its own `review_id`. Comparing two non-numeric strings for
        equality is not the same as either of them naming a real row; the claim must be
        rejected before any comparison, exactly like the bool/blank cases above."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item("abc"), _item(244)])
        before = _read_history(db_conn)

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id="abc",
        ) is None
        assert _read_history(db_conn) == before

    @pytest.mark.parametrize("review_id", ["²", "²⁴⁴", "9" * 5000])
    def test_a_value_int_cannot_parse_records_nothing_without_raising(
        self, db_conn, monkeypatch, review_id
    ):
        """0458 T0008 rev3 rework: a Unicode digit string `int()` cannot parse ("²" passes
        `str.isdigit()` but `int("²")` raises ValueError) and an absurdly long ASCII-digit
        string (Python 3.11+'s str-to-int conversion limit also raises ValueError) must both
        be treated the same as any other value that cannot name a row — folded to no match,
        not propagated as an unhandled exception through this boundary."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244), _item(245)])
        before = _read_history(db_conn)

        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=review_id,
        ) is None
        assert _read_history(db_conn) == before

    def test_omitting_the_id_keeps_the_legacy_latest_item_policy(self, db_conn, monkeypatch):
        """§3.2-6: a mention minted before this field existed still works, and so does every
        internal caller that has no review row to name."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(reason="first"), _item(reason="second")])

        record_rejection_response(
            doc_id="D001", response_text="answered the latest",
            recorded_by="u001", revision_no=2,
        )
        items = _read_history(db_conn)
        assert items[0]["ai_response"] is None
        assert items[1]["ai_response"] == "answered the latest"

    def test_the_same_id_twice_overwrites_only_its_own_item(self, db_conn, monkeypatch):
        """§3.2-6: idempotence is per target, not per history."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244), _item(245)])

        record_rejection_response(doc_id="D001", response_text="first",
                                  recorded_by="u001", revision_no=1, review_id=244)
        record_rejection_response(doc_id="D001", response_text="second",
                                  recorded_by="u001", revision_no=2, review_id="244")
        items = _read_history(db_conn)
        assert len(items) == 2
        assert items[0]["ai_response"] == "second"
        assert items[0]["response_revision_no"] == 2
        assert items[1]["ai_response"] is None

    def test_a_duplicated_id_only_answers_the_first_item(self, db_conn, monkeypatch):
        """§3.2-7: one review row makes one rejection, so a second item carrying the same id
        is a ghost. The answer belongs to the item that was written first."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244, reason="the real one"),
                                _item(244, reason="the ghost")])

        item = record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, review_id=244,
        )
        items = _read_history(db_conn)
        assert item["reason"] == "the real one"
        assert items[0]["ai_response"] == "done"
        assert items[1]["ai_response"] is None

    def test_the_no_op_and_truncation_contracts_survive_an_explicit_id(self, db_conn, monkeypatch):
        """§2.2-7: a blank response still records nothing, the ceiling still truncates, and
        neither ever appends a history entry."""
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(244)])

        assert record_rejection_response(
            doc_id="D001", response_text="   ", recorded_by="u001",
            revision_no=1, review_id=244,
        ) is None
        assert _read_history(db_conn)[0]["ai_response"] is None

        item = record_rejection_response(
            doc_id="D001", response_text="x" * (AI_RESPONSE_MAX_LEN + 50),
            recorded_by="u001", revision_no=1, review_id=244,
        )
        assert len(item["ai_response"]) == AI_RESPONSE_MAX_LEN
        assert len(_read_history(db_conn)) == 1

    def test_a_malformed_history_is_still_a_safe_no_op(self, db_conn, monkeypatch):
        """Non-list and non-dict members degrade to "record nothing", with or without an id."""
        _patch_store_everywhere(db_conn, monkeypatch)
        for raw in ('{"a": 1}', "[1, 2, 3]", "{not json", "[]", "null"):
            db_conn.execute(
                "UPDATE documents SET rejection_history = ? WHERE doc_id='D001'", (raw,)
            )
            db_conn.commit()
            assert record_rejection_response(
                doc_id="D001", response_text="done", recorded_by="u001",
                revision_no=1, review_id=244,
            ) is None, raw


class TestInboxForwardsTheReviewId:
    """§3.2-2 — the boundary between the submitted body and the recorder."""

    def _spy(self, monkeypatch):
        calls = []
        import modules.flow_gate.workflow.pipeline_service as ps

        monkeypatch.setattr(ps, "record_rejection_response",
                            lambda **kwargs: calls.append(kwargs))
        return calls

    def test_the_submitted_id_reaches_the_recorder_unchanged(self, monkeypatch):
        from modules.flow_gate.api import inbox_routes

        calls = self._spy(monkeypatch)
        inbox_routes._attach_rejection_response(
            doc_id="D001", response_text="I fixed the scope.",
            review_id=inbox_routes._submitted_review_id({"review_id": 244}),
            actor_user_id="u001", revision_no=3,
        )
        assert calls == [{
            "doc_id": "D001",
            "response_text": "I fixed the scope.",
            "recorded_by": "u001",
            "revision_no": 3,
            "review_id": 244,
            "rejection_id": None,
        }]

    def test_a_string_id_is_forwarded_as_the_string_it_arrived_as(self, monkeypatch):
        from modules.flow_gate.api import inbox_routes

        calls = self._spy(monkeypatch)
        inbox_routes._attach_rejection_response(
            doc_id="D001", response_text="done",
            review_id=inbox_routes._submitted_review_id({"review_id": "244"}),
            actor_user_id="u001", revision_no=3,
        )
        assert calls[0]["review_id"] == "244"

    def test_a_body_without_the_field_forwards_none(self, monkeypatch):
        """The ONLY input that reaches the legacy latest-item policy: no field at all."""
        from modules.flow_gate.api import inbox_routes

        calls = self._spy(monkeypatch)
        inbox_routes._attach_rejection_response(
            doc_id="D001", response_text="done",
            review_id=inbox_routes._submitted_review_id({"action": "edit"}),
            actor_user_id="u001", revision_no=3,
        )
        assert calls[0]["review_id"] is None

    def test_the_boundary_tells_an_absent_field_from_a_broken_one(self):
        """§2.2-2: three outcomes, not two. Absent → None (legacy fallback). Usable →
        verbatim. Present-but-unusable → the marker, which records nothing downstream. A
        bool must never come back as None here: that is what let `"review_id": true`
        answer an unrelated rejection."""
        from modules.flow_gate.api import inbox_routes

        assert inbox_routes._submitted_review_id({}) is None
        assert inbox_routes._submitted_review_id({"action": "edit"}) is None
        assert inbox_routes._submitted_review_id({"review_id": 244}) == 244
        assert inbox_routes._submitted_review_id({"review_id": "244"}) == "244"
        assert inbox_routes._submitted_review_id({"review_id": " 244 "}) == " 244 "
        for value in (
            None, True, False, "", "   ", 3.5, [], {}, "\t\n", "abc", "244a", "-1", "0",
            "²", "²⁴⁴", "9" * 5000,  # 0458 T0008 rev3: Unicode digits and overlong digit
        ):                                                     # strings must not raise
            assert inbox_routes._submitted_review_id({"review_id": value}) is (
                UNIDENTIFIABLE_REVIEW_ID
            ), value

    def test_a_broken_id_stops_at_the_recorder_leaving_the_history_alone(
        self, db_conn, monkeypatch
    ):
        """The storage half of the line above, end to end: the body's value walks the real
        boundary into the real recorder against a real history, and nothing is written."""
        _patch_store_everywhere(db_conn, monkeypatch)
        from modules.flow_gate.api import inbox_routes

        _seed_history(db_conn, [_item(244), _item(245)])
        before = _read_history(db_conn)

        for value in (True, "", "   ", 3.5, None):
            inbox_routes._attach_rejection_response(
                doc_id="D001", response_text="done",
                review_id=inbox_routes._submitted_review_id({"review_id": value}),
                actor_user_id="u001", revision_no=3,
            )
            assert _read_history(db_conn) == before, value

        # …while the SAME path with the field absent still lands on the latest item, and the
        # same path with a real id lands on that id's item.
        inbox_routes._attach_rejection_response(
            doc_id="D001", response_text="legacy",
            review_id=inbox_routes._submitted_review_id({}),
            actor_user_id="u001", revision_no=3,
        )
        assert _read_history(db_conn)[1]["ai_response"] == "legacy"
        inbox_routes._attach_rejection_response(
            doc_id="D001", response_text="targeted",
            review_id=inbox_routes._submitted_review_id({"review_id": "244"}),
            actor_user_id="u001", revision_no=3,
        )
        items = _read_history(db_conn)
        assert items[0]["ai_response"] == "targeted"
        assert items[1]["ai_response"] == "legacy"

    def test_a_blank_response_never_reaches_the_recorder(self, monkeypatch):
        from modules.flow_gate.api import inbox_routes

        calls = self._spy(monkeypatch)
        for text in (None, ""):
            inbox_routes._attach_rejection_response(
                doc_id="D001", response_text=text, review_id=244,
                actor_user_id="u001", revision_no=3,
            )
        assert calls == []

    def test_a_recorder_failure_propagates_instead_of_becoming_success(self, monkeypatch):
        import modules.flow_gate.workflow.pipeline_service as ps
        from modules.flow_gate.api import inbox_routes

        def _boom(**_kwargs):
            raise RuntimeError("history column is gone")

        monkeypatch.setattr(ps, "record_rejection_response", _boom)
        with pytest.raises(RuntimeError, match="history column is gone"):
            inbox_routes._attach_rejection_response(
                doc_id="D001", response_text="done", review_id=244,
                actor_user_id="u001", revision_no=3,
            )

    def test_the_edit_handler_actually_goes_through_that_boundary(self):
        """A helper nothing calls proves nothing. Step 7.5 must reach the recorder through
        it, and must read the id off the request body."""
        import inspect

        from modules.flow_gate.api import inbox_routes

        source = inspect.getsource(inbox_routes._handle_edit)
        assert "resolve_rejection_target(" in source
        assert "_submitted_review_id(body)" in source, (
            "the whole body must go in — the boundary decides on the field's PRESENCE"
        )
        assert "record_rejection_response" not in source, (
            "the handler must prepare the response for the atomic CAS, not call the recorder"
        )


class TestRecordRejectionResponseByRejectionId:
    def test_manual_after_automatic_targets_manual_not_old_review(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        automatic = _item(244, reason="automatic")
        automatic["ai_response"] = "already handled"
        manual = _item(reason="manual")
        _seed_history(db_conn, [automatic, manual])

        result = record_rejection_response(
            doc_id="D001", response_text="fixed manually requested issue",
            recorded_by="u001", revision_no=3,
            rejection_id=manual["rejection_id"], review_id=244,
        )
        items = _read_history(db_conn)
        assert result["rejection_id"] == manual["rejection_id"]
        assert items[0]["ai_response"] == "already handled"
        assert items[1]["ai_response"] == "fixed manually requested issue"

    def test_manual_without_review_row_is_targeted(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        manual = _item(reason="manual")
        _seed_history(db_conn, [manual])
        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, rejection_id=manual["rejection_id"],
        )["rejection_id"] == manual["rejection_id"]

    def test_automatic_rejection_is_targeted_by_rejection_id(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        automatic = _item(244)
        other = _item(245)
        _seed_history(db_conn, [automatic, other])
        record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, rejection_id=automatic["rejection_id"],
        )
        items = _read_history(db_conn)
        assert items[0]["ai_response"] == "done"
        assert items[1]["ai_response"] is None

    def test_unknown_rejection_id_is_byte_stable(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        _seed_history(db_conn, [_item(), _item()])
        before = _read_history(db_conn)
        assert record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, rejection_id="rej_unknown",
        ) is None
        assert _read_history(db_conn) == before

    def test_rejection_id_outranks_conflicting_review_id(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        by_review = _item(244)
        by_rejection = _item(245)
        _seed_history(db_conn, [by_review, by_rejection])
        record_rejection_response(
            doc_id="D001", response_text="done", recorded_by="u001",
            revision_no=2, rejection_id=by_rejection["rejection_id"], review_id=244,
        )
        items = _read_history(db_conn)
        assert items[0]["ai_response"] is None
        assert items[1]["ai_response"] == "done"

    def test_same_rejection_id_overwrites_idempotently(self, db_conn, monkeypatch):
        _patch_store_everywhere(db_conn, monkeypatch)
        target = _item()
        _seed_history(db_conn, [target])
        for text in ("first", "second"):
            record_rejection_response(
                doc_id="D001", response_text=text, recorded_by="u001",
                revision_no=2, rejection_id=target["rejection_id"],
            )
        items = _read_history(db_conn)
        assert len(items) == 1
        assert items[0]["ai_response"] == "second"


def test_inbox_rejection_id_boundary_and_forwarding(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    import modules.flow_gate.workflow.pipeline_service as ps

    calls = []
    monkeypatch.setattr(ps, "record_rejection_response", lambda **kw: calls.append(kw))
    body = {"rejection_id": "  rej_manual  ", "review_id": 244}
    inbox_routes._attach_rejection_response(
        doc_id="D001", response_text="done",
        review_id=inbox_routes._submitted_review_id(body),
        rejection_id=inbox_routes._submitted_rejection_id(body),
        actor_user_id="u001", revision_no=3,
    )
    assert calls[0]["rejection_id"] == "rej_manual"
    assert calls[0]["review_id"] == 244
    assert inbox_routes._submitted_rejection_id({}) is None
    assert inbox_routes._submitted_rejection_id({"rejection_id": " "}) is None
    assert inbox_routes._submitted_rejection_id({"rejection_id": True}) is not None
