"""T058: tests for the workflow pipeline + events + prompt copy.

Coverage:
- Group/document transition consistency (accept valid transitions, reject invalid ones)
- Detect CAS contention
- Record prompt_copied events
- timeline API (mock)
- Automatic Q state management
- Permission checks
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Separate DB dependencies with TESTING=1
os.environ.setdefault("TESTING", "1")
# 0414 T0010: the two TestWorkflowDecideTransition cases import a route module, which builds
# server/config.py Settings — and Settings has no defaults for these three. Every other suite
# in server/tests already declares them at import time, so this file only passed when it
# happened to run after one of them. Setting them here makes the file self-sufficient.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

# ── Shared fixtures ───────────────────────────────────────────────────────────

SCHEMA_SQL = (
    Path(__file__).resolve().parents[1]
    / "sql" / "migrations" / "sqlite" / "001_flowgate_schema.sql"
)

MIGRATION_003 = (
    Path(__file__).resolve().parents[1]
    / "sql" / "migrations" / "sqlite" / "003_workflow_states.sql"
)


@pytest.fixture
def db_conn():
    """In-memory SQLite DB — schema + migration 003 applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)
    if MIGRATION_003.exists():
        m3 = MIGRATION_003.read_text(encoding="utf-8")
        conn.executescript(m3)
    conn.execute("PRAGMA foreign_keys = ON")
    # seed: project
    conn.execute(
        "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
        "VALUES('P001','TestProject',1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    # seed: user
    conn.execute(
        "INSERT INTO users(user_id, username, email, password, is_admin, is_active, created_at, updated_at) "
        "VALUES('u001','admin','admin@test.com','hash',1,1,'2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    conn.commit()
    return conn


def _make_store(conn: sqlite3.Connection):
    """FlowGateStore のMock を返す (SQLite conn ラップ)."""
    store = MagicMock()
    store.update_cas = MagicMock(return_value=True)

    def _fetch_one(sql, params=None):
        row = conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(sql, params=None):
        rows = conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]

    def _execute(sql, params=None):
        conn.execute(sql, params or [])
        conn.commit()

    store._fetch_one = _fetch_one
    store._fetch_all = _fetch_all
    store._execute = _execute

    # update_cas implementation
    def _update_cas(table, row_id, id_col, expected_col, expected_val, updates):
        set_parts = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [expected_val, row_id]
        cur = conn.execute(
            f"UPDATE {table} SET {set_parts} WHERE {expected_col} = ? AND {id_col} = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount > 0

    store.update_cas = _update_cas
    return store


# ── transition_rules テスト ───────────────────────────────────────────────────

from modules.flow_gate.workflow.transition_rules import (
    check_permission,
    get_doc_rule,
    get_group_rule,
)


class TestTransitionRules:
    def test_doc_submit_from_draft(self):
        rule = get_doc_rule("draft", "submit")
        assert rule is not None
        assert rule.next_state == "open"

    def test_doc_approve_from_open(self):
        rule = get_doc_rule("open", "approve")
        assert rule is not None
        assert rule.next_state == "approved"

    def test_doc_reject_requires_comment_note(self):
        rule = get_doc_rule("open", "reject")
        assert rule is not None
        assert rule.next_state == "rejected"

    def test_doc_invalid_transition(self):
        rule = get_doc_rule("approved", "submit")
        assert rule is None

    def test_doc_resubmit_from_rejected(self):
        rule = get_doc_rule("rejected", "resubmit")
        assert rule is not None
        assert rule.next_state == "open"

    def test_group_approve_from_in_progress(self):
        rule = get_group_rule("in_progress", "approve")
        assert rule is not None
        assert rule.next_state == "approved"

    def test_group_close_from_approved(self):
        rule = get_group_rule("approved", "close")
        assert rule is not None
        assert rule.next_state == "closed"

    def test_group_invalid_transition(self):
        rule = get_group_rule("closed", "approve")
        assert rule is None

    def test_check_permission_match(self):
        assert check_permission({"document.approve", "document.read"}, ("document.approve",))

    def test_check_permission_or_logic(self):
        assert check_permission({"own.draft"}, ("document.update", "own.draft"))

    def test_check_permission_fail(self):
        assert not check_permission({"document.read"}, ("document.approve",))


# ── event_logger tests ────────────────────────────────────────────────────────

from modules.flow_gate.workflow.event_logger import (
    EVT_PROMPT_COPIED,
    EVT_STATE_CHANGED,
    log_event,
    log_prompt_copied,
    log_state_changed,
)


class TestEventLogger:
    """event_logger: validate by monkeypatching the db_events module attribute."""

    def test_log_state_changed_structure(self, monkeypatch):
        """log_state_changed calls db_events.create with the correct arguments."""
        captured: list[dict] = []

        import modules.flow_gate.workflow.event_logger as el

        mock_mod = MagicMock()
        mock_mod.create = lambda data: (captured.append(data), {"id": 1})[1]
        monkeypatch.setattr(el, "db_events", mock_mod)

        el.log_state_changed(
            project_id="P001",
            actor_user_id="u001",
            from_state="draft",
            to_state="open",
            group_id="G001",
            document_id=1,
            action_code="submit",
        )

        assert len(captured) == 1
        d = captured[0]
        assert d["event_type"] == EVT_STATE_CHANGED
        assert d["from_state"] == "draft"
        assert d["to_state"] == "open"
        assert d["project_id"] == "P001"

    def test_log_prompt_copied_structure(self, monkeypatch):
        """log_prompt_copied が prompt_copied イベントタイプで記録される。"""
        captured: list[dict] = []

        import modules.flow_gate.workflow.event_logger as el

        mock_mod = MagicMock()
        mock_mod.create = lambda data: (captured.append(data), {"id": 2})[1]
        monkeypatch.setattr(el, "db_events", mock_mod)

        el.log_prompt_copied(
            project_id="P001",
            actor_user_id="u001",
            doc_id="P001-G001-R0001",
            document_id=1,
            group_id="G001",
            template_type="R",
            action_context="in_progress",
        )

        assert len(captured) == 1
        d = captured[0]
        assert d["event_type"] == EVT_PROMPT_COPIED
        meta = json.loads(d["metadata"])
        assert meta["doc_id"] == "P001-G001-R0001"
        assert meta["template"] == "R"


# ── pipeline_service テスト ───────────────────────────────────────────────────

from modules.flow_gate.workflow.pipeline_service import (
    PermissionError as WFPermissionError,
    TransitionError,
    create_group,
    transition_document,
    transition_group,
)

# pipeline_service テスト中の store への参照 (test_cas_conflict_detected 用)
_mock_store_ref: list = [None]


def _setup_pipeline_mocks(
    db_conn_fixture: sqlite3.Connection,
    monkeypatch,
) -> None:
    """pipeline_service が使う db.* モジュールを SQLite conn でモック置換。

    各モジュールが import 時にキャッシュした get_store 参照をそれぞれ patch する。
    """
    import modules.flow_gate.db.groups as db_g
    import modules.flow_gate.db.documents as db_d
    import modules.flow_gate.workflow.pipeline_service as ps
    import modules.flow_gate.workflow.event_logger as el

    conn = db_conn_fixture

    # ── store mock ──────────────────────────────────────────────────────────
    store = MagicMock()

    def _fetch_one(sql, params=None):
        row = conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(sql, params=None):
        rows = conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]

    def _execute(sql, params=None):
        conn.execute(sql, params or [])
        conn.commit()

    def _update_cas(table, row_id, id_col, expected_col, expected_val, updates):
        set_parts = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [expected_val, row_id]
        cur = conn.execute(
            f"UPDATE {table} SET {set_parts} WHERE {expected_col} = ? AND {id_col} = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount > 0

    store._fetch_one = _fetch_one
    store._fetch_all = _fetch_all
    store._execute = _execute
    store.update_cas = _update_cas

    # 各モジュールのキャッシュ済み get_store をそれぞれ差し替え
    monkeypatch.setattr(db_g, "get_store", lambda: store)
    monkeypatch.setattr(db_d, "get_store", lambda: store)
    monkeypatch.setattr(ps, "get_store", lambda: store)

    # event_logger の db_events も stub
    mock_events = MagicMock()
    mock_events.create = MagicMock(return_value={"id": 99})
    monkeypatch.setattr(el, "db_events", mock_events)

    _mock_store_ref[0] = store


@pytest.fixture
def pipeline_env(db_conn, monkeypatch):
    """pipeline_service テスト用環境。"""
    _setup_pipeline_mocks(db_conn, monkeypatch)
    # グループ1件 insert
    db_conn.execute(
        "INSERT INTO groups(group_id,project_id,module,title,status,created_at,updated_at) "
        "VALUES('G001','P001','__ALL__','Test Group','draft','2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    # ドキュメント1件 insert
    db_conn.execute(
        "INSERT INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,status,"
        "owner_id,created_at,updated_at) "
        "VALUES('P001-G001-R0001','P001','__ALL__','G001','R',1,'Req 1','draft',"
        "'u001','2026-01-01T00:00:00','2026-01-01T00:00:00')"
    )
    db_conn.commit()
    return db_conn


ADMIN_PERMS = {
    "project.group.manage",
    "document.create",
    "document.read",
    "document.update",
    "document.approve",
    "document.reject",
    "document.delete",
    "document.delete.own.draft",
    "own.draft",
}
WORKER_PERMS = {"document.create", "document.read", "document.update", "own.draft"}


class TestTransitionGroup:
    def test_group_approve_from_in_progress(self, pipeline_env, monkeypatch):
        conn = pipeline_env
        conn.execute(
            "UPDATE groups SET status='in_progress' WHERE group_id='G001'"
        )
        conn.commit()
        result = transition_group(
            group_id="G001",
            action="approve",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
        )
        assert result["status"] == "approved"

    def test_group_invalid_transition_raises(self, pipeline_env):
        with pytest.raises(TransitionError):
            transition_group(
                group_id="G001",
                action="approve",  # draft → approve は許可外
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
            )

    def test_group_permission_error(self, pipeline_env):
        """document.create / document.update なし → start で PermissionError。"""
        with pytest.raises(WFPermissionError):
            transition_group(
                group_id="G001",
                action="start",
                actor_user_id="u001",
                user_permissions={"document.read"},  # create/update なし → 403
            )

    def test_group_not_found_raises(self, pipeline_env):
        with pytest.raises(ValueError, match="not found"):
            transition_group(
                group_id="NONEXIST",
                action="approve",
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
            )

    def test_create_group_permission_error(self, pipeline_env):
        with pytest.raises(WFPermissionError):
            create_group(
                project_id="P001",
                module="__ALL__",
                title="Test",
                actor_user_id="u001",
                user_permissions=WORKER_PERMS,
                group_id="G002",
            )


class TestTransitionDocument:
    def test_doc_submit_draft_to_open(self, pipeline_env):
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="submit",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
        )
        assert result["status"] == "open"

    def test_doc_approve_open_to_approved(self, pipeline_env, db_conn):
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="approve",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
        )
        assert result["status"] == "approved"

    def test_doc_reject_requires_comment(self, pipeline_env, db_conn):
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()
        with pytest.raises(ValueError, match="Comment required when rejecting"):
            transition_document(
                doc_id="P001-G001-R0001",
                action="reject",
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
                comment=None,
            )

    def test_doc_reject_stores_comment(self, pipeline_env, db_conn):
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="reject",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
            comment="unclear content",
        )
        assert result["status"] == "rejected"
        meta = json.loads(result["meta"])
        assert meta["reject_comment"] == "unclear content"

    def test_doc_invalid_transition_raises(self, pipeline_env):
        with pytest.raises(TransitionError):
            transition_document(
                doc_id="P001-G001-R0001",
                action="approve",  # draft → approve は不正
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
            )

    def test_doc_not_found_raises(self, pipeline_env):
        with pytest.raises(ValueError, match="not found"):
            transition_document(
                doc_id="NONE",
                action="submit",
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
            )

    def test_doc_permission_error(self, pipeline_env):
        """document.approve なし → approve で 403 相当."""
        with pytest.raises(WFPermissionError):
            transition_document(
                doc_id="P001-G001-R0001",
                action="submit",
                actor_user_id="other_user",  # own.draft なし
                user_permissions={"document.read"},
            )

    def test_cas_conflict_detected(self, pipeline_env, monkeypatch):
        """update_cas が rowcount=0 を返したとき TransitionError。"""
        store = _mock_store_ref[0]
        monkeypatch.setattr(store, "update_cas", lambda *a, **kw: False)
        with pytest.raises(TransitionError):
            transition_document(
                doc_id="P001-G001-R0001",
                action="submit",
                actor_user_id="u001",
                user_permissions=ADMIN_PERMS,
            )


# ── prompt_copy_service テスト ────────────────────────────────────────────────

from modules.flow_gate.workflow.prompt_copy_service import (
    build_prompt,
    _expected_output_types,
)


class TestPromptCopyService:
    def _patch_db(self, monkeypatch, doc: dict, group: dict | None, related: list[dict], vr_head: bool = False):
        import modules.flow_gate.workflow.prompt_copy_service as ps
        import modules.flow_gate.workflow.event_logger as el

        mock_docs = MagicMock()
        mock_docs.get_by_id = MagicMock(return_value=doc)
        mock_docs.list_documents = MagicMock(return_value=related)
        monkeypatch.setattr(ps, "db_docs", mock_docs)

        mock_groups = MagicMock()
        mock_groups.get_by_id = MagicMock(return_value=group)
        monkeypatch.setattr(ps, "db_groups", mock_groups)

        mock_events = MagicMock()
        mock_events.create = MagicMock(return_value={"id": 1})
        monkeypatch.setattr(el, "db_events", mock_events)

        # workflow_sequences: None by default (non-VR); set vr_head=True for VR tests
        mock_ws = MagicMock()
        if vr_head:
            # _get_v_report_path now reads the canonical "type" key (not "item_type")
            # on the head and sequence items.
            mock_ws.get_sequence_by_doc_id = MagicMock(return_value={"id": 99})
            mock_ws.get_effective_head = MagicMock(return_value={"type": "VR", "sort_order": 2, "id": 10})
            mock_ws.get_sequence_items = MagicMock(return_value=[
                {"id": 5, "type": "V", "sort_order": 1},
                {"id": 10, "type": "VR", "sort_order": 2},
            ])
        else:
            mock_ws.get_sequence_by_doc_id = MagicMock(return_value=None)
        monkeypatch.setattr(ps, "db_ws", mock_ws)

        mock_wir = MagicMock()
        mock_wir.get_latest_result_by_item = MagicMock(
            return_value={"registered_path": "/docs/V0001.md"} if vr_head else None
        )
        monkeypatch.setattr(ps, "db_wir", mock_wir)

        return mock_events

    def test_build_prompt_contains_doc_id(self, monkeypatch):
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "title": "Test requirement",
            "status": "open", "file_path": "docs/R0001.md", "owner_id": "u001",
        }
        group = {"group_id": "G001", "title": "Group 1", "status": "in_progress"}
        self._patch_db(monkeypatch, doc, group, [])
        result = build_prompt(doc_id="P001-G001-R0001", actor_user_id="u001")
        assert "P001-G001-R0001" in result["prompt_text"]
        assert result["doc_id"] == "P001-G001-R0001"

    def test_build_prompt_includes_related_docs(self, monkeypatch):
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "title": "Requirement",
            "status": "open", "file_path": None, "owner_id": "u001",
        }
        related = [
            {
                "doc_id": "P001-G001-Q0001", "type_code": "Q",
                "title": "Query 1", "status": "open",
            }
        ]
        group = {"group_id": "G001", "title": "Group 1", "status": "clarifying"}
        self._patch_db(monkeypatch, doc, group, related)
        result = build_prompt(doc_id="P001-G001-R0001", actor_user_id="u001")
        assert "P001-G001-Q0001" in result["prompt_text"]
        assert len(result["context"]["related_docs"]) == 1

    def test_build_prompt_records_prompt_copied_event(self, monkeypatch):
        doc = {
            "id": 1, "doc_id": "P001-G001-T0001", "project_id": "P001",
            "group_id": "G001", "type_code": "T", "title": "Task",
            "status": "open", "file_path": None, "owner_id": "u001",
        }
        group = {"group_id": "G001", "title": "Group 1", "status": "in_progress"}
        mock_events = self._patch_db(monkeypatch, doc, group, [])
        build_prompt(doc_id="P001-G001-T0001", actor_user_id="u001")
        mock_events.create.assert_called_once()
        call_data = mock_events.create.call_args[0][0]
        assert call_data["event_type"] == "prompt_copied"

    def test_build_prompt_not_found(self, monkeypatch):
        import modules.flow_gate.workflow.prompt_copy_service as ps
        monkeypatch.setattr(ps.db_docs, "get_by_id", MagicMock(return_value=None))
        with pytest.raises(ValueError, match="not found"):
            build_prompt(doc_id="NONE", actor_user_id="u001")

    def test_expected_output_types_ds(self):
        assert _expected_output_types("DS") == ["D"]

    def test_expected_output_types_r(self):
        types = _expected_output_types("R")
        assert "M" in types and "Q" in types

    def test_expected_output_types_n(self):
        assert _expected_output_types("N") == ["NR"]

    def test_build_prompt_vr_includes_v_report_path(self, monkeypatch):
        """In the VR stage, the preceding V report path is included in the prompt."""
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "title": "Requirement",
            "status": "open", "file_path": None, "owner_id": "u001",
        }
        group = {"group_id": "G001", "title": "Group 1", "status": "in_progress"}
        self._patch_db(monkeypatch, doc, group, [], vr_head=True)
        result = build_prompt(doc_id="P001-G001-R0001", actor_user_id="u001")
        assert "/docs/V0001.md" in result["prompt_text"]
        assert "Corrections" in result["prompt_text"]

    def test_build_prompt_non_vr_no_v_report(self, monkeypatch):
        """If it is not the VR stage, the issue-fix section is not included."""
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "title": "Requirement",
            "status": "open", "file_path": None, "owner_id": "u001",
        }
        group = {"group_id": "G001", "title": "Group 1", "status": "in_progress"}
        self._patch_db(monkeypatch, doc, group, [], vr_head=False)
        result = build_prompt(doc_id="P001-G001-R0001", actor_user_id="u001")
        assert "corrections" not in result["prompt_text"]


# ── register_workflow_result / transition_document_review(submit) tests ───────

class TestRegisterWorkflowResultReviewTransition:
    """DB004 §6.1 single-writer contract tests (T604).

    After the T604 integration:
    - register_workflow_result() only performs result_doc_id SET + workflow_item_results INSERT.
    - Automatic doc_review_status transitions are handled by transition_document_review(action='submit').
    - _auto_transition_doc_review() was removed.
    """

    def _patch(self, monkeypatch, doc: dict):
        import modules.flow_gate.workflow.pipeline_service as ps
        import modules.flow_gate.workflow.event_logger as el

        mock_wir = MagicMock()
        mock_wir.insert_result = MagicMock()
        monkeypatch.setattr(ps, "db_wir", mock_wir)

        updated_doc = {**doc}

        mock_docs = MagicMock()
        mock_docs.get_by_id = MagicMock(return_value=doc)

        def _update(doc_id, fields):
            updated_doc.update(fields)
            return updated_doc

        mock_docs.update = MagicMock(side_effect=_update)
        monkeypatch.setattr(ps, "db_docs", mock_docs)

        mock_events = MagicMock()
        mock_events.create = MagicMock(return_value={"id": 1})
        monkeypatch.setattr(el, "db_events", mock_events)

        # workflow_sequences mock. 0457 T0005: the slot write is now the conditional
        # claim, which returns the item row as it stands afterwards — this stand-in
        # reports the claim as won so the registration proceeds.
        mock_wfseq = MagicMock()
        mock_wfseq.claim_item_result_doc_id = MagicMock(
            side_effect=lambda item_id, result_doc_id: {
                "id": item_id, "result_doc_id": result_doc_id,
            }
        )

        import modules.flow_gate.db.workflow_sequences as db_wfseq_mod
        monkeypatch.setattr(
            db_wfseq_mod, "claim_item_result_doc_id", mock_wfseq.claim_item_result_doc_id
        )
        # 0457 T0007: registration first asks whether the document already occupies
        # some other slot (migration 090 makes two slots for one document a unique-index
        # violation). None = it does not, which is the case every test here describes.
        mock_wfseq.get_item_by_result_doc_id = MagicMock(return_value=None)
        monkeypatch.setattr(
            db_wfseq_mod, "get_item_by_result_doc_id", mock_wfseq.get_item_by_result_doc_id
        )

        return mock_docs, updated_doc, mock_wfseq

    def test_register_workflow_result_claims_the_slot(self, monkeypatch):
        """register_workflow_result() must write result_doc_id on the slot (DB004 §6.3).

        0457 T0005: the write is the conditional claim rather than the unconditional
        set_item_result_doc_id, so that a slot holding a different document is refused
        instead of overwritten. What is asserted here is unchanged — this registration
        does put its document into slot 1.
        """
        from modules.flow_gate.workflow.pipeline_service import register_workflow_result
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": None,
        }
        mock_docs, _, mock_wfseq = self._patch(monkeypatch, doc)
        register_workflow_result(
            item_id=1,
            registered_path="/out/R0001.md",
            registered_doc_id="P001-G001-R0001",
            registered_at="2026-01-01T00:00:00",
            actor_user_id="u001",
        )
        mock_wfseq.claim_item_result_doc_id.assert_called_once_with(1, "P001-G001-R0001")

    def test_register_workflow_result_no_doc_review_write(self, monkeypatch):
        """register_workflow_result() does not write doc_review_status (DB004 §6.1)."""
        from modules.flow_gate.workflow.pipeline_service import register_workflow_result
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": None,
        }
        mock_docs, _, _ = self._patch(monkeypatch, doc)
        register_workflow_result(
            item_id=1,
            registered_path="/out/R0001.md",
            registered_doc_id="P001-G001-R0001",
            registered_at="2026-01-01T00:00:00",
            actor_user_id="u001",
        )
        mock_docs.update.assert_not_called()

    def test_submit_none_review_status_sets_pending_review(self, monkeypatch):
        """transition_document_review(submit): doc_review_status=None → pending_review (DB004 §6.1)."""
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": None,
        }
        mock_docs, updated, _ = self._patch(monkeypatch, doc)
        result = transition_document_review(
            doc_id="P001-G001-R0001",
            action="submit",
            actor_user_id="u001",
            user_permissions={"document.update"},
        )
        assert result.get("doc_review_status") == "pending_review"
        mock_docs.update.assert_called_once()

    def test_submit_rejected_review_status_sets_revised(self, monkeypatch):
        """transition_document_review(submit): doc_review_status=rejected → revised (DB004 §6.1)."""
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": "rejected",
        }
        mock_docs, updated, _ = self._patch(monkeypatch, doc)
        result = transition_document_review(
            doc_id="P001-G001-R0001",
            action="submit",
            actor_user_id="u001",
            user_permissions={"document.update"},
        )
        assert result.get("doc_review_status") == "revised"

    def test_submit_approved_review_status_sets_pending_review(self, monkeypatch):
        """transition_document_review(submit): doc_review_status=approved → pending_review (M026 §8-1)."""
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": "approved",
        }
        mock_docs, updated, _ = self._patch(monkeypatch, doc)
        result = transition_document_review(
            doc_id="P001-G001-R0001",
            action="submit",
            actor_user_id="u001",
            user_permissions={"document.update"},
        )
        assert result.get("doc_review_status") == "pending_review"

    def test_submit_pending_review_status_raises_transition_error(self, monkeypatch):
        """transition_document_review(submit): doc_review_status=pending_review -> TransitionError (no transition)."""
        from modules.flow_gate.workflow.pipeline_service import transition_document_review, TransitionError
        doc = {
            "id": 1, "doc_id": "P001-G001-R0001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": "pending_review",
        }
        self._patch(monkeypatch, doc)
        with pytest.raises(TransitionError):
            transition_document_review(
                doc_id="P001-G001-R0001",
                action="submit",
                actor_user_id="u001",
                user_permissions={"document.update"},
            )

    def test_no_auto_transition_doc_review_exported(self):
        """Verify that _auto_transition_doc_review was removed from pipeline_service (guarantees T604 single-writer behavior)."""
        import modules.flow_gate.workflow.pipeline_service as ps
        assert not hasattr(ps, "_auto_transition_doc_review"), (
            "_auto_transition_doc_review must be removed; use transition_document_review(action='submit')"
        )


# ── T604: register_document_result_endpoint — single-writer + SSE tests ───────

class TestRegisterDocumentResultEndpoint:
    """T604: verify that workflow.py:511 register_document_result_endpoint satisfies single-writer + SSE behavior.

    VR066 §4 HIGH: T604 fixed the issue where this endpoint did not call
    _auto_transition_doc_review(), causing doc_review_status transitions to be silently skipped.
    """

    def _make_mocks(self, monkeypatch, doc: dict, head_item: dict, seq: dict):
        """Shared mock patching."""
        import modules.flow_gate.workflow.routers.workflow as wf_router
        import modules.flow_gate.workflow.pipeline_service as ps
        import modules.flow_gate.workflow.event_logger as el

        monkeypatch.setattr(wf_router, "db_docs", MagicMock(get_by_id=MagicMock(return_value=doc)))

        mock_db_wseq = MagicMock()
        mock_db_wseq.get_sequence_by_doc_id = MagicMock(return_value=seq)
        mock_db_wseq.get_effective_head = MagicMock(return_value=head_item)

        mock_wir = MagicMock()
        mock_wir.insert_result = MagicMock()
        monkeypatch.setattr(ps, "db_wir", mock_wir)

        updated_doc = {**doc}

        mock_ps_docs = MagicMock()
        mock_ps_docs.get_by_id = MagicMock(return_value=doc)

        def _update(doc_id, fields):
            updated_doc.update(fields)
            return updated_doc

        mock_ps_docs.update = MagicMock(side_effect=_update)
        monkeypatch.setattr(ps, "db_docs", mock_ps_docs)

        mock_events = MagicMock()
        mock_events.create = MagicMock(return_value={"id": 1})
        monkeypatch.setattr(el, "db_events", mock_events)

        mock_wfseq_mod = MagicMock()
        import modules.flow_gate.db.workflow_sequences as db_wfseq_mod
        monkeypatch.setattr(db_wfseq_mod, "set_item_result_doc_id", MagicMock())
        # 0457 T0005: registration claims the slot conditionally; the stand-in reports
        # the slot as now holding the registered document (claim won).
        monkeypatch.setattr(
            db_wfseq_mod,
            "claim_item_result_doc_id",
            MagicMock(side_effect=lambda item_id, result_doc_id: {
                "id": item_id, "result_doc_id": result_doc_id,
            }),
        )
        # 0457 T0007: registration first asks whether the document already occupies
        # some other slot (migration 090 makes two slots for one document a unique-index
        # violation). None = it does not, which is the case every test here describes.
        monkeypatch.setattr(
            db_wfseq_mod, "get_item_by_result_doc_id", MagicMock(return_value=None)
        )

        return mock_db_wseq, updated_doc

    def test_register_result_triggers_doc_review_transition(self, monkeypatch):
        """register_document_result_endpoint calls transition_document_review(submit) to transition doc_review_status."""
        import asyncio
        import modules.flow_gate.workflow.routers.workflow as wf_router
        import modules.flow_gate.db.workflow_sequences as db_wseq_mod

        doc = {
            "id": 1, "doc_id": "P001-G001-DS0001", "project_id": "P001",
            "group_id": "G001", "type_code": "DS", "doc_review_status": None,
            "file_path": "/storage/DS0001.md",
        }
        head_item = {"id": 10, "type": "DS"}
        seq = {"id": 1}

        mock_db_wseq, updated_doc = self._make_mocks(monkeypatch, doc, head_item, seq)
        monkeypatch.setattr(db_wseq_mod, "get_sequence_by_doc_id", mock_db_wseq.get_sequence_by_doc_id)
        monkeypatch.setattr(db_wseq_mod, "get_effective_head", mock_db_wseq.get_effective_head)

        transition_called = []
        orig_transition = wf_router.transition_document_review

        def _spy_transition(**kwargs):
            transition_called.append(kwargs)
            return orig_transition(**kwargs)

        monkeypatch.setattr(wf_router, "transition_document_review", _spy_transition)

        current_user = {"user_id": "u001", "permissions": ["document.update"]}

        async def _run():
            return await wf_router.register_document_result_endpoint(
                doc_id="P001-G001-DS0001",
                current_user=current_user,
            )

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()
        assert len(transition_called) == 1
        assert transition_called[0]["action"] == "submit"
        assert transition_called[0]["doc_id"] == "P001-G001-DS0001"

    def test_register_result_sse_broadcast_on_transition(self, monkeypatch):
        """An SSE DOC_REVIEW_STATUS_CHANGED broadcast occurs when doc_review_status transitions (VR066 §8)."""
        import asyncio
        import modules.flow_gate.workflow.routers.workflow as wf_router
        import modules.flow_gate.db.workflow_sequences as db_wseq_mod

        doc = {
            "id": 1, "doc_id": "P001-G001-DS0001", "project_id": "P001",
            "group_id": "G001", "type_code": "DS", "doc_review_status": None,
            "file_path": "/storage/DS0001.md",
        }
        head_item = {"id": 10, "type": "DS"}
        seq = {"id": 1}

        self._make_mocks(monkeypatch, doc, head_item, seq)
        monkeypatch.setattr(db_wseq_mod, "get_sequence_by_doc_id", MagicMock(return_value=seq))
        monkeypatch.setattr(db_wseq_mod, "get_effective_head", MagicMock(return_value=head_item))

        broadcast_calls = []

        async def _mock_broadcast(event):
            broadcast_calls.append(event)

        # Patch transition_document_review to return an updated doc
        monkeypatch.setattr(
            wf_router,
            "transition_document_review",
            lambda **kw: {**doc, "doc_review_status": "pending_review"},
        )

        import modules.flow_gate.api.v1.events.publisher as pub_mod
        monkeypatch.setattr(pub_mod, "broadcast_event", _mock_broadcast)

        current_user = {"user_id": "u001", "permissions": ["document.update"]}

        async def _run():
            return await wf_router.register_document_result_endpoint(
                doc_id="P001-G001-DS0001",
                current_user=current_user,
            )

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
        assert len(broadcast_calls) == 1
        payload = broadcast_calls[0].payload
        assert payload["prev_status"] is None
        assert payload["next_status"] == "pending_review"

class TestDecideWorkflowReviewStatus:
    """T487: verify that decide_workflow sets doc_review_status to wf_in_progress."""

    def _make_tx(self):
        """Return a MagicMock that acts as a context manager."""
        tx = MagicMock()
        tx.__enter__ = MagicMock(return_value=None)
        tx.__exit__ = MagicMock(return_value=False)
        return tx

    def test_decide_sets_wf_in_progress(self, monkeypatch):
        """After calling decide_workflow, doc_review_status is updated to 'wf_in_progress'."""
        import modules.flow_gate.services.workflow_decision_service as svc

        doc = {
            "id": 1, "doc_id": "R001", "project_id": "P001",
            "group_id": "G001", "type_code": "R", "doc_review_status": None,
        }

        updated_fields: dict = {}

        mock_docs = MagicMock()
        mock_docs.get_by_id = MagicMock(return_value=doc)

        def _update(doc_id, fields):
            updated_fields.update(fields)
            return {**doc, **fields}

        mock_docs.update = MagicMock(side_effect=_update)
        monkeypatch.setattr(svc, "db_documents", mock_docs)

        seq_id_box = [0]

        def _get_seq_by_doc(did):
            return None if seq_id_box[0] == 0 else {"id": 42}

        mock_wfseq = MagicMock()
        mock_wfseq.get_sequence_by_doc_id = MagicMock(side_effect=_get_seq_by_doc)

        def _insert_seq(did):
            seq_id_box[0] = 1

        mock_wfseq.insert_sequence = MagicMock(side_effect=_insert_seq)
        mock_wfseq.insert_sequence_item = MagicMock()
        mock_wfseq.get_effective_head = MagicMock(return_value=None)
        monkeypatch.setattr(svc, "db_wfseq", mock_wfseq)

        mock_store = MagicMock()
        mock_store.transaction = MagicMock(return_value=self._make_tx())
        monkeypatch.setattr(svc, "get_store", MagicMock(return_value=mock_store))

        result = svc.decide_workflow(
            doc_id="R001",
            doc_class="R",
            sequence=[{"id": 1, "type": "D", "label": "Basic Design"}],
        )

        assert result["status"] == "decided"
        assert updated_fields.get("doc_review_status") == "wf_in_progress", (
            "decide_workflow must set doc_review_status to 'wf_in_progress'."
        )

    def test_decide_already_decided_raises(self, monkeypatch):
        """If a sequence is already decided, ValueError is raised — there must be no state change."""
        import modules.flow_gate.services.workflow_decision_service as svc

        doc = {"id": 1, "doc_id": "R001", "project_id": "P001", "type_code": "R"}

        mock_docs = MagicMock()
        mock_docs.get_by_id = MagicMock(return_value=doc)
        mock_docs.update = MagicMock()
        monkeypatch.setattr(svc, "db_documents", mock_docs)

        mock_wfseq = MagicMock()
        mock_wfseq.get_sequence_by_doc_id = MagicMock(return_value={"id": 99})
        monkeypatch.setattr(svc, "db_wfseq", mock_wfseq)

        import pytest
        with pytest.raises(ValueError, match="already_decided"):
            svc.decide_workflow("R001", "R", [{"id": 1, "type": "D", "label": "Basic Design"}])

        mock_docs.update.assert_not_called()


# ── T518: tests for R document draft→open transition after workflow decision ──

class TestWorkflowDecideTransition:
    """T518: verify that an R document status transitions draft→open after [workflow decision]."""

    def test_transition_rule_exists(self):
        """Verify that the ("draft", "workflow_decide") rule exists and next_state="open"."""
        rule = get_doc_rule("draft", "workflow_decide")
        assert rule is not None, "The ('draft','workflow_decide') transition rule is missing"
        assert rule.next_state == "open"
        # Permission: either document.update or own.draft is sufficient (OR)
        assert "document.update" in rule.required_permissions
        assert "own.draft" in rule.required_permissions

    def test_transition_document_workflow_decide(self, pipeline_env):
        """Calling transition_document('workflow_decide') transitions the R document status to 'open'."""
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="workflow_decide",
            actor_user_id="u001",
            user_permissions=WORKER_PERMS,
        )
        assert result["status"] == "open", (
            f"expected 'open' but got {result['status']!r}"
        )

    def test_transition_document_workflow_decide_admin(self, pipeline_env):
        """Verify that the workflow_decide transition also succeeds with admin permissions."""
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="workflow_decide",
            actor_user_id="u001",
            user_permissions=ADMIN_PERMS,
        )
        assert result["status"] == "open"

    def test_route_post_workflow_decide_transitions_draft_to_open(self, pipeline_env, monkeypatch):
        """Verify that when POST /workflow/{doc_id}/decide is called, the R document transitions draft→open.

        Mock decide_workflow to remove sequence-related dependencies,
        and verify that _transition_document actually updates the DB.
        """
        import modules.flow_gate.api.v1.workflow_decision_routes as routes_mod

        # verify_bearer は routes_mod にバインドされた参照を patch する
        # verify_bearer -> return a non-admin PM user token
        monkeypatch.setattr(
            routes_mod, "verify_bearer",
            lambda req: {"issued_to": "u001", "is_admin": False},
        )

        # decide_workflow -> return a successful response (skip sequence-related DB changes)
        monkeypatch.setattr(
            routes_mod, "decide_workflow",
            lambda **kw: {"status": "decided", "doc_id": kw["doc_id"], "sequence_count": 1, "head": None},
        )

        from unittest.mock import MagicMock
        fake_request = MagicMock()

        body = routes_mod.DecideRequest(
            doc_class="R",
            sequence=[routes_mod.SequenceItem(id=1, type="D", label="Basic Design")],
        )

        response = routes_mod.post_workflow_decide("P001-G001-R0001", body, fake_request)
        assert response.status_code == 201, f"status={response.status_code} body={response.body}"

        # Verify directly in the DB: status must be 'open'
        conn = pipeline_env
        row = conn.execute(
            "SELECT status FROM documents WHERE doc_id = 'P001-G001-R0001'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "open", (
            f"expected 'open' but DB still has {row['status']!r}. "
            "The route did not perform the draft→open transition."
        )

    def test_route_does_not_transition_non_draft(self, pipeline_env, monkeypatch, db_conn):
        """A document already in open state is not transitioned by the route (no duplicate transition)."""
        import modules.flow_gate.api.v1.workflow_decision_routes as routes_mod

        # Set the document to open in advance
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()

        monkeypatch.setattr(
            routes_mod, "verify_bearer",
            lambda req: {"issued_to": "u001", "is_admin": False},
        )
        monkeypatch.setattr(
            routes_mod, "decide_workflow",
            lambda **kw: {"status": "decided", "doc_id": kw["doc_id"], "sequence_count": 1, "head": None},
        )

        from unittest.mock import MagicMock
        response = routes_mod.post_workflow_decide(
            "P001-G001-R0001",
            routes_mod.DecideRequest(
                doc_class="R",
                sequence=[routes_mod.SequenceItem(id=1, type="D", label="Basic Design")],
            ),
            MagicMock(),
        )
        assert response.status_code == 201

        # status must still be 'open' (no double transition)
        row = db_conn.execute(
            "SELECT status FROM documents WHERE doc_id='P001-G001-R0001'"
        ).fetchone()
        assert row["status"] == "open"


# ── T528: tests for parent open → closed transition when a child document is created ──

class TestChildCreatedTransitionRule:
    """T528: unit tests for the ("open", "child_created") → closed transition rule."""

    def test_rule_exists(self):
        """Verify that the ("open", "child_created") rule exists and next_state="closed"."""
        rule = get_doc_rule("open", "child_created")
        assert rule is not None, "The ('open','child_created') transition rule is missing (T528)"
        assert rule.next_state == "closed"

    def test_rule_requires_update_or_approve(self):
        """required_permissions includes document.update or document.approve."""
        rule = get_doc_rule("open", "child_created")
        assert rule is not None
        perms = set(rule.required_permissions)
        assert perms & {"document.update", "document.approve"}, (
            "The child_created rule requires document.update or document.approve permission"
        )

    def test_transition_r_doc_open_to_closed(self, pipeline_env, db_conn):
        """R-type document: child_created action in open state -> closed."""
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()
        result = transition_document(
            doc_id="P001-G001-R0001",
            action="child_created",
            actor_user_id="u001",
            user_permissions={"document.update", "document.approve"},
        )
        assert result["status"] == "closed", (
            f"R document open→closed transition failed: status={result['status']!r}"
        )

    def test_transition_m_doc_open_to_closed(self, pipeline_env, db_conn):
        """M-type document: child_created action in open state -> closed."""
        db_conn.execute(
            "INSERT OR REPLACE INTO documents"
            "(doc_id,project_id,module,group_id,type_code,seq,title,status,"
            "owner_id,created_at,updated_at) "
            "VALUES('P001-G001-M0001','P001','__ALL__','G001','M',10,'Memo 1','open',"
            "'u001','2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        db_conn.commit()
        result = transition_document(
            doc_id="P001-G001-M0001",
            action="child_created",
            actor_user_id="u001",
            user_permissions={"document.update", "document.approve"},
        )
        assert result["status"] == "closed", (
            f"M document open→closed transition failed: status={result['status']!r}"
        )

    def test_try_close_parent_skips_non_rm_type(self, pipeline_env, db_conn, monkeypatch):
        """_try_close_parent_on_child_created: do not transition when type_code is not R/M."""
        db_conn.execute(
            "INSERT OR REPLACE INTO documents"
            "(doc_id,project_id,module,group_id,type_code,seq,title,status,"
            "owner_id,created_at,updated_at) "
            "VALUES('P001-G001-T0099','P001','__ALL__','G001','T',99,'Task','open',"
            "'u001','2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        db_conn.commit()

        import modules.flow_gate.documents.routers.documents as doc_routes
        import modules.flow_gate.db.documents as _db_docs

        monkeypatch.setattr(
            doc_routes,
            "_try_close_parent_on_child_created",
            doc_routes._try_close_parent_on_child_created,
        )

        # Call the helper directly — T type must be skipped
        doc_routes._try_close_parent_on_child_created("P001-G001-T0099", "u001")

        row = db_conn.execute(
            "SELECT status FROM documents WHERE doc_id='P001-G001-T0099'"
        ).fetchone()
        assert row["status"] == "open", (
            "A T-type parent is not a target for closed transition (T528 applies only to R/M)"
        )

    def test_try_close_parent_transitions_r_open_to_closed(self, pipeline_env, db_conn):
        """_try_close_parent_on_child_created: open R-type parent -> closed transition."""
        db_conn.execute(
            "UPDATE documents SET status='open' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()

        import modules.flow_gate.documents.routers.documents as doc_routes
        doc_routes._try_close_parent_on_child_created("P001-G001-R0001", "u001")

        row = db_conn.execute(
            "SELECT status FROM documents WHERE doc_id='P001-G001-R0001'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "closed", (
            f"After calling the R document helper it should be closed, got: {row['status']!r}"
        )

    def test_try_close_parent_noop_when_not_open(self, pipeline_env, db_conn):
        """_try_close_parent_on_child_created: no transition if the parent is not open."""
        db_conn.execute(
            "UPDATE documents SET status='approved' WHERE doc_id='P001-G001-R0001'"
        )
        db_conn.commit()

        import modules.flow_gate.documents.routers.documents as doc_routes
        doc_routes._try_close_parent_on_child_created("P001-G001-R0001", "u001")

        row = db_conn.execute(
            "SELECT status FROM documents WHERE doc_id='P001-G001-R0001'"
        ).fetchone()
        assert row["status"] == "approved", (
            "Parent document not open should not change status"
        )


class TestRecentGroupDocsAnchor:
    """Regression: the next-action mention's "Recent documents in group" must be
    anchored at the group's latest document, not the workflow-owning parent R
    (whose seq is the group minimum). Otherwise a doc created after the parent —
    e.g. a memo produced earlier in the same sequence — is silently excluded.
    """

    def _insert_memo(self, db_conn, seq=2, status="closed"):
        db_conn.execute(
            "INSERT INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,status,"
            "owner_id,created_at,updated_at) "
            "VALUES('P001-G001-M0002','P001','__ALL__','G001','M',?,'Memo 2',?,"
            "'u001','2026-01-02T00:00:00','2026-01-02T00:00:00')",
            [seq, status],
        )
        db_conn.commit()

    def test_get_group_max_seq_tracks_latest(self, pipeline_env, db_conn):
        import modules.flow_gate.db.documents as db_d
        assert db_d.get_group_max_seq("G001") == 1
        self._insert_memo(db_conn)
        assert db_d.get_group_max_seq("G001") == 2

    def test_get_group_max_seq_empty_group(self, pipeline_env):
        import modules.flow_gate.db.documents as db_d
        assert db_d.get_group_max_seq("NO_SUCH_GROUP") == 0

    def test_recent_docs_anchored_at_parent_excludes_later_memo(self, pipeline_env, db_conn):
        """Documents the buggy anchor: before_seq=parent R seq drops the memo."""
        import modules.flow_gate.db.documents as db_d
        self._insert_memo(db_conn)
        parent_seq = 1  # R0001 is the group minimum
        recent = db_d.fetch_recent_group_docs(group_id="G001", before_seq=parent_seq, limit=5)
        ids = {r["doc_id"] for r in recent}
        assert ids == {"P001-G001-R0001"}
        assert "P001-G001-M0002" not in ids

    def test_recent_docs_anchored_at_group_max_includes_memo(self, pipeline_env, db_conn):
        """The fix: anchoring at group MAX(seq) surfaces the later memo."""
        import modules.flow_gate.db.documents as db_d
        self._insert_memo(db_conn)
        before = db_d.get_group_max_seq("G001")
        recent = db_d.fetch_recent_group_docs(group_id="G001", before_seq=before, limit=5)
        ids = {r["doc_id"] for r in recent}
        assert ids == {"P001-G001-R0001", "P001-G001-M0002"}
