"""F108-5: seconds persistence, legacy migration and effective command limits."""
import io
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import user_conversation_settings as settings_db
from modules.flow_gate.services import chat_doc_settings_service as settings
from modules.flow_gate.services import chat_command_service as commands
from modules.flow_gate.services import tr_self_check_executor as executor

MIGRATIONS = Path(__file__).resolve().parents[3] / "sql" / "migrations" / "sqlite"


class Store:
    def __init__(self, connection):
        self.db = connection
        self.db.row_factory = sqlite3.Row

    def _execute(self, sql, params=()):
        return self.db.execute(sql, params)

    def _fetch_one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    db = sqlite3.connect(str(migrated_sqlite_db("chat_limits_0108.db")), check_same_thread=False)
    value = Store(db)
    monkeypatch.setattr(settings_db, "get_store", lambda: value)
    yield value
    db.close()


@pytest.fixture
def client(store, monkeypatch):
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.documents.routers import conversation_turns
    monkeypatch.setattr(document_service, "get_document",
                        lambda doc: {"doc_id": doc, "type_code": "CH", "project_id": "p"})
    app = FastAPI()
    app.include_router(conversation_turns.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u"}
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("seconds", [None, 60, 61, 604800])
@pytest.mark.parametrize("command", [None, 0, 1, 1800])
def test_settings_round_trip(client, seconds, command):
    url = "/documents/chat/conversation/settings"
    response = client.patch(url, json={"timeout_seconds": seconds, "command_timeout_seconds": command})
    assert response.status_code == 200, response.text
    saved = client.get(url).json()["settings"]
    assert saved["timeout_seconds"] == seconds
    assert saved["command_timeout_seconds"] == command
    assert settings.get_settings("other", "chat")["timeout_seconds"] is None
    assert settings.get_settings("u", "other")["command_timeout_seconds"] is None


@pytest.mark.parametrize("field,value", [
    ("timeout_seconds", v) for v in (0, 59, 604801, -1, True, 60.5, "60")
] + [
    ("command_timeout_seconds", v) for v in (-1, 1801, True, 1.5, "1")
])
def test_settings_invalid_422(client, field, value):
    response = client.patch("/documents/chat/conversation/settings", json={field: value})
    assert response.status_code == 422
    assert response.json()["field"] == field


def test_legacy_patch_and_seconds_precedence(store):
    assert settings.save_settings("u", "chat", {"timeout_minutes": 31}, project_id=None)["timeout_seconds"] == 1860
    value = settings.save_settings("u", "chat", {"timeout_minutes": 31, "timeout_seconds": 61}, project_id=None)
    assert value["timeout_seconds"] == 61
    assert value["timeout_minutes"] == 2
    assert settings.save_settings("u", "chat", {"timeout_minutes": None}, project_id=None)["timeout_seconds"] is None


def test_upgrade_preserves_legacy_values_and_command_history():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE user_conversation_settings (user_id TEXT, timeout_minutes INTEGER);"
                     "INSERT INTO user_conversation_settings VALUES ('finite',31),('unlimited',NULL);")
    db.executescript((MIGRATIONS / "136_chat_command_execution.sql").read_text())
    db.executescript((MIGRATIONS / "137_chat_activity_anchor.sql").read_text())
    db.executescript((MIGRATIONS / "157_chat_command_output_truncation.sql").read_text())
    db.execute("INSERT INTO chat_command_requests "
               "(request_id,ai_run_id,doc_id,project_id,group_id,program,args_json,cwd_relative,"
               "timeout_seconds,category,policy,status,created_at,updated_at,stdout_tail,stdout_truncated,anchor_seq) "
               "VALUES ('r','a','d','p','g','echo','[]','.',300,'read','reject','rejected','t','t','kept',1,9)")
    db.commit()
    db.executescript((MIGRATIONS / "216_chat_execution_limits.sql").read_text())
    assert db.execute("SELECT timeout_seconds FROM user_conversation_settings ORDER BY user_id").fetchall() == [(1860,), (None,)]
    assert db.execute("SELECT stdout_tail,stdout_truncated,anchor_seq FROM chat_command_requests").fetchone() == ("kept", 1, 9)
    db.execute("UPDATE chat_command_requests SET timeout_seconds=0")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE chat_command_requests SET timeout_seconds=-1")
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_chat_cmd_%'").fetchall()) == 4
    db.close()


@pytest.mark.parametrize("limit,ai,expected", [
    (None, None, 300), (None, 90, 90), (0, None, 0), (0, 90, 0),
    (600, None, 600), (60, 90, 60), (600, 90, 90), (1, None, 1), (1800, None, 1800),
])
def test_request_uses_requester_and_chat_settings(store, monkeypatch, tmp_path, limit, ai, expected):
    settings.save_settings("owner", "chat", {"command_timeout_seconds": limit}, project_id=None)
    settings.save_settings("other", "chat", {"command_timeout_seconds": 7}, project_id=None)
    monkeypatch.setattr(commands, "_authorize_run", lambda *a: None)
    monkeypatch.setattr(commands, "worktree_root", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(commands.policy, "resolve", lambda *a: None)
    monkeypatch.setattr(commands, "resolve_policy", lambda user: "reject")
    monkeypatch.setattr(commands.db, "create", lambda **kw: kw)
    monkeypatch.setattr(commands, "_run_closed", lambda *a: False)
    monkeypatch.setattr(commands, "_note_progress", lambda *a: None)
    monkeypatch.setattr(commands, "_notify", lambda *a: None)
    payload = {"program": "echo"}
    if ai is not None:
        payload["timeout_seconds"] = ai
    row = commands._create_request(
        {"issued_to": "owner", "doc_ref": "chat", "project_id": "p", "group_id": "g", "run_id": "run"},
        payload, token_id="token",
    )
    assert row["timeout_seconds"] == expected
    with pytest.raises(commands.policy.ChatCommandPolicyError):
        commands.policy.validate_request({"program": "echo", "timeout_seconds": 0})


def test_unlimited_result_wait_keeps_remaining_run_budget(monkeypatch):
    row = {"request_id": "r", "status": "running", "timeout_seconds": 0}
    monkeypatch.setattr(commands, "create_request", lambda *a: row)
    monkeypatch.setattr(commands, "ai_result", lambda row: row)
    waits = []
    def wait(request_id, seconds, *args):
        waits.append(seconds)
        return {**row, "status": "succeeded"}
    monkeypatch.setattr(commands, "wait_terminal", wait)
    commands.run_tool({}, {}, 100)
    assert 90 < waits[0] <= 95  # not 0 + 15


def test_approval_wait_consumes_budget_and_exhaustion_cancels(monkeypatch):
    row = {"request_id": "r", "status": "pending_approval", "timeout_seconds": 0}
    monkeypatch.setattr(commands, "create_request", lambda *a: row)
    monkeypatch.setattr(commands, "ai_result", lambda row: row)
    ticks = iter([0, 30])
    monkeypatch.setattr(commands.time, "monotonic", lambda: next(ticks))
    waits = []
    def wait(request_id, seconds, *args):
        waits.append(seconds)
        return {**row, "status": "running"}
    monkeypatch.setattr(commands, "wait_terminal", wait)
    cancellations = []
    def cancel(request_id, reason):
        cancellations.append(reason)
        return {**row, "status": "cancelled"}
    monkeypatch.setattr(commands, "_cancel", cancel)
    commands.run_tool({}, {}, 100)
    assert waits == [95, 65]
    assert cancellations == ["chat_command_run_budget_exhausted"]


@pytest.mark.parametrize("limit,cancel,expected", [(0, False, "success"), (1, False, "timeout"), (0, True, "cancel")])
def test_process_limit_and_cancel(tmp_path, limit, cancel, expected):
    script = tmp_path / "child.py"
    script.write_text("import time\ntime.sleep(1.5)\nprint('finished')\n")
    event = threading.Event()
    control, _ = executor.spawn([sys.executable, str(script)], tmp_path, dict(os.environ), limit, "limits-test")
    timer = None
    if cancel:
        def stop():
            event.set()
            control.cancel()
        timer = threading.Timer(.15, stop)
        timer.start()
    try:
        result = executor.wait(control, limit, event)
    finally:
        if timer:
            timer.join()
        control.close()
    assert result.timed_out == (expected == "timeout")
    assert result.cancelled == (expected == "cancel")
    if expected == "success":
        assert result.exit_code == 0 and "finished" in result.stdout_tail


def test_windows_wait_zero_is_unlimited():
    waits = []
    proc = SimpleNamespace(stdout=io.BytesIO(b"ok"), stderr=io.BytesIO(), returncode=0,
                           wait=lambda timeout: waits.append(timeout))
    result = executor.wait(SimpleNamespace(proc=proc, owner=object()), 0, threading.Event())
    assert waits == [None]
    assert result.exit_code == 0


@pytest.mark.parametrize("limit,ai,expected", [(None, None, 300), (0, 90, 0), (600, None, 600), (60, 90, 60), (600, 90, 90)])
def test_conversation_command_path(store, monkeypatch, tmp_path, limit, ai, expected):
    from flowgate.conversation import commands as modern
    settings.save_settings("owner", "chat", {"command_timeout_seconds": limit}, project_id=None)
    monkeypatch.setattr(modern, "_authorize_run", lambda *a: None)
    monkeypatch.setattr(modern, "_require_command_access", lambda *a: None)
    monkeypatch.setattr(modern, "worktree_root", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(modern.policy, "resolve", lambda *a: None)
    monkeypatch.setattr(modern, "resolve_policy", lambda user: modern.c.POLICY_REJECT)
    monkeypatch.setattr(modern.repo, "insert_command", lambda **kw: kw)
    monkeypatch.setattr(modern.repo, "get_command", lambda *a: None)
    monkeypatch.setattr(modern.activity, "resolve_run", lambda *a: None)
    monkeypatch.setattr(modern, "note_progress", lambda *a: None)
    monkeypatch.setattr(modern, "_notify", lambda *a: None)
    payload = {"program": "echo"}
    if ai is not None:
        payload["timeout_seconds"] = ai
    run = {"issued_to": "owner", "doc_ref": "chat", "project_id": "p", "group_id": "g", "run_id": "run"}
    assert modern._create_request(run, payload, token_id="token")["timeout_seconds"] == expected


def test_conversation_unlimited_wait_keeps_live_budget_checks(monkeypatch):
    from flowgate.conversation import commands as modern
    row = {"request_id": "r", "status": modern.c.RUNNING, "timeout_seconds": 0}
    run = {"run_id": "run"}
    monkeypatch.setattr(modern, "create_request", lambda *a: row)
    monkeypatch.setattr(modern, "ai_result", lambda row: row)
    def wait(request_id, max_wait, actual_run):
        assert max_wait == float("inf") and actual_run is run
        return {**row, "status": modern.c.SUCCEEDED}, modern.WAIT_TERMINAL
    monkeypatch.setattr(modern, "wait_for", wait)
    assert modern.run_tool(run, {})[0] == 200
