"""flowgate.default.0503 T0007 -- native assertion engine and case evidence (T2).

Covers the T's completion-condition test list (section 9):
  * parse_test_cases: assert-less legacy cases keep assert_mode=None; the six known
    assert modes parse; unknown modes, malformed JSON literals, and a malformed
    exit_code grammar (missing colon / non-integer target) all 422 at parse time
    (before a single command runs -- parse_test_plan is called at admission, ahead
    of _admission_lock/insert_run/Popen).
  * _evaluate_case_assertion: the native comparator for all six modes, including the
    "stdout is not JSON" / "path not found" grading-time mismatch paths that must NOT
    raise or 422.
  * _execute_case: exit_code==0 with a mismatching assert still yields result="fail"
    (NR0003's legal combination) -- with a fake Popen so exit_code is deterministic,
    the same recipe test_test_run_0138.py's timeout test uses.
  * db/test_runs.py: insert_run / mark_case_finished / list_cases roundtrip preserves
    assert_mode/actual/comparison_result against a real sqlite DB with every migration
    applied (so migration 102's columns are proven, not assumed).
  * _shape_case_item: the three fields are exposed on the API-shaped case dict.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest


# -- parse_test_cases: assert field syntax and validation --------------------


def _ts_with_assert(assert_line):
    body = (
        "## 테스트 케이스\n\n"
        "### TC-1: ok\n"
        "- cmd: echo hi\n"
        "- 기대: exits zero\n"
    )
    if assert_line is not None:
        body += f"- assert: {assert_line}\n"
    return body


def test_parse_test_cases_without_assert_field_leaves_assert_mode_none():
    from modules.flow_gate.services.test_run_service import parse_test_cases

    cases = parse_test_cases(_ts_with_assert(None))

    assert cases[0]["assert_mode"] is None


@pytest.mark.parametrize(
    "assert_line",
    [
        "exit_code:0",
        "stdout_equals:ok",
        'stdout_contains:"enabled":true',
        "stdout_not_contains:error",
        "json_equals:data.enabled=true",
        'json_subset:{"enabled": true}',
    ],
)
def test_parse_test_cases_accepts_all_six_assert_modes(assert_line):
    from modules.flow_gate.services.test_run_service import parse_test_cases

    cases = parse_test_cases(_ts_with_assert(assert_line))

    assert cases[0]["assert_mode"] == assert_line


def test_parse_test_cases_rejects_unknown_assert_mode():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("regex_match:^ok$"))

    assert excinfo.value.code == "invalid_case_block"
    assert "unknown assert mode" in excinfo.value.detail


def test_parse_test_cases_rejects_invalid_json_literal_in_json_equals():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("json_equals:data.enabled=not-json"))

    assert excinfo.value.code == "invalid_case_block"


def test_parse_test_cases_rejects_invalid_json_literal_in_json_subset():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("json_subset:{not valid json}"))

    assert excinfo.value.code == "invalid_case_block"


def test_parse_test_cases_rejects_non_object_json_subset():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("json_subset:[1, 2, 3]"))

    assert excinfo.value.code == "invalid_case_block"
    assert "json_subset" in excinfo.value.detail


def test_parse_test_cases_rejects_exit_code_without_colon():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("exit_code"))

    assert excinfo.value.code == "invalid_case_block"
    assert "exit_code" in excinfo.value.detail


def test_parse_test_cases_rejects_non_integer_exit_code_target():
    from modules.flow_gate.services.test_run_service import (
        TestCaseParseError,
        parse_test_cases,
    )

    with pytest.raises(TestCaseParseError) as excinfo:
        parse_test_cases(_ts_with_assert("exit_code:not-a-number"))

    assert excinfo.value.code == "invalid_case_block"
    assert "exit_code" in excinfo.value.detail


def test_parse_test_cases_accepts_negative_exit_code_target():
    from modules.flow_gate.services.test_run_service import parse_test_cases

    cases = parse_test_cases(_ts_with_assert("exit_code:-1"))

    assert cases[0]["assert_mode"] == "exit_code:-1"


# -- _evaluate_case_assertion: the native comparator --------------------------


def test_exit_code_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    actual, result = _evaluate_case_assertion("exit_code:0", 0, "")
    assert (actual, result) == ("0", "match")

    actual, result = _evaluate_case_assertion("exit_code:0", 1, "")
    assert (actual, result) == ("1", "mismatch")


def test_stdout_equals_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    assert _evaluate_case_assertion("stdout_equals:ok", 0, "ok") == ("ok", "match")
    assert _evaluate_case_assertion("stdout_equals:ok", 0, "not ok")[1] == "mismatch"


def test_stdout_contains_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    _, match = _evaluate_case_assertion(
        'stdout_contains:"enabled":true', 0, '{"enabled":true}'
    )
    assert match == "match"

    _, mismatch = _evaluate_case_assertion(
        'stdout_contains:"enabled":true', 0, '{"enabled":false}'
    )
    assert mismatch == "mismatch"


def test_stdout_not_contains_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    assert _evaluate_case_assertion("stdout_not_contains:error", 0, "all good")[1] == "match"
    assert _evaluate_case_assertion("stdout_not_contains:error", 0, "an error occurred")[1] == "mismatch"


def test_json_equals_nested_dotted_path_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    stdout = '{"data": {"items": [{"enabled": true}]}}'
    actual, result = _evaluate_case_assertion(
        "json_equals:data.items.0.enabled=true", 0, stdout
    )
    assert (actual, result) == ("True", "match")

    actual, result = _evaluate_case_assertion(
        "json_equals:data.items.0.enabled=false", 0, stdout
    )
    assert result == "mismatch"


def test_json_equals_non_json_stdout_is_mismatch_not_error():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    actual, result = _evaluate_case_assertion(
        "json_equals:data.enabled=true", 0, "not json at all"
    )
    assert result == "mismatch"
    assert actual == "<non-json output>"


def test_json_equals_unresolvable_path_is_mismatch_not_error():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    actual, result = _evaluate_case_assertion(
        "json_equals:data.missing=true", 0, '{"data": {"enabled": true}}'
    )
    assert result == "mismatch"
    assert actual == "<path not found>"


def test_json_subset_match_and_mismatch():
    from modules.flow_gate.services.test_run_service import _evaluate_case_assertion

    stdout = '{"data": {"enabled": true, "other": 1}, "extra": "x"}'
    _, match = _evaluate_case_assertion(
        'json_subset:{"data": {"enabled": true}}', 0, stdout
    )
    assert match == "match"

    _, mismatch = _evaluate_case_assertion(
        'json_subset:{"data": {"enabled": false}}', 0, stdout
    )
    assert mismatch == "mismatch"


# -- _execute_case wiring: exit_code==0 + mismatch is a legal "fail" ----------


class _FakeProc:
    def __init__(self, returncode, stdout, stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def communicate(self, timeout=None):
        return (self._stdout, self._stderr)

    def poll(self):
        return self.returncode


def test_execute_case_exit_zero_with_mismatching_assert_yields_fail(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        test_run_service.subprocess, "Popen", lambda *a, **kw: _FakeProc(0, b'{"enabled": false}')
    )

    captured = {}
    monkeypatch.setattr(
        test_run_service.db_test_runs, "mark_case_finished", lambda **kw: captured.update(kw)
    )

    doc = {"doc_id": "d1", "project_id": "p1", "group_id": "g1"}
    run = {"run_id": "r1"}
    case = {
        "id": 1,
        "cmd": "echo whatever",
        "assert_mode": "json_equals:enabled=true",
    }

    test_run_service._execute_case(doc, run, case, 1, 1, tmp_path, 12345, tmp_path, {}, None)

    assert captured["exit_code"] == 0
    assert captured["comparison_result"] == "mismatch"
    assert captured["result"] == "fail"


def test_execute_case_without_assert_mode_keeps_legacy_exit_code_verdict(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        test_run_service.subprocess, "Popen", lambda *a, **kw: _FakeProc(0, b"ok")
    )

    captured = {}
    monkeypatch.setattr(
        test_run_service.db_test_runs, "mark_case_finished", lambda **kw: captured.update(kw)
    )

    doc = {"doc_id": "d1", "project_id": "p1", "group_id": "g1"}
    run = {"run_id": "r1"}
    case = {"id": 1, "cmd": "echo ok", "assert_mode": None}

    test_run_service._execute_case(doc, run, case, 1, 1, tmp_path, 12345, tmp_path, {}, None)

    assert captured["result"] == "pass"
    assert captured["actual"] is None
    assert captured["comparison_result"] is None


def test_execute_case_timeout_skips_grading_even_with_assert_mode(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    class _TimeoutProc:
        def communicate(self, timeout=None):
            raise test_run_service.subprocess.TimeoutExpired(cmd="sleep", timeout=timeout)

        def poll(self):
            return None

    monkeypatch.setattr(test_run_service.subprocess, "Popen", lambda *a, **kw: _TimeoutProc())
    monkeypatch.setattr(test_run_service, "_kill_process_tree", lambda proc: None)

    captured = {}
    monkeypatch.setattr(
        test_run_service.db_test_runs, "mark_case_finished", lambda **kw: captured.update(kw)
    )

    doc = {"doc_id": "d1", "project_id": "p1", "group_id": "g1"}
    run = {"run_id": "r1"}
    case = {"id": 1, "cmd": "sleep 999", "assert_mode": "exit_code:0"}

    test_run_service._execute_case(doc, run, case, 1, 1, tmp_path, 12345, tmp_path, {}, None)

    assert captured["result"] == "timeout"
    assert captured["exit_code"] is None
    assert captured["actual"] is None
    assert captured["comparison_result"] is None


# -- _shape_case_item: assert evidence reaches the API shape ------------------


def test_shape_case_item_exposes_assert_evidence_fields():
    from modules.flow_gate.services.test_run_service import _shape_case_item

    shaped = _shape_case_item(
        {
            "case_no": "TC-1",
            "kind": "case",
            "assert_mode": "exit_code:0",
            "actual": "0",
            "comparison_result": "match",
        }
    )

    assert shaped["assert_mode"] == "exit_code:0"
    assert shaped["actual"] == "0"
    assert shaped["comparison_result"] == "match"


def test_shape_case_item_legacy_case_has_null_assert_evidence():
    from modules.flow_gate.services.test_run_service import _shape_case_item

    shaped = _shape_case_item({"case_no": "TC-1", "kind": "case"})

    assert shaped["assert_mode"] is None
    assert shaped["actual"] is None
    assert shaped["comparison_result"] is None


# -- db/test_runs.py: real-sqlite roundtrip (migration 102 columns) ----------


class _TestStore:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def transaction(self):
        yield self

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, sql, params=None):
        rows = self._conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]


@pytest.fixture
def store(all_migrations_db, monkeypatch):
    from modules.flow_gate.db import connection

    test_store = _TestStore(all_migrations_db)
    monkeypatch.setattr(connection, "STORE", test_store)
    return test_store


def _seed_doc(conn, doc_id):
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(doc_id, project_id, module, type_code, seq, title, status, created_at, updated_at) "
        "VALUES (?, '__SYSTEM__', 'default', 'TS', 1, 'seed TS', 'approved', "
        "datetime('now'), datetime('now'))",
        [doc_id],
    )
    conn.commit()


def test_insert_run_mark_case_finished_list_cases_roundtrip_preserves_assert_fields(store):
    from modules.flow_gate.db import test_runs as db_test_runs

    _seed_doc(store._conn, "doc-0503-assert")

    run = db_test_runs.insert_run(
        doc_id="doc-0503-assert",
        revision_no=1,
        triggered_via="ui",
        runner_id="tester",
        cases=[
            {
                "kind": "case",
                "case_no": "TC-1",
                "title": "assert case",
                "cmd": "echo hi",
                "expect": "hi",
                "assert_mode": "exit_code:0",
            },
            {
                "kind": "case",
                "case_no": "TC-2",
                "title": "legacy case",
                "cmd": "echo bye",
                "expect": "bye",
            },
        ],
    )

    cases = db_test_runs.list_cases(run["run_id"])
    assert [c["assert_mode"] for c in cases] == ["exit_code:0", None]
    assert [c["actual"] for c in cases] == [None, None]
    assert [c["comparison_result"] for c in cases] == [None, None]

    db_test_runs.mark_case_finished(
        case_id=cases[0]["id"],
        result="pass",
        exit_code=0,
        duration_ms=12,
        output_tail="0",
        actual="0",
        comparison_result="match",
    )
    db_test_runs.mark_case_finished(
        case_id=cases[1]["id"],
        result="pass",
        exit_code=0,
        duration_ms=8,
        output_tail="bye",
    )

    refreshed = db_test_runs.list_cases(run["run_id"])
    assert refreshed[0]["assert_mode"] == "exit_code:0"
    assert refreshed[0]["actual"] == "0"
    assert refreshed[0]["comparison_result"] == "match"
    assert refreshed[1]["assert_mode"] is None
    assert refreshed[1]["actual"] is None
    assert refreshed[1]["comparison_result"] is None
