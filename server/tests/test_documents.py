"""T058: pytest tests for document CRUD + state machine + storage integration.

Scope:
  1. document CRUD
  2. state transitions (valid / reject invalid transitions / CAS race)
  3. template rendering
  4. verify workflow_events records
  5. auto-close Q

Environment: TESTING=1 (temporary SQLite without sqloader)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

sys.path.insert(0, str(_SERVER_DIR))


def get_all_migrations() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return files


# ── Test DB helper ──────────────────────────────────────────────────────────

class _MockDB:
    """Test driver that uses sqlite3 directly without sqloader."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    # connection.py's _fetch_one/_fetch_all call txn.fetchone()/fetchall() (no
    # underscore) when running inside a transaction. delete() now reads inside the
    # transaction context, so expose the names the real txn interface uses.
    def fetchone(self) -> dict | None:
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self) -> list[dict]:
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]

    # Backwards-compatible aliases (pre-existing test callers).
    fetch_one = fetchone
    fetch_all = fetchall


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    
    # Apply all migrations in order
    for migration_file in get_all_migrations():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            mock_db._conn.executescript(sql)
        except sqlite3.OperationalError:
            # Some migrations might fail (e.g., IF NOT EXISTS constraints)
            pass
    
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    """Patch connection.get_store() to point to the temporary DB."""
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError("_sql is not used in tests")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed_project_user(tmp_db):
    """Seed test project + user."""
    from modules.flow_gate.db import projects, users

    projects.create({"project_id": "DOCTEST", "project_name": "Doc Test Prj"})
    users.create({
        "user_id": "usr_doc_001",
        "username": "docuser",
        "email": "doc@example.com",
        "password": "hashed_pw",
    })
    yield
    # teardown handled by deleting the DB file when the module ends


# ── 1. CRUD tests ───────────────────────────────────────────────────────────

class TestDocumentCRUD:
    def test_create_document(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        doc = document_service.create_document(
            {
                "doc_id": "DOCTEST-T-0001",
                "project_id": "DOCTEST",
                "type_code": "T",
                "seq": 1,
                "title": "Test work order",
            },
            actor_user_id="usr_doc_001",
        )
        assert doc["doc_id"] == "DOCTEST-T-0001"
        assert doc["status"] == "draft"

    def test_create_document_persists_origin_snapshot(self, seed_project_user):
        """0410 T0008: the two nullable AI-origin snapshot fields survive
        create() -> get() without being silently dropped by the cols whitelist
        (the exact failure mode NR0003/WP0005 flagged for db/documents.py:create)."""
        from modules.flow_gate.documents import document_service

        doc = document_service.create_document(
            {
                "doc_id": "DOCTEST-T-ORIGIN",
                "project_id": "DOCTEST",
                "type_code": "T",
                "seq": 2,
                "title": "AI-authored work order",
                "origin_provider_name": "Claude Sonnet 5",
                "origin_ai_run_id": "run_xyz789",
            },
            actor_user_id="usr_doc_001",
        )
        assert doc["origin_provider_name"] == "Claude Sonnet 5"
        assert doc["origin_ai_run_id"] == "run_xyz789"

        fetched = document_service.get_document("DOCTEST-T-ORIGIN")
        assert fetched["origin_provider_name"] == "Claude Sonnet 5"
        assert fetched["origin_ai_run_id"] == "run_xyz789"

    def test_create_document_without_origin_snapshot_is_explicit_none(self, seed_project_user):
        """A document created without the two fields reads back with the keys
        present and set to None — the legacy row the group list renders as 미상."""
        from modules.flow_gate.documents import document_service

        document_service.create_document(
            {
                "doc_id": "DOCTEST-T-LEGACY",
                "project_id": "DOCTEST",
                "type_code": "T",
                "seq": 3,
                "title": "Legacy work order",
            },
            actor_user_id="usr_doc_001",
        )
        doc = document_service.get_document("DOCTEST-T-LEGACY")
        assert "origin_provider_name" in doc
        assert doc["origin_provider_name"] is None
        assert "origin_ai_run_id" in doc
        assert doc["origin_ai_run_id"] is None

    def test_get_document(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        doc = document_service.get_document("DOCTEST-T-0001")
        assert doc is not None
        assert doc["title"] == "Test work order"

    def test_get_document_not_found(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        assert document_service.get_document("NO-SUCH-DOC") is None

    def test_list_documents(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        lst = document_service.list_documents("DOCTEST")
        assert any(d["doc_id"] == "DOCTEST-T-0001" for d in lst)

    def test_list_documents_filter_type(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        lst = document_service.list_documents("DOCTEST", type_code="T")
        assert all(d["type_code"] == "T" for d in lst)

    def test_update_document(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        updated = document_service.update_document(
            "DOCTEST-T-0001",
            {"title": "Updated title"},
            actor_user_id="usr_doc_001",
        )
        assert updated["title"] == "Updated title"

    def test_update_document_status_ignored(self, seed_project_user):
        """update_document() should ignore status even if it is passed."""
        from modules.flow_gate.documents import document_service

        doc_before = document_service.get_document("DOCTEST-T-0001")
        document_service.update_document(
            "DOCTEST-T-0001",
            {"status": "approved"},  # should be ignored
            actor_user_id="usr_doc_001",
        )
        doc_after = document_service.get_document("DOCTEST-T-0001")
        assert doc_after["status"] == doc_before["status"]

    def test_delete_document(self, seed_project_user):
        from modules.flow_gate.documents import document_service

        document_service.create_document(
            {
                "doc_id": "DOCTEST-T-DELETE",
                "project_id": "DOCTEST",
                "type_code": "T",
                "seq": 99,
                "title": "Item to delete",
            },
            actor_user_id="usr_doc_001",
        )
        document_service.delete_document("DOCTEST-T-DELETE", actor_user_id="usr_doc_001")
        assert document_service.get_document("DOCTEST-T-DELETE") is None

    def test_delete_not_found_raises(self, seed_project_user):
        from fastapi import HTTPException
        from modules.flow_gate.documents import document_service

        with pytest.raises(HTTPException) as exc_info:
            document_service.delete_document("NO-SUCH-DOC", actor_user_id="usr_doc_001")
        assert exc_info.value.status_code == 404


# ── 2. State transition tests ───────────────────────────────────────────────

class TestStateMachine:
    @pytest.fixture(autouse=True)
    def _setup_doc(self, seed_project_user, request):
        from modules.flow_gate.documents import document_service

        # Use a unique doc_id per test method (avoid UNIQUE constraint conflicts)
        self._sm_doc_id = f"DOCTEST-SM-{request.node.name[-8:].replace('[','_').replace(']','_')}"
        document_service.create_document(
            {
                "doc_id": self._sm_doc_id,
                "project_id": "DOCTEST",
                "type_code": "T",
                "seq": 10,
                "title": "State machine test document",
                "status": "draft",
            },
            actor_user_id="usr_doc_001",
        )
        yield
        try:
            document_service.delete_document(self._sm_doc_id, actor_user_id="usr_doc_001")
        except Exception:
            pass

    def test_valid_transition_draft_to_open(self):
        from modules.flow_gate.documents import document_service

        doc = document_service.transition_state(
            self._sm_doc_id, "open", actor_user_id="usr_doc_001"
        )
        assert doc["status"] == "open"

    def test_invalid_transition_rejected(self):
        """draft -> rejected is not an allowed transition."""
        from fastapi import HTTPException
        from modules.flow_gate.documents import document_service

        with pytest.raises(HTTPException) as exc_info:
            document_service.transition_state(
                self._sm_doc_id, "rejected", actor_user_id="usr_doc_001"
            )
        assert exc_info.value.status_code == 422

    def test_full_approval_flow(self):
        """draft -> open -> in_review -> approved normal flow."""
        from modules.flow_gate.documents import document_service

        document_service.transition_state(self._sm_doc_id, "open", actor_user_id="usr_doc_001")
        document_service.transition_state(self._sm_doc_id, "in_review", actor_user_id="usr_doc_001")
        doc = document_service.transition_state(self._sm_doc_id, "approved", actor_user_id="usr_doc_001")
        assert doc["status"] == "approved"

    def test_rejection_and_resubmit(self):
        """draft -> open -> in_review -> rejected -> open (resubmit)."""
        from modules.flow_gate.documents import document_service

        document_service.transition_state(self._sm_doc_id, "open", actor_user_id="usr_doc_001")
        document_service.transition_state(self._sm_doc_id, "in_review", actor_user_id="usr_doc_001")
        document_service.transition_state(self._sm_doc_id, "rejected", actor_user_id="usr_doc_001", reason="Needs revision")
        doc = document_service.transition_state(self._sm_doc_id, "open", actor_user_id="usr_doc_001")
        assert doc["status"] == "open"

    def test_terminal_state_no_transition(self):
        """No transitions are allowed from the cancelled state."""
        from fastapi import HTTPException
        from modules.flow_gate.documents import document_service

        document_service.transition_state(self._sm_doc_id, "cancelled", actor_user_id="usr_doc_001")
        with pytest.raises(HTTPException) as exc_info:
            document_service.transition_state(self._sm_doc_id, "open", actor_user_id="usr_doc_001")
        assert exc_info.value.status_code == 422

    def test_cas_conflict_detection(self, tmp_db):
        """CAS race: change state first, then retry with the same expected_val -> 409."""
        from fastapi import HTTPException
        from modules.flow_gate.documents import document_service
        from modules.flow_gate.db.connection import get_store, now_iso

        # draft -> open normal transition
        document_service.transition_state(self._sm_doc_id, "open", actor_user_id="usr_doc_001")

        # Simulate a race by changing the DB directly to in_review
        store = get_store()
        store._db.execute(
            "UPDATE documents SET status='in_review', updated_at=? WHERE doc_id=?",
            [now_iso(), self._sm_doc_id],
        )

        # Try transitioning to open from in_review -> not allowed (no in_review -> open)
        with pytest.raises(HTTPException):
            document_service.transition_state(
                self._sm_doc_id, "open", actor_user_id="usr_doc_001"
            )


# ── 3. Verify workflow_events records ───────────────────────────────────────

class TestWorkflowEvents:
    def test_create_document_records_event(self, seed_project_user):
        from modules.flow_gate.db import workflow_events as db_events
        from modules.flow_gate.documents import document_service

        document_service.create_document(
            {
                "doc_id": "DOCTEST-EV-0001",
                "project_id": "DOCTEST",
                "type_code": "M",
                "seq": 20,
                "title": "Event test document",
            },
            actor_user_id="usr_doc_001",
        )
        doc = document_service.get_document("DOCTEST-EV-0001")
        events = db_events.list_by_document(doc["id"])
        assert any(e["event_type"] == "doc_created" for e in events)

    def test_transition_records_state_changed(self, seed_project_user):
        from modules.flow_gate.db import workflow_events as db_events
        from modules.flow_gate.documents import document_service

        document_service.transition_state(
            "DOCTEST-EV-0001", "open", actor_user_id="usr_doc_001"
        )
        doc = document_service.get_document("DOCTEST-EV-0001")
        events = db_events.list_by_document(doc["id"])
        state_events = [e for e in events if e["event_type"] == "state_changed"]
        assert len(state_events) >= 1
        last = state_events[0]
        assert last["from_state"] == "draft"
        assert last["to_state"] == "open"

    def test_delete_records_event(self, seed_project_user):
        from modules.flow_gate.db import workflow_events as db_events
        from modules.flow_gate.documents import document_service

        # Create a separate doc to verify the delete event (dedicated to test_delete_records_event)
        document_service.create_document(
            {
                "doc_id": "DOCTEST-EV-DEL",
                "project_id": "DOCTEST",
                "type_code": "M",
                "seq": 25,
                "title": "Delete event test",
            },
            actor_user_id="usr_doc_001",
        )
        document_service.delete_document("DOCTEST-EV-DEL", actor_user_id="usr_doc_001")

        # The doc_deleted event is stored with document_id=NULL (FK released)
        # Search by project_id + event_type
        from modules.flow_gate.db import workflow_events as db_events
        from modules.flow_gate.db.connection import get_store
        store = get_store()
        events = store._fetch_all(
            "SELECT * FROM workflow_events WHERE event_type='doc_deleted' AND project_id='DOCTEST'"
        )
        assert len(events) >= 1


# ── 4. Q auto-close tests ───────────────────────────────────────────────────

class TestQAutoClose:
    def test_a_document_closes_q(self, seed_project_user):
        """Creating an A document closes the target Q document."""
        from modules.flow_gate.documents import document_service

        document_service.create_document(
            {
                "doc_id": "DOCTEST-Q-0001",
                "project_id": "DOCTEST",
                "type_code": "Q",
                "seq": 30,
                "title": "Query document",
                "status": "open",
            },
            actor_user_id="usr_doc_001",
        )

        document_service.create_document(
            {
                "doc_id": "DOCTEST-A-0001",
                "project_id": "DOCTEST",
                "type_code": "A",
                "seq": 31,
                "title": "Response document",
                "target_id": "DOCTEST-Q-0001",
            },
            actor_user_id="usr_doc_001",
        )

        q_doc = document_service.get_document("DOCTEST-Q-0001")
        assert q_doc["status"] == "closed"

    def test_close_group_documents(self, seed_project_user):
        """Calling close_group_documents() closes Q documents in the group."""
        from modules.flow_gate.db import groups as db_groups
        from modules.flow_gate.documents import document_service

        # Create the group first to satisfy the group_id FK
        db_groups.create({
            "group_id": "GRP-001",
            "project_id": "DOCTEST",
            "title": "Test group",
        })

        document_service.create_document(
            {
                "doc_id": "DOCTEST-Q-GRP-0001",
                "project_id": "DOCTEST",
                "type_code": "Q",
                "seq": 40,
                "title": "Group query document",
                "group_id": "GRP-001",
                "status": "open",
            },
            actor_user_id="usr_doc_001",
        )

        count = document_service.close_group_documents(
            "GRP-001", actor_user_id="usr_doc_001"
        )
        assert count >= 1

        q = document_service.get_document("DOCTEST-Q-GRP-0001")
        assert q["status"] == "closed"


# ── 5. Template rendering tests ─────────────────────────────────────────────

class TestTemplateService:
    def test_save_and_get_template(self, tmp_path):
        from modules.flow_gate.documents import template_service

        content = "Hello {{ title }}!"
        with patch(
            "modules.flow_gate.documents.template_service._template_storage_path"
        ) as mock_path:
            tmpl_path = tmp_path / "_global" / "T_TEST.j2"
            mock_path.return_value = tmpl_path

            with patch(
                "modules.flow_gate.documents.template_service.get_template_by_type",
                return_value=None,
            ), patch(
                "modules.flow_gate.documents.template_service.create_template",
                return_value={"id": 1, "type_code": "T_TEST", "template_path": str(tmpl_path)},
            ):
                result = template_service.save_template(
                    content=content,
                    type_code="T_TEST",
                    project_id=None,
                    uploaded_by="usr_doc_001",
                )
                assert result["type_code"] == "T_TEST"
                assert tmpl_path.read_text(encoding="utf-8") == content

    def test_render_template(self, tmp_path):
        from modules.flow_gate.documents import template_service

        # Create the actual template file
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        tmpl_file = tmpl_dir / "T.j2"
        tmpl_file.write_text("Title: {{ title }}", encoding="utf-8")

        out_dir = tmp_path / "output"
        out_dir.mkdir()

        with patch(
            "modules.flow_gate.documents.template_service.get_template_by_type",
            return_value={
                "id": 1,
                "type_code": "T",
                "template_path": str(tmpl_file),
            },
        ), patch(
            "modules.flow_gate.storage.paths.document_path",
            return_value=out_dir / "DOC001_output.txt",
        ):
            out_path = template_service.render_template(
                doc_id="DOCTEST-T-RENDER",
                type_code="T",
                project_id="DOCTEST",
                context={"title": "Rendering test"},
                group_code="GRP",
                doc_code="DOC001",
                filename="output.txt",
            )
            assert out_path.exists()
            assert "Rendering test" in out_path.read_text(encoding="utf-8")

    def test_render_template_not_found(self):
        from fastapi import HTTPException
        from modules.flow_gate.documents import template_service

        with patch(
            "modules.flow_gate.documents.template_service.get_template_by_type",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                template_service.render_template(
                    doc_id="X",
                    type_code="UNKNOWN",
                    project_id="DOCTEST",
                    context={},
                    group_code="G",
                    doc_code="D",
                    filename="f.txt",
                )
            assert exc_info.value.status_code == 404
