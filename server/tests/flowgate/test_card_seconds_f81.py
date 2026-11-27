"""F81 seconds contract: actual settings routes, chat writes, SQLite migration and execution sinks."""
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_fg_card_settings import (
    FA, FB, db_path, decided, env, get_settings, legacy_cards, node, put_draft, setting,
)
from test_chat_card_roles_v02gui_0028 import guard, _caller
from test_chat_card_run_settings_v02gui_0032 import _place, _setting_audit


@pytest.fixture(autouse=True)
def preserve_generated_environment():
    name = "FLOWGATE_AGENT_PEPPER_ID"
    before = os.environ.get(name)
    yield
    if before is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = before


def test_seconds_write_read_legacy_and_clamp(env):
    from flowgate.card_settings import fields, verify_limits
    key = put_draft(env, FB, 0, [node("tmp_work", "T")])["key_map"]["tmp_work"]
    shown = get_settings(env, FB, key)["fields"]
    assert shown["time_limit_sec"]["value"] == 14400
    assert shown["verify_run_sec"]["value"] == 60
    assert "time_limit" not in shown and "verify_run_minutes" not in shown
    result = setting(env, FB, key, "time_limit", 45)
    assert result["fields"]["time_limit_sec"]["value"] == 2700
    assert result["clamped"] is False
    result = setting(env, FB, key, "verify_run_sec", 1901)
    assert result["clamped"] is True and result["fields"]["verify_run_sec"]["value"] == 1800
    assert verify_limits.resolve_verify_limits(FB, key).run_timeout_sec == 1800
    result = setting(env, FB, key, "time_limit_sec", 1)
    assert result["clamped"] is True and result["fields"]["time_limit_sec"]["value"] == 1800
    assert fields.run_plan(fields.RUN, {"time_limit": 45})["step_timeout_sec"] == 2700
    assert fields.run_plan(fields.PLAN, {"time_limit_sec": 2345})["step_timeout_sec"] == 2345


@pytest.mark.parametrize("value", [True, 1.5, "120"])
def test_seconds_reject_non_integer(value):
    from flowgate.card_settings import fields as f
    with pytest.raises(f.InvalidValue):
        f.check_value(f.field_of(f.TEST, "auto_case_timeout_sec"), value,
                      repeat_choices=(), team_exists=lambda _: True, ai_exists=lambda _: True)


def test_ts_defaults_and_mode_preserves_count(env):
    key = put_draft(env, FB, 0, [node("tmp_test", "TS")])["key_map"]["tmp_test"]
    shown = get_settings(env, FB, key)["fields"]
    assert [shown[n]["value"] for n in ("auto_case_timeout_sec", "ai_judge_mode", "ai_judge_run_sec", "ai_judge_runs")] == [120, "case_count", 120, 5]
    setting(env, FB, key, "ai_judge_runs", 9)
    setting(env, FB, key, "ai_judge_mode", "fixed")
    shown = setting(env, FB, key, "ai_judge_mode", "case_count")["fields"]
    assert shown["ai_judge_runs"]["value"] == 9
    assert "verify_run_sec" not in shown


def test_chat_placement_patch_and_audit(env, decided, guard):
    from flowgate.chat_control import card_roles
    cards = _place([{"label": "work", "settings": {"time_limit": 240, "verify_run_minutes": 1}},
                    {"label": "test", "doc_type": "TS", "settings": {"auto_case_timeout_sec": 5,
                     "ai_judge_mode": "fixed", "ai_judge_run_sec": 150, "ai_judge_runs": 7}}])["cards"]
    assert cards[0]["settings"]["time_limit_sec"] == 14400
    assert cards[1]["clamped"] is True
    key = cards[1]["node_key"]
    read = card_roles.view(_caller(), key)
    assert read["settings"]["auto_case_timeout_sec"]["value"] == 60
    changed = card_roles.change(_caller(), key, {"settings": {"ai_judge_mode": "case_count", "ai_judge_run_sec": 5000},
                                                "expected_settings_version": read["settings_version"]})
    assert changed["clamped"] is True
    assert changed["settings"]["ai_judge_run_sec"]["value"] == 1800
    assert changed["settings"]["ai_judge_runs"]["value"] == 7
    assert {r["field"] for r in _setting_audit(key)} >= {"auto_case_timeout_sec", "ai_judge_mode", "ai_judge_run_sec", "ai_judge_runs"}


def test_migration_converts_rows_and_audit_without_double_conversion():
    root = Path(__file__).resolve().parents[2] / "sql/migrations/sqlite"
    db = sqlite3.connect(":memory:")
    db.executescript("""
    CREATE TABLE groups(group_id TEXT PRIMARY KEY);
    INSERT INTO groups VALUES ('g');
    CREATE TABLE graph_node_settings(group_id TEXT, node_key TEXT, field TEXT, value_json TEXT,
        updated_at TEXT, updated_by TEXT, PRIMARY KEY(group_id,node_key,field));
    CREATE TABLE chat_card_setting_audit(audit_id TEXT PRIMARY KEY, group_id TEXT, node_key TEXT,
        field TEXT, before_json TEXT, after_json TEXT, changed INTEGER, settings_version INTEGER,
        token_id TEXT, ai_run_id TEXT, chat_doc_id TEXT, actor_user_id TEXT, created_at TEXT);
    INSERT INTO graph_node_settings VALUES ('g','n','time_limit','240','now','u');
    INSERT INTO graph_node_settings VALUES ('g','n','verify_run_minutes','1','now','u');
    INSERT INTO graph_node_settings VALUES ('g','both','time_limit','30','now','u');
    INSERT INTO graph_node_settings VALUES ('g','both','time_limit_sec','2500','now','u');
    """)
    db.execute("INSERT INTO chat_card_setting_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ('a','g','n','verify_run_minutes',json.dumps({'value': 1, 'stored': False}),
                json.dumps({'value': 3, 'stored': True}),1,1,None,None,'ch','u','now'))
    db.commit()
    migration = (root / "216_card_settings_seconds.sql").read_text()
    for _ in range(2):
        db.executescript(migration)
        rows = {(n,f): json.loads(v) for n,f,v in db.execute("SELECT node_key,field,value_json FROM graph_node_settings")}
        assert rows == {('n','time_limit_sec'):14400,('n','verify_run_sec'):60,('both','time_limit_sec'):2500}
        field,before,after = db.execute("SELECT field,before_json,after_json FROM chat_card_setting_audit").fetchone()
        assert field == 'verify_run_sec' and json.loads(before)['value'] == 60 and json.loads(after)['value'] == 180
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE chat_card_setting_audit SET field='unknown'")


def test_execution_budget_counts_only_non_automatic_cases(monkeypatch):
    from modules.flow_gate.services import test_run_service as runner
    from flowgate.card_documents import repository as docs
    from flowgate.card_settings import repository
    doc = {'group_id':'g','doc_id':'ts'}
    stored = {}
    monkeypatch.setattr(docs, 'rows', lambda _: [{'node_key':'n','doc_id':'ts','slot':'scenario'}])
    monkeypatch.setattr(repository, 'values', lambda *_: stored)
    cases = [{'execution_mode': x} for x in ('automated','manual','ai','external')]
    budget = runner.execution_time_settings(doc, cases)
    assert budget['ai_judge_runs'] == 3 and budget['ai_judge_timeout_sec'] == 360
    assert runner.execution_time_settings(doc, cases[:1])['ai_judge_runs'] == 1
    stored.update(ai_judge_mode='fixed', ai_judge_runs=5, ai_judge_run_sec=150, auto_case_timeout_sec=90)
    budget = runner.execution_time_settings(doc, cases)
    assert budget['ai_judge_timeout_sec'] == 750 and budget['auto_case_timeout_sec'] == 90
    stored['ai_judge_mode'] = 'case_count'
    assert runner.execution_time_settings(doc, [])['ai_judge_runs'] == 1
    assert stored['ai_judge_runs'] == 5


def test_automatic_adapter_uses_card_timeout(monkeypatch):
    from modules.flow_gate.services import test_run_service as runner, spec_execution_service as spec
    monkeypatch.setattr(spec.db_test_runs, 'get_run', lambda _: {'doc_id':'ts'})
    monkeypatch.setattr(spec.db_docs, 'get_by_id', lambda _: {'doc_id':'ts'})
    monkeypatch.setattr(runner, 'automatic_case_timeout', lambda doc: 150)
    assert spec._automatic_timeout(SimpleNamespace(run_id='r')) == 150
    assert spec._automatic_timeout(None) == 120


def test_automatic_timeout_reaches_spawn_and_wait(monkeypatch, tmp_path):
    import threading
    from modules.flow_gate.services import spec_execution_service as spec
    from modules.flow_gate.services import test_run_service as runner, tr_self_check_executor as executor
    observed = []
    monkeypatch.setattr(spec, '_automatic_timeout', lambda active: 150)
    monkeypatch.setattr(spec, '_python', lambda env: 'python')
    monkeypatch.setattr(runner, '_allocate_port', lambda: 12345)
    monkeypatch.setattr(runner, '_execution_env', lambda *_: {})
    monkeypatch.setattr(spec.process_runner, 'child_env', lambda env, **kw: env)
    monkeypatch.setattr(runner, '_set_current_control', lambda *_: None)
    monkeypatch.setattr(runner, '_clear_current_control', lambda *_: None)
    control = SimpleNamespace(close=lambda: None)
    def spawn(argv, cwd, env, timeout, run_id):
        observed.append(('spawn', timeout))
        return control, None
    def wait(control, timeout, cancelled):
        observed.append(('wait', timeout))
        return SimpleNamespace(stdout_tail='', stderr_tail='', timed_out=False, exit_code=0)
    monkeypatch.setattr(executor, 'spawn', spawn)
    monkeypatch.setattr(executor, 'wait', wait)
    active = SimpleNamespace(run_id='r', cancel_event=threading.Event())
    result, _, code, _ = spec.ExistingRunnerAdapter.run_pytest('test.py::test_one', tmp_path, tmp_path, active)
    assert (result, code) == ('pass', 0)
    assert observed == [('spawn', 150), ('wait', 150)]
