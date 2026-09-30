"""Targeted policy and process-owner regressions for 0651."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

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


def test_worker_tool_exposure_is_tr_edit_only(monkeypatch):
    monkeypatch.setattr(tools, "_step_type", lambda run: run["step_type"])
    monkeypatch.setattr(tools.tool_registry, "kind_for_step", lambda *_: ("read_write", None))
    monkeypatch.setattr(tools.tool_registry, "tool_names", lambda *_: [])
    monkeypatch.setattr(tools.tr_self_check_service, "available", lambda doc_id: True)
    base = {"doc_ref": "p.default.0001.0003-TR", "step_type": "TR"}
    edit = {item["name"] for item in tools.definitions_for_run({**base, "action_scope": "edit"})}
    assert set(tools.SELF_CHECK_NAMES) <= edit
    review = {item["name"] for item in tools.definitions_for_run({**base, "action_scope": "review"})}
    assert set(tools.SELF_CHECK_NAMES).isdisjoint(review)
    ts = {item["name"] for item in tools.definitions_for_run({**base, "step_type": "TS", "action_scope": "edit"})}
    assert set(tools.SELF_CHECK_NAMES).isdisjoint(ts)


def test_worker_call_uses_live_token_and_no_bundle(monkeypatch):
    doc_id = "p.default.0001.0003-TR"
    run = {"action_scope": "edit", "doc_ref": doc_id, "group_id": "p.default.0001", "project_id": "p"}
    token = {"action_scope": "edit", "doc_ref": doc_id, "group_id": "p.default.0001", "project": "p", "issued_to": "user"}
    monkeypatch.setattr(tools.token_service, "verify", lambda _: token)
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _: {"type_code": "TR"})
    monkeypatch.setattr(tools.tr_self_check_service, "start", lambda *args: {"self_check_run_id": "scr_1", "status": "pending"})
    monkeypatch.setattr(tools.source_bundle_access_service, "access", lambda *args: pytest.fail("Bundle ensure called"))
    status, result = tools.self_check_call(run, "live-token", "run_self_check", {"program": "pytest"})
    assert status == 202
    assert result["self_check_run_id"] == "scr_1"
    token["action_scope"] = "review"
    with pytest.raises(tools.ToolError, match="selfcheck_forbidden"):
        tools.self_check_call(run, "live-token", "run_self_check", {"program": "pytest"})
