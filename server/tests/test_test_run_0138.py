from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock


def _resp_json(resp) -> dict:
    return json.loads(bytes(resp.body).decode("utf-8"))


def test_parse_test_cases_success():
    from modules.flow_gate.services.test_run_service import parse_test_cases

    cases = parse_test_cases(
        """
# TS

## 테스트 케이스

### TC-01: ok
- cmd: `python -c "print(1)"`
- 기대: exits zero

### TC-02: fail path
- cmd: pytest tests/test_x.py -q
- 기대: exits zero
"""
    )

    assert [c["case_no"] for c in cases] == ["TC-01", "TC-02"]
    assert cases[0]["cmd"] == 'python -c "print(1)"'
    assert cases[1]["title"] == "fail path"


def test_parse_test_cases_rejects_missing_cmd():
    from modules.flow_gate.services.test_run_service import TestCaseParseError, parse_test_cases

    try:
        parse_test_cases(
            """
## 테스트 케이스

### TC-01: bad
- 기대: exits zero
"""
        )
    except TestCaseParseError as exc:
        assert exc.code == "invalid_case_block"
        assert "cmd" in exc.detail
    else:
        raise AssertionError("expected parse error")


def test_parse_test_plan_with_setup_and_teardown():
    from modules.flow_gate.services.test_run_service import parse_test_plan

    plan = parse_test_plan(
        """
## 테스트 준비

- cmd: `python -m pip install -r requirements.txt --quiet`
- 기동: `python dev.py --port {PORT} --db {SCRATCH}/test.db`
- 대기: {PORT}

## 테스트 케이스

### TC-01: ok
- cmd: `python tests/remote/tc01.py --base http://127.0.0.1:{PORT}`
- 기대: exits zero

## 테스트 정리

- cmd: `python tools/cleanup.py --root {SCRATCH}`
"""
    )

    assert [step["kind"] for step in plan["setup"]] == ["setup", "service", "wait"]
    assert [step["case_no"] for step in plan["setup"]] == ["SETUP-1", "SETUP-2", "SETUP-3"]
    assert plan["cases"][0]["kind"] == "case"
    assert plan["teardown"][0]["kind"] == "teardown"
    assert plan["teardown"][0]["case_no"] == "CLEAN-1"


def test_migration_052_adds_test_run_schema_and_scope(all_migrations_db):
    conn = all_migrations_db
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "test_runs" in tables
    assert "test_run_cases" in tables
    run_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(test_runs)").fetchall()
    }
    case_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(test_run_cases)").fetchall()
    }
    assert "port" in run_columns
    assert "kind" in case_columns

    conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, "
        " action_scope, issued_to, created_at, expires_at) "
        "VALUES ('tok_test_run_scope', 'hash_test_run_scope', 'p1', '__SYSTEM__', "
        "NULL, 'flowgate.default.0138.0005-TS', 'test_run', 'usr_admin', "
        "datetime('now'), datetime('now','+8 hours'))"
    )
    row = conn.execute(
        "SELECT action_scope FROM tokens WHERE token_id='tok_test_run_scope'"
    ).fetchone()
    assert row["action_scope"] == "test_run"
    perm = conn.execute(
        "SELECT permission_id FROM permissions WHERE permission_id='perm_test_run'"
    ).fetchone()
    assert perm is not None
    conn.execute("DELETE FROM tokens WHERE token_id='tok_test_run_scope'")
    conn.commit()


def test_inbox_test_run_dry_run(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        inbox_routes.token_service,
        "verify",
        lambda _raw: {
            "token_id": "tok-1",
            "project": "flowgate",
            "issued_to": "user-1",
            "action_scope": "test_run",
            "doc_ref": "flowgate.default.0138.0005-TS",
            "dry_run_count": 0,
        },
    )
    monkeypatch.setattr(test_run_service, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda _id: {"doc_id": _id, "project_id": "flowgate"},
    )
    inc = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", inc)
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)

    resp = asyncio.run(
        inbox_routes._handle_test_run(
            MagicMock(),
            "raw",
            {
                "action": "test_run",
                "project": "flowgate",
                "doc_id": "flowgate.default.0138.0005-TS",
                "dry_run": True,
            },
        )
    )
    data = _resp_json(resp)
    assert resp.status_code == 200
    assert data["would_register"]["action"] == "test_run"
    inc.assert_called_once_with("tok-1")
    consume.assert_not_called()


def test_inbox_test_run_accepts_and_consumes(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        inbox_routes.token_service,
        "verify",
        lambda _raw: {
            "token_id": "tok-1",
            "project": "flowgate",
            "issued_to": "user-1",
            "action_scope": "test_run",
            "doc_ref": "flowgate.default.0138.0005-TS",
            "dry_run_count": 0,
        },
    )
    monkeypatch.setattr(test_run_service, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda _id: {"doc_id": _id, "project_id": "flowgate"},
    )
    create = MagicMock(return_value={
        "ok": True,
        "run_id": "trun_20260703_000001",
        "doc_id": "flowgate.default.0138.0005-TS",
        "status": "running",
        "case_total": 1,
    })
    monkeypatch.setattr(test_run_service, "validate_and_create_run", create)
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)

    resp = asyncio.run(
        inbox_routes._handle_test_run(
            MagicMock(),
            "raw",
            {
                "action": "test_run",
                "project": "flowgate",
                "doc_id": "flowgate.default.0138.0005-TS",
            },
        )
    )
    data = _resp_json(resp)
    assert resp.status_code == 202
    assert data["run_id"] == "trun_20260703_000001"
    create.assert_called_once()
    consume.assert_called_once_with(
        token_id="tok-1",
        project_id="flowgate",
        doc_id="flowgate.default.0138.0005-TS",
    )


def test_case_command_timeout_kills_process_tree(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    killed = []
    popen_kwargs = {}
    communicate_timeouts = []

    class FakeProc:
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            communicate_timeouts.append(timeout)
            if timeout == 0.01:
                raise test_run_service.subprocess.TimeoutExpired(
                    cmd="pytest -q",
                    timeout=timeout,
                    output=b"partial stdout",
                    stderr=b"partial stderr",
                )
            self.returncode = -9
            return (b"final stdout", b"final stderr")

        def poll(self):
            return self.returncode

    def fake_popen(_cmd, **kwargs):
        popen_kwargs.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(test_run_service, "CASE_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(test_run_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(test_run_service, "_kill_process_tree", lambda proc: killed.append(proc.pid))

    result, exit_code, output = test_run_service._run_case_command("pytest -q", tmp_path)

    assert result == "timeout"
    assert exit_code is None
    assert output == "final stdoutfinal stderr"
    assert killed == [4242]
    assert communicate_timeouts == [0.01, 5]
    assert popen_kwargs["shell"] is True
    assert popen_kwargs["stdout"] == test_run_service.subprocess.PIPE
    assert popen_kwargs["stderr"] == test_run_service.subprocess.PIPE
    if test_run_service.os.name == "nt":
        assert "creationflags" in popen_kwargs
    else:
        assert popen_kwargs["start_new_session"] is True
