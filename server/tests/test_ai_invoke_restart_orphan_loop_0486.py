"""Restart-orphan document review loop recovery (flowgate.default.0486 T0019)."""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as service  # noqa: E402
from modules.flow_gate.services.ai_invoke import chain  # noqa: E402

MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
GROUP = "flowgate.default.0486"
DOC = "flowgate.default.0486.0019-T"
OWNER = "owner-0486"
OTHER = "other-0486"


class Store:
    def __init__(self, conn):
        self.conn = conn

    def _execute(self, sql, values=()):
        cursor = self.conn.execute(sql, values)
        self.conn.commit()
        return cursor

    def _execute_affected(self, sql, values=()):
        cursor = self.conn.execute(sql, values)
        self.conn.commit()
        return cursor.rowcount

    def _fetch_one(self, sql, values=()):
        row = self.conn.execute(sql, values).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, values=()):
        return [dict(row) for row in self.conn.execute(sql, values).fetchall()]

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


@pytest.fixture
def recovery_db(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "restart-orphan.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(doc_id TEXT PRIMARY KEY);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        CREATE TABLE ai_invoke_runs(
            run_id TEXT PRIMARY KEY, issued_to TEXT, group_id TEXT,
            started_at TEXT, finished_at TEXT
        );
    """)
    for name in (
        "091_ai_invoke_document_review_loops.sql",
        "092_ai_invoke_document_review_loop_live_run.sql",
        "102_ai_invoke_review_loop_card_dismissed.sql",
        "105_ai_invoke_document_review_loop_hold_stop_reason.sql",
        "106_ai_invoke_document_review_loop_restart_orphaned.sql",
    ):
        conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    conn.execute("INSERT INTO groups VALUES (?)", [GROUP])
    conn.execute("INSERT INTO documents VALUES (?)", [DOC])
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()
    store = Store(conn)
    monkeypatch.setattr(db_loops, "get_store", lambda: store)
    monkeypatch.setattr(db_runs, "get_store", lambda: store)
    monkeypatch.setattr(db_runs, "_row_to_payload", lambda row: dict(row))
    yield conn
    conn.close()


def loop_row(run_id, stage):
    return {
        "run_id": run_id, "group_id": GROUP, "doc_ref": DOC,
        "review_count": 2, "reviewer_provider_id": "reviewer",
        "review_criteria": "document_type_default", "rework_provider_id": "reworker",
        "rework_timeout_sec": 1800, "rework_message": "fix findings",
        "failure_restart_max_attempts": 1, "total_timeout_sec": 3600,
        "review_baseline_id": 0, "baseline_revision_no": 0,
        "starts_with_rework": stage == "rework",
        "started_at": "2026-09-10T00:00:00+00:00",
        "deadline_at": "2026-09-10T01:00:00+00:00",
        "round_no": 2, "current_stage": stage, "attempts_used": 1,
        "created_at": "2026-09-10T00:00:00+00:00",
        "updated_at": "2026-09-10T00:30:00+00:00",
    }


@pytest.mark.parametrize("stage", ["review", "rework"])
def test_crash_recovery_stops_active_loop_once_and_preserves_progress(recovery_db, stage):
    run_id = f"aiv_orphan_{stage}"
    db_loops.insert(loop_row(run_id, stage))
    before = db_loops.get(run_id)

    assert db_loops.stop_for_restart_orphan(run_id, at="2026-09-10T00:40:00+00:00") is True
    first = db_loops.get(run_id)
    assert (first["current_stage"], first["stop_reason"]) == ("stopped", "restart_orphaned")
    assert first["stop_detail"] == "worker lease orphaned by server restart"
    assert (first["round_no"], first["attempts_used"], first["created_at"]) == (
        before["round_no"], before["attempts_used"], before["created_at"]
    )

    assert db_loops.stop_for_restart_orphan(run_id, at="2099-01-01T00:00:00+00:00") is False
    assert db_loops.get(run_id) == first


def test_recovery_does_not_touch_absent_terminal_or_other_loop(recovery_db):
    active = loop_row("aiv_other", "review")
    terminal = loop_row("aiv_human_stop", "review")
    db_loops.insert(active)
    db_loops.insert(terminal)
    recovery_db.execute(
        "UPDATE ai_invoke_document_review_loops SET current_stage='stopped', "
        "stop_reason='review_verdict_hold', stop_detail='human hold' WHERE run_id=?",
        ["aiv_human_stop"],
    )
    recovery_db.commit()
    terminal_before = db_loops.get("aiv_human_stop")
    other_before = db_loops.get("aiv_other")

    assert db_loops.stop_for_restart_orphan("aiv_missing") is False
    assert db_loops.stop_for_restart_orphan("aiv_human_stop") is False
    assert db_loops.get("aiv_human_stop") == terminal_before
    assert db_loops.get("aiv_other") == other_before


def test_orphan_run_keeps_token_owner_and_drives_owner_scoped_card(monkeypatch, recovery_db):
    run_id = "aiv_owned_orphan"
    db_loops.insert(loop_row(run_id, "review"))
    captured = {}

    monkeypatch.setattr(db_runs, "get", lambda _run_id: None)
    monkeypatch.setattr(db_runs, "upsert", lambda row: captured.update(row))
    monkeypatch.setattr(db_tokens, "get_by_id", lambda token_id: {
        "token_id": token_id, "doc_ref": DOC, "issued_to": OWNER,
        "continuation_target_seq": None,
    })
    service._record_orphaned_lease_run({
        "run_id": run_id, "group_id": GROUP, "project_id": "flowgate",
        "token_id": "tok-owned", "acquired_at": "2026-09-10T00:00:00+00:00",
    }, "orphaned_by_restart")

    assert captured["issued_to"] == OWNER
    assert captured["token_id"] == "tok-owned"
    assert db_loops.get(run_id)["stop_reason"] == "restart_orphaned"

    recovery_db.execute(
        "INSERT INTO ai_invoke_runs(run_id,issued_to,group_id,started_at,finished_at) "
        "VALUES (?,?,?,?,?)",
        [run_id, captured["issued_to"], GROUP, captured["started_at"], captured["finished_at"]],
    )
    recovery_db.commit()
    assert [row["run_id"] for row in db_runs.list_review_loops_by_user(OWNER)] == [run_id]
    assert db_runs.list_review_loops_by_user(OTHER) == []

    monkeypatch.setattr(service, "_runs", {})
    monkeypatch.setattr(chain, "_finished_card_retention_minutes", lambda _user_id: 10_000_000)
    monkeypatch.setattr(chain.diagnostics, "_run_detail_from_row", lambda row: {
        "run_id": row["run_id"], "document_review_loop": db_loops.get(row["run_id"])
    })
    assert [row["run_id"] for row in service.active_all(OWNER)["runs"]] == [run_id]
    assert service.active_all(OTHER)["runs"] == []


def test_loop_failure_is_logged_without_losing_orphan_run(monkeypatch, caplog):
    captured = {}
    monkeypatch.setattr(db_runs, "get", lambda _run_id: None)
    monkeypatch.setattr(db_runs, "upsert", lambda row: captured.update(row))
    monkeypatch.setattr(db_tokens, "get_by_id", lambda _token_id: {
        "doc_ref": DOC, "issued_to": OWNER, "continuation_target_seq": None,
    })
    monkeypatch.setattr(
        db_loops, "stop_for_restart_orphan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("loop db down")),
    )
    with caplog.at_level("WARNING"):
        service._record_orphaned_lease_run({
            "run_id": "aiv_loop_failure", "group_id": GROUP, "project_id": "flowgate",
            "token_id": "tok-owned", "acquired_at": "2026-09-10T00:00:00+00:00",
        }, "orphaned_by_restart")
    assert captured["run_id"] == "aiv_loop_failure"
    assert "review-loop stop failed" in caplog.text


def test_106_three_dialects_widen_check_and_preserve_schema_contract():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (MIGRATIONS / dialect /
               "106_ai_invoke_document_review_loop_restart_orphaned.sql").read_text(encoding="utf-8")
        assert "restart_orphaned" in sql
        assert "review_verdict_hold" in sql
        assert "ai_invoke_document_review_loops_stop_reason_check" in sql or (
            "CREATE TABLE ai_invoke_document_review_loops_new" in sql
            and "idx_aidrl_group_updated" in sql
            and "idx_aidrl_doc_updated" in sql
        )


def test_existing_restart_orphan_run_retries_loop_cas_without_overwriting_run(monkeypatch):
    calls = []
    monkeypatch.setattr(db_runs, "get", lambda _run_id: {
        "run_id": "aiv_partial", "end_reason": "orphaned_by_restart"
    })
    monkeypatch.setattr(db_runs, "upsert", lambda _row: pytest.fail("must not rewrite run"))
    monkeypatch.setattr(db_loops, "stop_for_restart_orphan", lambda run_id: calls.append(run_id) or True)
    service._record_orphaned_lease_run({"run_id": "aiv_partial"}, "orphaned_by_restart")
    assert calls == ["aiv_partial"]


def test_manual_orphan_record_does_not_claim_restart_loop_reason(monkeypatch):
    captured, calls = {}, []
    monkeypatch.setattr(db_runs, "get", lambda _run_id: None)
    monkeypatch.setattr(db_runs, "upsert", lambda row: captured.update(row))
    monkeypatch.setattr(db_loops, "stop_for_restart_orphan", lambda *args, **kwargs: calls.append(args))
    service._record_orphaned_lease_run({
        "run_id": "aiv_manual", "group_id": GROUP, "project_id": "flowgate",
        "acquired_at": "2026-09-10T00:00:00+00:00",
    }, "orphaned_by_manual_release")
    assert captured["end_reason"] == "orphaned_by_manual_release"
    assert calls == []
