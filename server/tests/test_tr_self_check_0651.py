"""Targeted policy and process-owner regressions for 0651."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from modules.flow_gate.api.v1 import self_check_routes
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import api_server_tools as tools


@pytest.mark.parametrize("program", ["", ".", "..", "../pytest", "C:\\pytest.exe", "\\\\host\\share\\pytest", "git", "git-foo", "curl", "docker", "powershell", "npx"])
def test_program_rejected(program):
    if program in {"git", "git-foo", "curl", "docker", "powershell", "npx"}:
        with pytest.raises(policy.PolicyError, match="selfcheck_program_denied"):
            policy.resolve_command(program, [], Path.cwd(), os.environ.get("PATH", ""))
    else:
        with pytest.raises(policy.PolicyError, match="selfcheck_invalid_program"):
            policy.validate_program(program)


def test_args_allow_normal_test_paths_and_python_module():
    policy.validate_args("pytest", ["tests/test_git_merge.py", "-q"])
    policy.validate_args("python", ["-m", "pytest", "tests/test_git_merge.py", "-q"])
    policy.validate_args("python", ["file.py"])
    policy.validate_args("java", ["-jar", "app.jar"])
    policy.validate_args("dotnet", ["test"])


@pytest.mark.parametrize("program,args", [
    ("pytest", ["&&"]), ("python", ["-c", "print(1)"]),
    ("node", ["--eval=console.log(1)"]), ("npm", ["install", "-g", "pkg"]),
    ("pip", ["install", "--break-system-packages", "pkg"]),
    ("cargo", ["publish"]), ("mvn", ["deploy"]),
])
def test_args_denied(program, args):
    with pytest.raises(policy.PolicyError):
        policy.validate_args(program, args)


def test_cwd_containment_and_env_scrubbing(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "tests").mkdir()
    assert policy.validate_cwd(tree, "tests") == tree / "tests"
    with pytest.raises(policy.PolicyError):
        policy.validate_cwd(tree, "..")
    try:
        (tree / "escape").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(policy.PolicyError):
            policy.validate_cwd(tree, "escape")
    env = policy.scrubbed_env(str(tree), tmp_path / "runtime", {
        "LANG": "C.UTF-8", "FLOWGATE_TOKEN": "secret", "NODE_OPTIONS": "--require evil",
        "GIT_DIR": "evil", "PATH": "evil",
    })
    assert env["PATH"] == str(tree)
    assert env["LANG"] == "C.UTF-8"
    assert "FLOWGATE_TOKEN" not in env
    assert "NODE_OPTIONS" not in env
    assert "GIT_DIR" not in env


def test_controlled_path_prefers_project_virtualenv(tmp_path):
    tree = tmp_path / "tree"
    local_bin = tree / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    local_bin.mkdir(parents=True)
    host_bin = tmp_path / "host"
    host_bin.mkdir()
    value, dirs = policy.controlled_path(tree, str(host_bin))
    assert dirs[0] == local_bin.resolve()
    assert str(local_bin) in value


def test_executor_shell_free_output_and_reap(tmp_path):
    # The executor is tested directly so its process ownership can be checked
    # independently of policy and DB admission.
    argv = [sys.executable, "-c", "import sys; print('ready'); print('warning', file=sys.stderr)"]
    control, owner = executor.spawn(argv, tmp_path, dict(os.environ), 5, "scr_test")
    try:
        assert owner["process_owner_kind"] in {"windows_job", "posix_supervisor"}
        result = executor.wait(control, 5, threading.Event())
        assert result.exit_code == 0
        assert "ready" in result.stdout_tail
        assert "warning" in result.stderr_tail
        assert control.proc.poll() is not None
    finally:
        control.close()


def test_executor_timeout_reaps(tmp_path):
    control, _ = executor.spawn([sys.executable, "-c", "import time; time.sleep(10)"],
                                tmp_path, dict(os.environ), 1, "scr_timeout")
    try:
        result = executor.wait(control, 1, threading.Event())
        assert result.timed_out
        assert control.proc.poll() is not None
    finally:
        control.close()


def test_executor_cancel_reaps(tmp_path):
    control, _ = executor.spawn([sys.executable, "-c", "import time; time.sleep(10)"],
                                tmp_path, dict(os.environ), 10, "scr_cancel")
    cancelled = threading.Event()
    cancelled.set()
    control.cancel()
    try:
        result = executor.wait(control, 10, cancelled)
        assert result.cancelled
        assert control.proc.poll() is not None
    finally:
        control.close()


def test_worker_tool_exposure_splits_tr_edit_and_review(monkeypatch):
    monkeypatch.setattr(tools, "_step_type", lambda run: run["step_type"])
    monkeypatch.setattr(tools.tool_registry, "kind_for_step", lambda *_: ("read_write", None))
    monkeypatch.setattr(tools.tool_registry, "tool_names", lambda *_: [])
    base = {"doc_ref": "p.default.0001.0003-TR", "step_type": "TR"}
    edit = {item["name"] for item in tools.definitions_for_run({**base, "action_scope": "edit"})}
    assert set(tools.SELF_CHECK_NAMES) <= edit
    assert "access_source_bundle" not in edit
    review = {item["name"] for item in tools.definitions_for_run({**base, "action_scope": "review"})}
    assert "read_self_check" in review
    assert "run_self_check" not in review
    assert "cancel_self_check" not in review
    assert "access_source_bundle" not in review
    ts = {item["name"] for item in tools.definitions_for_run({**base, "step_type": "TS", "action_scope": "edit"})}
    assert set(tools.SELF_CHECK_NAMES).isdisjoint(ts)


def test_worker_call_splits_edit_execution_and_review_evidence(monkeypatch):
    doc_id = "p.default.0001.0003-TR"
    run = {"action_scope": "edit", "doc_ref": doc_id, "group_id": "p.default.0001", "project_id": "p"}
    token = {"action_scope": "edit", "doc_ref": doc_id, "group_id": "p.default.0001", "project": "p", "issued_to": "user"}
    monkeypatch.setattr(tools.token_service, "verify", lambda _: token)
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _: {"type_code": "TR"})
    monkeypatch.setattr(tools.tr_self_check_service, "start", lambda *args: {"self_check_run_id": "scr_1", "status": "pending"})
    monkeypatch.setattr(tools.tr_self_check_service, "list_runs", lambda doc, limit=20: [{"self_check_run_id": "scr_1", "status": "completed"}])
    monkeypatch.setattr(tools.tr_self_check_service, "read", lambda doc, rid: {"self_check_run_id": rid, "status": "completed", "exit_code": 0})
    status, result = tools.self_check_call(run, "live-token", "run_self_check", {"program": "pytest"})
    assert status == 202 and result["self_check_run_id"] == "scr_1"

    run["action_scope"] = token["action_scope"] = "review"
    status, listed = tools.self_check_call(run, "live-token", "read_self_check", {})
    assert status == 200 and listed["runs"][0]["self_check_run_id"] == "scr_1"
    status, detail = tools.self_check_call(run, "live-token", "read_self_check", {"self_check_run_id": "scr_1"})
    assert status == 200 and detail["exit_code"] == 0
    for name, payload in (("run_self_check", {"program": "pytest"}), ("cancel_self_check", {"self_check_run_id": "scr_1"})):
        with pytest.raises(tools.ToolError, match="selfcheck_forbidden"):
            tools.self_check_call(run, "live-token", name, payload)


def test_rest_worker_auth_allows_review_reads_but_not_mutation(monkeypatch):
    doc_id = "p.default.0001.0003-TR"
    token = {"action_scope": "review", "doc_ref": doc_id, "group_id": "p.default.0001", "project": "p"}
    monkeypatch.setattr(self_check_routes, "verify_bearer", lambda _request: token)
    monkeypatch.setattr(self_check_routes.selfcheck, "_document", lambda _doc: {"project_id": "p", "group_id": "p.default.0001"})
    assert self_check_routes._auth(object(), doc_id, False) is token
    denied = self_check_routes._auth(object(), doc_id, True)
    assert denied.status_code == 403


def test_review_selfcheck_detail_is_isolated_to_bound_tr(monkeypatch):
    doc_a = "p.default.0001.0003-TR"
    foreign_run = "scr_from_tr_b"
    run = {"action_scope": "review", "doc_ref": doc_a, "group_id": "p.default.0001", "project_id": "p"}
    token = {"action_scope": "review", "doc_ref": doc_a, "group_id": "p.default.0001", "project": "p", "issued_to": "reviewer"}
    monkeypatch.setattr(tools.token_service, "verify", lambda _: token)
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda doc_id: {"doc_id": doc_id, "type_code": "TR", "project_id": "p", "group_id": "p.default.0001"})
    monkeypatch.setattr(tools.tr_self_check_service.db_groups, "get_by_id", lambda _group_id: {"group_id": "p.default.0001", "status": "OPEN", "deleted_at": None})
    monkeypatch.setattr(tools.tr_self_check_service.db_runs, "get_run_for_doc", lambda run_id, doc_id: None)

    with pytest.raises(tools.ToolError) as exc:
        tools.self_check_call(run, "live-token", "read_self_check", {"self_check_run_id": foreign_run})
    assert exc.value.status == 404
    assert exc.value.reason == "selfcheck_run_not_found"
