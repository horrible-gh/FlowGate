"""flowgate.v02.0094 B0001, B0005 — the automated Case's execution contract is framework-neutral.

An automated Case says what runs in one ``run_command``; naming a test file is not required, and
the test files its arguments do name are its targets (B0005). The command is checked by the TR Self-check command policy (no shell,
operators, inline code or denied program), resolved again in the run's copy and executed
through the shared process layer; exit 0 is PASS, a JUnit XML report written to ``{junit}``
adds per-test detail. A Case saved with a target (``automation_ref``, optionally ``run_cwd``)
keeps the result it had: a ``.py`` target without a command keeps its pytest invocation, any
other target without one is ``command_missing``.
"""
from __future__ import annotations

import shutil

import pytest
from fastapi import HTTPException

from basis_support import BasisEnv
from modules.flow_gate.services import spec_execution_service as execution
from modules.flow_gate.services import test_basis_service as basis
from modules.flow_gate.services import test_run_service as runner
from modules.flow_gate.services import test_spec_service as spec

NODE_TEST = "client_gui/tests/sum.0094.test.mjs"
NODE_SOURCE = (
    "import { test } from 'node:test'\n"
    "import assert from 'node:assert/strict'\n"
    "test('TC-1 sum', () => { assert.equal(1 + 2, 3) })\n"
)
PY_PASS = "tests/check_pass_0094.py"
PY_FAIL = "tests/check_fail_0094.py"
PY_JUNIT = "tests/check_junit_0094.py"
# Outside any test folder, driven by its arguments.
PY_SCRIPT = "scripts/check_args_0094.py"
SOURCES = {
    PY_SCRIPT: "import sys\nprint('args', sys.argv[1:])\nsys.exit(0 if sys.argv[1:] == ['--mode', 'fast'] else 1)\n",
    NODE_TEST: NODE_SOURCE,
    PY_PASS: "import sys\nassert sorted([3, 1, 2]) == [1, 2, 3]\nprint('sorted ok')\nsys.exit(0)\n",
    PY_FAIL: "import sys\nprint('expected 3, got 4')\nsys.exit(1)\n",
    # Exit 0 but a failed test in the report: the report decides.
    PY_JUNIT: ("import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write(\n"
               "    '<testsuite><testcase name=\"ok\"/><testcase name=\"broken\">'\n"
               "    '<failure message=\"boom\">trace</failure></testcase></testsuite>')\n"),
}


def case(ref, command="", cwd="", mode="automated", case_id="TC-1"):
    return {"case_id": case_id, "title": "t", "execution_mode": mode, "automation_ref": ref,
            "run_command": command, "run_cwd": cwd, "test_assets": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = BasisEnv(tmp_path, monkeypatch)
    root = environment.repo("g1")
    for relative, text in SOURCES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return environment


def _run(env, cases):
    env.cases = cases
    doc = env.ts()
    _, root, scratch = env.run_basis(doc, "run-cmd")
    out = []
    for item in cases:
        loc = execution.AutomationRefResolver.resolve(item)
        out.append(execution.ExistingRunnerAdapter.run_command(
            item, loc, root, scratch, None, submitted_by="u", source_identity={}))
    return out


# ── the contract: target + command, no framework assumed ─────────────────────

def test_a_command_makes_any_test_file_runnable_and_selectable():
    for ref, command, cwd in (
        ("client_gui/tests/login.spec.ts", "npm test -- {target}", "client_gui"),
        ("src/test/java/com/acme/LoginTest.java", "mvn -q test -Dtest=LoginTest", ""),
        ("service/test/login_test.go", "go test ./service/test", ""),
        ("tests/test_login.py::test_ok", "pytest -q {target}::{node}", ""),
    ):
        loc = basis.locator(case(ref, command, cwd))
        assert loc["capability"] == "case_selectable", (ref, loc)
        assert loc["command"]["program"] == command.split()[0]
        assert loc["command"]["cwd"] == (cwd or ".")
    assert basis.locator(case("tests/a.spec.ts", 'node --test "{target}"'))["command"] == {
        "program": "node", "args": ["--test", "{target}"], "cwd": "."}
    selected = execution.automated_selection([
        case("client_gui/tests/a.spec.ts", "npm test -- {target}", "client_gui", case_id="TC-1"),
        case("client_gui/tests/b.spec.ts", "", case_id="TC-2"),
        case("tests/test_c.py::test_c", "", case_id="TC-3"),
        case("tests/d.spec.ts", "npm test", mode="manual", case_id="TC-4"),
    ])
    assert [item["case_id"] for item in selected] == ["TC-1", "TC-3"]


def test_without_a_command_only_a_saved_py_target_keeps_its_pytest_invocation():
    assert basis.locator(case("tests/test_a.py::test_x")) == {
        "capability": "case_selectable", "path": "tests/test_a.py", "node": "test_x"}
    for ref in ("client_gui/tests/a.spec.ts", "src/test/java/ATest.java", "tests/a.test.mjs"):
        assert basis.locator(case(ref))["capability"] == "command_missing"
    for ref in ("6", "server/app.py", "tests/fixture.json", "run the login test"):
        assert basis.locator(case(ref, "npm test"))["capability"] == "unbound"


# ── B0005: the command alone says what runs ─────────────────────────────────

def test_without_a_target_the_command_names_its_test_files():
    loc = basis.locator(case("", "node --test client_gui/tests/a.test.mjs"))
    assert loc == {"capability": "case_selectable", "path": "client_gui/tests/a.test.mjs", "node": None,
                   "command": {"program": "node", "args": ["--test", "client_gui/tests/a.test.mjs"], "cwd": "."},
                   "tests": ["client_gui/tests/a.test.mjs"]}
    assert basis.locator(case("", "pytest -q tests/test_a.py::test_x"))["node"] == "test_x"
    both = basis.locator(case("", "npm test -- --spec=tests/a.spec.ts ./tests/b.spec.ts tests/a.spec.ts"))
    assert both["tests"] == ["tests/a.spec.ts", "tests/b.spec.ts"]
    # A stored cwd still counts: the path is read from it, as the run does.
    assert basis.locator(case("", "npm test -- tests/a.spec.ts", "client_gui"))["path"] == "client_gui/tests/a.spec.ts"
    assert basis._roles([case("", "node --test tests/a.test.mjs tests/b.test.mjs")]) == {
        "tests/a.test.mjs": "test", "tests/b.test.mjs": "test"}


def test_without_a_target_a_command_need_not_name_a_test_file():
    assert basis.locator(case("")) == {"capability": "command_missing"}
    for command in ("npm test", "go test ./...", "cargo test", "python scripts/check.py --mode fast",
                    "node --test tests/fixture.json", "7"):
        loc = basis.locator(case("", command))
        assert loc["capability"] == "case_selectable", command
        assert loc["tests"] == [] and "path" not in loc, command
    assert basis._roles([case("", "npm test")]) == {}
    # The command policy still decides what may run.
    assert basis.locator(case("", "npx vitest run tests/a.spec.ts")) == {
        "capability": "command_invalid", "reason": "selfcheck_program_denied"}
    assert basis.locator(case("", "bash tests/run.sh a b"))["capability"] == "command_invalid"
    assert [item["case_id"] for item in execution.automated_selection(
        [case("", "npm test", case_id="TC-1"), case("", "bash run.sh", case_id="TC-2")])] == ["TC-1"]


def test_the_editor_save_refuses_a_changed_case_that_cannot_run():
    stored = [case("6", "7", case_id="TC-1"), case("", "", mode="manual", case_id="TC-2")]
    # Untouched settings are not judged again, so a save never fails on a case it did not change.
    assert spec.unrunnable_case_errors(stored, stored) == []
    changed = [case("", "bash run.sh", case_id="TC-1"), case("", "", case_id="TC-2"),
               case("", "node --test tests/a.test.mjs", case_id="TC-3"), case("", "npm test", case_id="TC-4")]
    errors = spec.unrunnable_case_errors(changed, stored)
    assert [(e["case_id"], e["field"], e["code"]) for e in errors] == [
        ("TC-1", "run_command", "command_invalid"), ("TC-2", "run_command", "command_missing")]


@pytest.mark.parametrize("command, cwd, reason", [
    ("bash -c 'npm test'", "", "selfcheck_program_denied"),
    ("cmd /c npm test", "", "selfcheck_program_denied"),
    ("powershell -Command Get-Item", "", "selfcheck_program_denied"),
    ("git status", "", "selfcheck_program_denied"),
    ("curl http://example.invalid", "", "selfcheck_program_denied"),
    ("rm -rf tests", "", "selfcheck_program_denied"),
    ("npx vitest run", "", "selfcheck_program_denied"),
    ("python -c 'print(1)'", "", "selfcheck_inline_execution"),
    ("node -e 'process.exit(0)'", "", "selfcheck_inline_execution"),
    ("npm test | tee out.txt", "", "selfcheck_shell_operator"),
    ("npm test && rm x", "", "selfcheck_shell_operator"),
    ("npm publish", "", "selfcheck_package_mutation"),
    ("./run_tests", "", "selfcheck_invalid_program"),
    ("C:/tools/runner.exe", "", "selfcheck_invalid_program"),
    ("npm test 'unbalanced", "", "command_unparsable"),
    ("npm test", "../outside", "command_invalid_cwd"),
    ("npm test", "/abs", "command_invalid_cwd"),
    ("npm test", "C:/work", "command_invalid_cwd"),
])
def test_the_self_check_command_policy_refuses_unsafe_commands(command, cwd, reason):
    loc = basis.locator(case("client_gui/tests/a.spec.ts", command, cwd))
    assert loc["capability"] == "command_invalid"
    assert loc["reason"] == reason
    assert execution.automated_selection([case("client_gui/tests/a.spec.ts", command, cwd)]) == []


def test_admit_runs_a_command_case_instead_of_reporting_no_automated_case(env, monkeypatch):
    env.cases = [case("client_gui/tests/a.spec.ts", "", case_id="TC-1")]
    doc = env.ts()
    monkeypatch.setattr(runner, "load_spec_ts", lambda doc_id: (env.doc(doc_id), {"cases": env.cases}))
    monkeypatch.setattr(runner, "_active_tsr_for_ts", lambda d: None)
    with pytest.raises(HTTPException) as nothing:
        execution.admit(doc["doc_id"], case_id=None, runner_id="u", locale="en")
    assert nothing.value.detail["error"] == "no_automated_bindings"
    env.cases = [case("client_gui/tests/a.spec.ts", "npm test -- {target}", "client_gui")]
    monkeypatch.setattr(execution.db_test_runs, "get_running_by_doc", lambda doc_id: None)
    monkeypatch.setattr(execution.db_test_runs, "get_pending_failure_origin", lambda doc_id: None)
    monkeypatch.setattr(execution.db_test_runs, "insert_run", lambda **kw: {"run_id": "r1", **kw})
    monkeypatch.setattr(runner, "open_execution_tsr", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_emit_started", lambda d, run: None)
    monkeypatch.setattr(runner, "_run_response", lambda run: {"run_id": run["run_id"]})
    admitted = execution.admit(doc["doc_id"], case_id="TC-1", runner_id="u", locale="en")
    assert admitted["selected_case_ids"] == ["TC-1"]


# ── the specification grammar keeps the fields, and only when set ────────────

def test_run_command_and_cwd_round_trip_through_the_specification():
    body = ("---\n" + spec.CONTRACT_KEY + ": " + str(spec.CONTRACT_SPEC) + "\n---\n# T\n\n## 시험 사양\n\n"
            "### TC-1: login\n- category: normal\n- requirement: R1\n- execution_mode: automated\n"
            "- procedure: run\n- expected: ok\n- check_points: ok\n"
            "- 실행 대상: client_gui/tests/login.spec.ts\n- 실행 명령: npm test -- {target}\n"
            "- 실행 위치: client_gui\n")
    parsed = spec.parse_spec(body)
    assert parsed["errors"] == []
    item = parsed["cases"][0]
    assert (item["automation_ref"], item["run_command"], item["run_cwd"]) == (
        "client_gui/tests/login.spec.ts", "npm test -- {target}", "client_gui")
    rendered = spec.render_spec_body(parsed["cases"], title="T")
    assert "- run_command: npm test -- {target}\n- run_cwd: client_gui\n" in rendered
    plain = spec.render_spec_body([{**item, "run_command": "", "run_cwd": ""}], title="T")
    assert "run_command" not in plain and "run_cwd" not in plain
    validated, errors = spec.validate_cases_payload([item])
    assert errors == [] and validated[0]["run_command"] == "npm test -- {target}"


# ── execution: real processes in the run's copy, the exit code decides ───────

def test_a_python_check_runs_without_pytest_and_its_exit_code_is_the_verdict(env):
    passed, failed = _run(env, [case(PY_PASS, "python {target}", case_id="TC-1"),
                                case(PY_FAIL, "python {target}", case_id="TC-2")])
    assert passed["status"] == "PASS" and passed["actual"] == "exit 0"
    assert any("sorted ok" in item["value"] for item in passed["evidence"])
    assert failed["status"] == "FAIL" and failed["actual"] == "exit 1"
    assert any("expected 3, got 4" in item["value"] for item in failed["evidence"])
    assert all("pytest" not in item["value"] for item in passed["evidence"] + failed["evidence"])


def test_a_command_without_a_target_runs_and_its_exit_code_is_the_verdict(env):
    passed, failed = _run(env, [case("", f"python {PY_PASS}", case_id="TC-1"),
                                case("", f"python {PY_FAIL}", case_id="TC-2")])
    assert (passed["status"], failed["status"]) == ("PASS", "FAIL")
    assert passed["source_name"] == PY_PASS
    assert f"command: python {PY_PASS} (cwd .) exit=0" in passed["evidence"][0]["value"]


def test_a_script_outside_the_test_folders_runs_with_its_arguments(env):
    passed, failed = _run(env, [case("", f"python {PY_SCRIPT} --mode fast", case_id="TC-1"),
                                case("", f"python {PY_SCRIPT} --mode slow", case_id="TC-2")])
    assert (passed["status"], failed["status"]) == ("PASS", "FAIL"), (passed, failed)
    # No test file named: the command is the result's source.
    assert passed["source_name"] == f"python {PY_SCRIPT} --mode fast"


def test_a_junit_report_written_to_the_placeholder_is_collected(env):
    (result,) = _run(env, [case(PY_JUNIT, "python {target} {junit}")])
    assert result["status"] == "FAIL"
    assert result["actual"].startswith("exit 0, junit 1/2 passed: boom")
    assert any("broken" in item["value"] for item in result["evidence"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
def test_a_javascript_test_runs_with_its_own_runner_from_its_module_directory(env):
    (result,) = _run(env, [case(NODE_TEST, "node --test {target}", "client_gui")])
    assert result["status"] == "PASS", result
    assert "command: node --test tests/sum.0094.test.mjs (cwd client_gui) exit=0" in result["evidence"][0]["value"]


def test_the_run_copy_refuses_again_what_the_policy_refuses(env):
    (missing,) = _run(env, [case(PY_PASS, "flowgate-no-such-runner-0094 {target}")])
    assert missing["status"] == "BLOCKED"
    assert missing["actual"] == "command refused: selfcheck_executable_not_found"
    # A locator answer that skipped the lexical check is still refused before anything starts.
    env.cases = [case(PY_PASS, "python {target}")]
    doc = env.ts(number=11)
    _, root, scratch = env.run_basis(doc, "run-deny")
    loc = {"capability": "case_selectable", "path": PY_PASS, "node": None,
           "command": {"program": "bash", "args": ["-c", "exit 0"], "cwd": "."}}
    tampered = execution.ExistingRunnerAdapter.run_command(
        env.cases[0], loc, root, scratch, None, submitted_by="u", source_identity={})
    assert tampered["status"] == "BLOCKED"
    assert tampered["actual"] == "command refused: selfcheck_program_denied"
