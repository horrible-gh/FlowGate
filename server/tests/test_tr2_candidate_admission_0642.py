"""0642 TR2 candidate admission and post-commit registry reflection contracts."""
from __future__ import annotations

import pytest

from modules.flow_gate.documents import tr2_command_admission as admission
from modules.flow_gate.documents import tr2_service
from modules.flow_gate.services import test_command_service as service


def _registry(monkeypatch, rows=()):
    table = {row["command"]: dict(row) for row in rows}
    monkeypatch.setattr(admission.registry, "find_by_command", lambda _p, cmd: table.get(cmd))
    monkeypatch.setattr(admission.commands, "current_os", lambda: "posix")
    return table


def _row(command="pytest -q", *, status="active", origin="manual", os_name=None):
    return {"id": 7, "command": command, "status": status,
            "origin": origin, "verified_os": os_name}


def test_registered_and_candidate_preserve_gate_order(monkeypatch):
    _registry(monkeypatch, [_row()])
    result = admission.classify_gate_commands("p", [" pytest   -q ", "cd client && npm test"])
    assert [i["state"] for i in result["commands"]] == ["registered", "candidate"]
    assert result["commands"][1]["shell_complex"] is True
    assert result["candidate_count"] == 1 and result["all_candidate"] is False
    assert len(admission.require_admitted(result)) == 2


@pytest.mark.parametrize("count", [1, 2, 3])
def test_all_candidate_bootstrap(monkeypatch, count):
    _registry(monkeypatch)
    result = admission.classify_gate_commands("p", [f"test {i}" for i in range(count)])
    assert result["candidate_count"] == count and result["all_candidate"]
    assert len(admission.require_admitted(result)) == count


def test_distinct_candidate_limit_and_duplicate_execution(monkeypatch):
    _registry(monkeypatch)
    result = admission.classify_gate_commands("p", ["a", "a", "b", "c"])
    assert result["candidate_count"] == 3
    assert len(admission.require_admitted(result)) == 4
    denied = admission.classify_gate_commands("p", ["a", "b", "c", "d"])
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_spec_invalid"):
        admission.require_admitted(denied)


def test_suppressed_and_registered_trust(monkeypatch):
    table = _registry(monkeypatch, [_row(status="suppressed")])
    result = admission.classify_gate_commands("p", ["pytest -q"])
    assert result["commands"][0]["state"] == "suppressed"
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_validation_command_unapproved"):
        admission.require_admitted(result)
    table["pytest -q"] = _row(origin="auto")
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_validation_command_unverified"):
        admission.require_admitted(admission.classify_gate_commands("p", ["pytest -q"]))
    table["pytest -q"] = _row(origin="tr2")
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_validation_command_unverified"):
        admission.require_admitted(admission.classify_gate_commands("p", ["pytest -q"]))
    table["pytest -q"] = _row(os_name="nt")
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_validation_command_os_mismatch"):
        admission.require_admitted(admission.classify_gate_commands("p", ["pytest -q"]))
    table["pytest -q"] = _row()
    assert admission.require_admitted(admission.classify_gate_commands("p", ["pytest -q"]))


@pytest.mark.parametrize("char", ["\x00", "\r", "\n"])
def test_candidate_control_char_denied(monkeypatch, char):
    _registry(monkeypatch)
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_spec_invalid"):
        admission.require_admitted(admission.classify_gate_commands("p", ["a" + char + "b"]))


@pytest.mark.parametrize("command", ["cd client && npm test", "a || b", "a | b",
                                         "a > out", "a < in", "a; b", "`a`", "$(a)", "a & b"])
def test_shell_grammar_warns_but_admits(monkeypatch, command):
    _registry(monkeypatch)
    result = admission.classify_gate_commands("p", [command])
    assert result["commands"][0]["shell_complex"]
    assert admission.require_admitted(result)


def test_fingerprint_tracks_order_command_and_registry(monkeypatch):
    table = _registry(monkeypatch)
    first = admission.classify_gate_commands("p", ["a", "b"])["fingerprint"]
    assert admission.classify_gate_commands("p", ["b", "a"])["fingerprint"] != first
    assert admission.classify_gate_commands("p", ["a", "c"])["fingerprint"] != first
    table["a"] = _row("a")
    assert admission.classify_gate_commands("p", ["a", "b"])["fingerprint"] != first
    table["a"] = _row("a", status="suppressed")
    assert admission.classify_gate_commands("p", ["a", "b"])["commands"][0]["state"] == "suppressed"


def test_reflection_registers_and_preserves_manual_and_suppressed(monkeypatch):
    table = {}
    inserted = []
    monkeypatch.setattr(service, "current_os", lambda: "posix")
    monkeypatch.setattr(service.db, "find_by_command", lambda _p, cmd: table.get(cmd))
    monkeypatch.setattr(service.db, "count_active", lambda _p: sum(
        row["status"] == "active" for row in table.values()))
    def insert(_p, cmd, desc, origin, timestamp, **kwargs):
        assert origin == "tr2" and timestamp and kwargs["verified_os"] == "posix"
        row = {"id": len(table) + 1, "command": cmd, "status": "active",
               "origin": origin, "last_success_at": timestamp, "verified_os": "posix"}
        table[cmd] = row
        inserted.append(cmd)
        return row
    monkeypatch.setattr(service.db, "insert", insert)
    def update(_p, row_id, fields):
        row = next(row for row in table.values() if row["id"] == row_id)
        row.update(fields)
        return row
    monkeypatch.setattr(service.db, "update_success_if_active",
                        lambda _p, row_id, when, os_name: update(
                            _p, row_id, {"last_success_at": when, "verified_os": os_name}))
    table["manual"] = {**_row("manual"), "id": 10}
    table["blocked"] = {**_row("blocked", status="suppressed"), "id": 11}
    results = [{"command": cmd, "exit_code": 0, "timed_out": False}
               for cmd in ["new", "new", "manual", "blocked"]]
    service.reflect_tr2_validation_success("p", "p.m.0642.0007-TR2", "attempt", "sha", results)
    assert inserted == ["new"]
    assert table["new"]["origin"] == "tr2"
    assert table["manual"]["origin"] == "manual" and table["manual"]["verified_os"] == "posix"
    assert table["blocked"]["status"] == "suppressed" and table["blocked"]["verified_os"] is None


def test_reflection_unique_race_keeps_winning_origin(monkeypatch):
    table = {}
    monkeypatch.setattr(service, "current_os", lambda: "posix")
    monkeypatch.setattr(service.db, "find_by_command", lambda _p, cmd: table.get(cmd))
    monkeypatch.setattr(service.db, "count_active", lambda _p: 0)
    def insert(_p, cmd, *_args, **_kwargs):
        table[cmd] = {**_row(cmd), "id": 22}
        raise RuntimeError("unique conflict")
    monkeypatch.setattr(service.db, "insert", insert)
    monkeypatch.setattr(service.db, "update_success_if_active",
                        lambda _p, _id, when, os_name: table["race"].update(
                            {"last_success_at": when, "verified_os": os_name}))
    service.reflect_tr2_validation_success("p", "d", "a", "sha",
                                           [{"command": "race", "exit_code": 0}])
    assert table["race"]["origin"] == "manual"
    assert table["race"]["verified_os"] == "posix"


def test_origin_migration_three_dialects_and_sqlite_rows():
    import sqlite3
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    sqlite_sql = (root / "sqlite" / "126_tr2_test_command_origin.sql").read_text()
    postgres_sql = (root / "postgres" / "126_tr2_test_command_origin.sql").read_text()
    mysql_sql = (root / "mysql" / "126_tr2_test_command_origin.sql").read_text()
    assert "'tr2'" in postgres_sql and "DROP CONSTRAINT" in postgres_sql
    assert "origin=tr2" in mysql_sql
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE projects(project_id TEXT PRIMARY KEY);
        INSERT INTO projects VALUES ('p');
        CREATE TABLE project_test_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            command TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'auto')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suppressed')),
            last_success_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            verified_os TEXT, UNIQUE(project, command));
        INSERT INTO project_test_commands
            (id,project,command,description,origin,status,last_success_at,
             created_at,updated_at,verified_os)
        VALUES (7,'p','old','desc','manual','suppressed',NULL,'c','u',NULL);
        CREATE INDEX idx_project_test_commands_lookup
            ON project_test_commands(project,status);
    """)
    connection.executescript(sqlite_sql)
    connection.execute("INSERT INTO project_test_commands "
                       "(project,command,description,origin,status,created_at,updated_at) "
                       "VALUES ('p','new','','tr2','active','c','u')")
    assert connection.execute("SELECT id, status, origin FROM project_test_commands "
                              "WHERE command='old'").fetchone() == (7, "suppressed", "manual")
    assert connection.execute("SELECT origin FROM project_test_commands "
                              "WHERE command='new'").fetchone() == ("tr2",)
    connection.close()


def test_reflection_case_insensitive_unique_race_does_not_update_other_command(monkeypatch):
    other = {**_row("PYTEST -Q"), "last_success_at": "old"}
    updates = []
    monkeypatch.setattr(service, "current_os", lambda: "posix")
    monkeypatch.setattr(service.db, "find_by_command", lambda _p, _cmd: other)
    monkeypatch.setattr(service.db, "count_active", lambda _p: 0)
    monkeypatch.setattr(service.db, "insert", lambda *_a, **_kw: (_ for _ in ()).throw(
        RuntimeError("unique conflict")))
    monkeypatch.setattr(service.db, "update_success_if_active",
                        lambda *_a: updates.append(_a))
    service.reflect_tr2_validation_success("p", "d", "a", "sha",
                                           [{"command": "pytest -q", "exit_code": 0}])
    assert updates == []
    assert other["last_success_at"] == "old"


def test_reflection_case_insensitive_initial_lookup_does_not_update_other_command(monkeypatch):
    other = {**_row("PYTEST -Q"), "last_success_at": "old"}
    updates = []
    monkeypatch.setattr(service, "current_os", lambda: "posix")
    monkeypatch.setattr(service.db, "find_by_command", lambda _p, _cmd: other)
    monkeypatch.setattr(service.db, "update_success_if_active",
                        lambda *_a: updates.append(_a))
    service.reflect_tr2_validation_success("p", "d", "a", "sha",
                                           [{"command": "pytest -q", "exit_code": 0}])
    assert updates == []
    assert other["last_success_at"] == "old"


@pytest.fixture
def approval_flow(monkeypatch, tmp_path):
    """Exercise approve() with a transactional finalize and a shared registry."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from modules.flow_gate.documents import tr2_approval_service as approval
    from modules.flow_gate.services import workflow_rework_service

    doc = {"doc_id": "p.default.0642.0001-TR2", "id": 1, "type_code": "TR2",
           "project_id": "p", "group_id": "g", "revision_no": 1,
           "doc_review_status": "pending_review"}
    spec = {"termination": "ready_to_apply", "edits": [{"file": "a.txt"}],
            "gate": {"commands": ["pytest -q"]}}
    body = {"baseline_fingerprint": "sha256:" + "a" * 64, "edit_spec": spec}
    rows = {}
    state = {"failure": None, "rollbacks": [], "finished": [], "callbacks": [],
             "attempt": {"attempt_id": "attempt", "pre_apply_head_sha": "head",
                         "backup_bundle_id": "backup"}}
    monkeypatch.setattr(approval, "in_transaction", lambda: False)
    monkeypatch.setattr(approval, "check_permission", lambda *_a: True)
    monkeypatch.setattr(approval, "assert_group_mutation_allowed", lambda *_a: None)
    monkeypatch.setattr(approval, "get_doc_review_rule", lambda *_a: "approved")
    monkeypatch.setattr(approval.db_docs, "get_by_id", lambda *_a: doc)
    monkeypatch.setattr(approval.db_docs, "update_review_cas",
                        lambda *_a: doc.update(doc_review_status="approved") or True)
    monkeypatch.setattr(approval.db_attempts, "recovery_required", lambda *_a: False)
    monkeypatch.setattr(approval.db_attempts, "in_progress", lambda *_a: None)
    monkeypatch.setattr(approval.db_attempts, "latest_success", lambda *_a: None)
    monkeypatch.setattr(approval.db_attempts, "create", lambda **_kw: state["attempt"])
    monkeypatch.setattr(approval.db_attempts, "update",
                        lambda _id, **kw: state["attempt"].update(kw) or state["attempt"])
    monkeypatch.setattr(approval.db_attempts, "by_id", lambda *_a: state["attempt"])
    monkeypatch.setattr(approval.db_attempts, "finish",
                        lambda _id, **kw: state["finished"].append(kw))
    @contextmanager
    def source_lock(*_a, **_kw):
        yield SimpleNamespace(root=tmp_path, project_id="p")
    monkeypatch.setattr(approval.tr2_precheck, "source_lock", source_lock)
    monkeypatch.setattr(approval.tr2_precheck, "authoritative_precheck",
                        lambda *_a, **_kw: {"spec": spec,
                                            "evaluation": {"live_fingerprint": "live"}})
    monkeypatch.setattr(approval.tr2, "load_current", lambda *_a: body)
    monkeypatch.setattr(approval.tr2, "validate", lambda value, **_kw: value)
    monkeypatch.setattr(approval.tr2, "canonicalize", lambda value: value)
    monkeypatch.setattr(approval.tr2, "spec_fingerprint",
                        lambda value: "spec:" + ",".join(value["gate"]["commands"]))
    monkeypatch.setattr(approval.tr2, "target_set", lambda *_a: ["a.txt"])
    monkeypatch.setattr(approval, "_head", lambda *_a: "head")
    monkeypatch.setattr(approval, "_backup_root", lambda *_a: tmp_path)
    monkeypatch.setattr(approval.adapter, "create_backup", lambda *_a, **_kw: "backup")
    monkeypatch.setattr(approval.adapter, "apply_all", lambda *_a: {"paths": ["a.txt"]})
    monkeypatch.setattr(approval, "_rollback",
                        lambda *_a: state["rollbacks"].append(state["failure"]))
    def run_validation(_root, commands, _attempt_id):
        results = [{**item, "exit_code": 0, "timed_out": False} for item in commands]
        return {"status": "failed" if state["failure"] == "validation" else "passed",
                "commands": results}
    monkeypatch.setattr(approval, "_run_validation", run_validation)
    def commit(*_a):
        if state["failure"] == "git":
            raise RuntimeError("git commit failed")
        return "sha", ["a.txt"]
    monkeypatch.setattr(approval, "_commit_exact", commit)
    def record_commit(**_kw):
        if state["failure"] == "finalize":
            raise RuntimeError("finalize failed")
        return {"id": 1}
    monkeypatch.setattr(approval.db_ledger, "record_commit", record_commit)
    monkeypatch.setattr(approval.tr2, "effective_head_for", lambda *_a: None)
    monkeypatch.setattr(approval, "log_state_changed", lambda **_kw: None)
    monkeypatch.setattr(workflow_rework_service,
                        "clear_return_point_if_complete", lambda *_a: None)
    @contextmanager
    def transaction():
        previous = doc["doc_review_status"]
        try:
            yield
        except Exception:
            doc["doc_review_status"] = previous
            state["callbacks"].clear()
            raise
        callbacks, state["callbacks"] = state["callbacks"], []
        for callback in callbacks:
            callback()
    monkeypatch.setattr(approval, "get_store",
                        lambda: SimpleNamespace(transaction=transaction))
    monkeypatch.setattr(approval, "after_commit",
                        lambda callback: state["callbacks"].append(callback))
    monkeypatch.setattr(service, "current_os", lambda: "posix")
    monkeypatch.setattr(admission.commands, "current_os", lambda: "posix")
    def find(_project, cmd):
        return next((r for r in rows.values()
                     if r["command"].lower() == cmd.lower()), None)
    monkeypatch.setattr(service.db, "find_by_command", find)
    monkeypatch.setattr(service.db, "count_active", lambda *_a: len(rows))
    def insert(_project, cmd, _description, origin, timestamp, **kw):
        if find(_project, cmd):
            raise RuntimeError("unique conflict")
        row = {"id": len(rows) + 1, "command": cmd, "origin": origin,
               "status": kw["status"], "last_success_at": timestamp,
               "verified_os": kw["verified_os"]}
        rows[cmd] = row
        return row
    monkeypatch.setattr(service.db, "insert", insert)
    def update(_project, row_id, timestamp, os_name):
        row = next(r for r in rows.values() if r["id"] == row_id)
        if row["status"] == "active":
            row.update(last_success_at=timestamp, verified_os=os_name)
    monkeypatch.setattr(service.db, "update_success_if_active", update)
    return approval, doc, spec, body, rows, state


def _approve_flow(flow, **kwargs):
    approval, doc, *_rest = flow
    return approval.approve(doc_id=doc["doc_id"], actor_user_id="u",
                            user_permissions={"document.approve"},
                            mutation_principal=object(), **kwargs)


def test_command_change_rejected_by_spec_fingerprint(approval_flow, monkeypatch):
    approval, _doc, spec, body, _rows, state = approval_flow
    old = approval.tr2.spec_fingerprint(spec)
    body["edit_spec"] = {**spec, "gate": {"commands": ["pytest -x"]}}
    changed = approval.tr2.spec_fingerprint(body["edit_spec"])
    assert changed != old
    def precheck(*_a, **kw):
        if kw["expected_spec_fingerprint"] != changed:
            raise AssertionError("spec fingerprint must follow changed command")
        raise tr2_service.Tr2ValidationError("tr2_spec_changed", "spec_fingerprint", {})
    monkeypatch.setattr(approval.tr2_precheck, "authoritative_precheck", precheck)
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_spec_changed"):
        _approve_flow(approval_flow)
    assert approval.tr2.spec_fingerprint(body["edit_spec"]) != old
    assert state["rollbacks"] == []


def test_approve_rejects_stale_registry_admission_fingerprint(approval_flow):
    _approval, _doc, spec, _body, rows, state = approval_flow
    old = admission.classify_gate_commands("p", spec["gate"]["commands"])["fingerprint"]
    rows["pytest -q"] = {**_row(), "id": 1}
    with pytest.raises(tr2_service.Tr2ValidationError) as exc:
        _approve_flow(approval_flow, expected_command_admission_fingerprint=old)
    assert exc.value.code == "tr2_spec_changed"
    assert "command_admission_fingerprint" in str(exc.value)
    assert state["rollbacks"] == []


@pytest.mark.parametrize("failure", ["validation", "git", "finalize"])
def test_approve_failure_rolls_back_without_registry_reflection(approval_flow, failure):
    _approval, doc, _spec, _body, rows, state = approval_flow
    state["failure"] = failure
    with pytest.raises((RuntimeError, tr2_service.Tr2ValidationError)):
        _approve_flow(approval_flow)
    assert state["rollbacks"] == [failure]
    assert rows == {}
    assert doc["doc_review_status"] == "pending_review"
    assert state["callbacks"] == []


def test_approve_full_success_registers_candidate_after_commit(approval_flow):
    _approval, doc, _spec, _body, rows, state = approval_flow
    assert _approve_flow(approval_flow)["doc_review_status"] == "approved"
    row = rows["pytest -q"]
    assert row["origin"] == "tr2" and row["status"] == "active"
    assert row["verified_os"] == "posix" and row["last_success_at"]
    assert state["finished"][0]["state"] == "succeeded"


def test_approve_full_success_updates_registered_without_changing_origin(approval_flow):
    _approval, _doc, _spec, _body, rows, _state = approval_flow
    rows["pytest -q"] = {**_row(), "id": 1, "last_success_at": None}
    _approve_flow(approval_flow)
    assert rows["pytest -q"]["origin"] == "manual"
    assert rows["pytest -q"]["verified_os"] == "posix"
    assert rows["pytest -q"]["last_success_at"]


def test_approve_suppressed_race_does_not_revive(approval_flow, monkeypatch):
    approval, _doc, _spec, _body, rows, _state = approval_flow
    original = approval._run_validation
    def suppress_after_validation(root, commands, attempt_id):
        result = original(root, commands, attempt_id)
        rows["pytest -q"] = {**_row(status="suppressed"), "id": 1,
                             "last_success_at": None}
        return result
    monkeypatch.setattr(approval, "_run_validation", suppress_after_validation)
    _approve_flow(approval_flow)
    assert rows["pytest -q"]["status"] == "suppressed"
    assert rows["pytest -q"]["last_success_at"] is None


def test_approve_candidate_unique_race_succeeds(approval_flow, monkeypatch):
    approval, doc, _spec, _body, rows, _state = approval_flow
    original = service.db.insert
    def concurrent_insert(project, cmd, description, origin, timestamp, **kw):
        rows[cmd] = {**_row(cmd), "id": 3, "last_success_at": None}
        raise RuntimeError("unique conflict")
    monkeypatch.setattr(service.db, "insert", concurrent_insert)
    assert _approve_flow(approval_flow)["doc_review_status"] == "approved"
    assert rows["pytest -q"]["origin"] == "manual"
    assert rows["pytest -q"]["verified_os"] == "posix"
    assert rows["pytest -q"]["last_success_at"]

def test_spec_fingerprint_changes_with_command_string():
    original = {"gate": {"commands": ["pytest -q"]}}
    changed = {"gate": {"commands": ["pytest -x"]}}
    assert tr2_service.spec_fingerprint(original) != tr2_service.spec_fingerprint(changed)


def test_postgres_origin_constraint_name_matches_055_inline_check():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "postgres"
    initial = (root / "055_project_test_commands.sql").read_text(encoding="utf-8")
    upgrade = (root / "126_tr2_test_command_origin.sql").read_text(encoding="utf-8")
    assert "origin          TEXT" in initial
    assert "CHECK (origin IN ('manual', 'auto'))" in initial
    assert "CONSTRAINT project_test_commands_origin_check" in upgrade
    assert "CHECK (origin IN ('manual', 'auto', 'tr2'))" in upgrade