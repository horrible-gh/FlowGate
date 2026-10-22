"""flowgate.v02.0089 T0004: server secrets never reach a child process by inheritance.

NR0003 §5. Fake server secrets (`FAKE_*_DO_NOT_USE`) are put into the parent env and
every fixed spawn path is checked twice where it matters:

A. the env handed to Popen / executor.spawn is captured right before the spawn: env is
   explicit, no fake value appears anywhere (value scan, case/name independent), no
   blocked name is present, caller-injected and allowed variables are kept exactly;
B. a real child compares its own os.environ against the fakes and prints only counts,
   booleans and blocked NAMES -- never a value.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import qa_routes  # noqa: E402
from modules.flow_gate.services import process_runner  # noqa: E402
from modules.flow_gate.services import ai_provider_probe_service as probe_service  # noqa: E402
from modules.flow_gate.services import spec_execution_service as execution  # noqa: E402
from modules.flow_gate.services import test_run_service  # noqa: E402
from modules.flow_gate.services import tr_self_check_executor as executor  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_cli  # noqa: E402
from modules.flow_gate.settings.routers import env_vars_commands  # noqa: E402

FAKE_SECRETS = {
    "SECRET_KEY": "FAKE_SECRET_KEY_DO_NOT_USE",
    "FLOWGATE_TOKEN_PEPPER_ACTIVE_ID": "FAKE_PEPPER_ACTIVE_ID_DO_NOT_USE",
    "FLOWGATE_TOKEN_PEPPER_v1": "FAKE_TOKEN_PEPPER_LOWER_DO_NOT_USE",
    "FLOWGATE_TOKEN_PEPPER_V1": "FAKE_TOKEN_PEPPER_UPPER_DO_NOT_USE",
    "FLOWGATE_AGENT_PEPPER": "FAKE_AGENT_PEPPER_DO_NOT_USE",
    "FLOWGATE_AGENT_PEPPER_ID": "FAKE_AGENT_PEPPER_ID_DO_NOT_USE",
    "FLOWGATE_GIT_ENCRYPT_KEY": "FAKE_GIT_ENCRYPT_KEY_DO_NOT_USE",
    "FLOWGATE_TOTP_ENCRYPT_KEY": "FAKE_TOTP_ENCRYPT_KEY_DO_NOT_USE",
    "FLOWGATE_AI_ENCRYPT_KEY_PREV": "FAKE_AI_ENCRYPT_KEY_PREV_DO_NOT_USE",
    "FLOWGATE_AI_EVALUATION_HMAC_KEY": "FAKE_EVALUATION_HMAC_KEY_DO_NOT_USE",
    "FLOWGATE_STORAGE_DIR": "FAKE_STORAGE_DIR_DO_NOT_USE",
    "DB_PASSWORD": "FAKE_DB_PASSWORD_DO_NOT_USE",
    "DB_USER": "FAKE_DB_USER_DO_NOT_USE",
}
FAKE_VALUES = tuple(FAKE_SECRETS.values())
BLOCKED_NAMES = sorted({name.upper() for name in FAKE_SECRETS})
# Inherited, allowed under both policies.
SHARED_KEPT = {
    "HTTPS_PROXY": "http://proxy.invalid:3128",
    "ANTHROPIC_API_KEY": "vendor-key-fake-kept",
}
# Inherited project variable: kept by "default", dropped by the "cli" allowlist.
PROJECT_VAR = ("PROJECT_SETTING_0089", "project-value-kept")
# A vendor PREFIX that is not a named credential: dropped by the "cli" allowlist.
UNLISTED_VENDOR_VAR = ("GH_UNLISTED_0089", "vendor-prefix-dropped")


@pytest.fixture
def fake_server_env(monkeypatch):
    for name, value in {**FAKE_SECRETS, **SHARED_KEPT}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(*PROJECT_VAR)
    monkeypatch.setenv(*UNLISTED_VENDOR_VAR)


class _Stop(BaseException):
    """Ends a launch right after the spawn; not an Exception, so nothing swallows it."""


class _Recorder:
    def __init__(self):
        self.calls = []

    @property
    def kwargs(self):
        assert len(self.calls) == 1, len(self.calls)
        return self.calls[0][1]


def _record_popen(monkeypatch, *, proc_factory=None, raise_exc=None):
    recorder = _Recorder()

    def fake_popen(args, **kwargs):
        recorder.calls.append((args, kwargs))
        if raise_exc is not None:
            raise raise_exc
        return proc_factory() if proc_factory else _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return recorder


class _FakeProc:
    returncode = 0
    pid = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def communicate(self, input=None, timeout=None):
        return b"", b""


class _Owner:
    active = False

    def __init__(self, run_id=None):
        self.run_id = run_id

    def creationflags(self, existing=0):
        return existing

    def attach(self, _proc):
        return False

    def close(self):
        pass


def _assert_isolated(env, *, explicit, kept, dropped=()):
    assert env is not None, "env must be explicit: a missing env inherits the server env"
    leaked = sorted(key for key, value in env.items() if any(f in str(value) for f in FAKE_VALUES))
    assert leaked == []
    names = {key.upper() for key in env}
    assert sorted(names & set(BLOCKED_NAMES)) == []
    inherited_flowgate = sorted(key for key in env
                                if key.upper().startswith("FLOWGATE_") and key not in explicit)
    assert inherited_flowgate == []
    for key, value in explicit.items():
        assert env.get(key) == value, key
    for key, value in kept.items():
        assert env.get(key) == value, key
    for key in dropped:
        assert key.upper() not in names, key
    assert env.get("PATH") == os.environ["PATH"]
    if os.name == "nt":
        for key in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
            if key in os.environ:
                assert env.get(key) == os.environ[key], key


CLI_KEPT = SHARED_KEPT
CLI_DROPPED = (PROJECT_VAR[0], UNLISTED_VENDOR_VAR[0])
DEFAULT_KEPT = {**SHARED_KEPT, PROJECT_VAR[0]: PROJECT_VAR[1], UNLISTED_VENDOR_VAR[0]: UNLISTED_VENDOR_VAR[1]}


# ── C. policy units ──────────────────────────────────────────────────────────

_BASE = {
    "PATH": "/bin", "SystemRoot": "C:\\Windows", "HOME": "/home/u", "LC_ALL": "C.UTF-8",
    "http_proxy": "http://p:1", "ANTHROPIC_API_KEY": "vendor", "GH_TOKEN": "gh",
    "GH_ENTERPRISE_TOKEN": "gh-prefix", "GITHUB_ACTIONS": "true", "SOME_RANDOM_VAR": "x",
    "secret_key": "FAKE_SECRET_KEY_DO_NOT_USE",
    "flowgate_token_pepper_v1": "FAKE_TOKEN_PEPPER_LOWER_DO_NOT_USE",
    "FLOWGATE_TOKEN_PEPPER_V2": "FAKE_TOKEN_PEPPER_UPPER_DO_NOT_USE",
    "Db_Password": "FAKE_DB_PASSWORD_DO_NOT_USE",
    "FLOWGATE_TEST_PORT": "inherited-not-contract",
}


def test_cli_policy_is_an_allowlist_with_named_vendor_credentials():
    env = process_runner.child_env({"FLOWGATE_TOKEN": "raw-fake"},
                                   policy=process_runner.CHILD_ENV_POLICY_CLI, base=_BASE)
    assert env == {"PATH": "/bin", "SystemRoot": "C:\\Windows", "HOME": "/home/u",
                   "LC_ALL": "C.UTF-8", "http_proxy": "http://p:1",
                   "ANTHROPIC_API_KEY": "vendor", "GH_TOKEN": "gh", "FLOWGATE_TOKEN": "raw-fake"}


def test_default_policy_is_a_server_secret_denylist():
    env = process_runner.child_env({"FLOWGATE_TEST_PORT": "4242"}, base=_BASE)
    assert sorted(env) == sorted(["PATH", "SystemRoot", "HOME", "LC_ALL", "http_proxy",
                                  "ANTHROPIC_API_KEY", "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
                                  "GITHUB_ACTIONS", "SOME_RANDOM_VAR", "FLOWGATE_TEST_PORT"])
    assert env["FLOWGATE_TEST_PORT"] == "4242"


def test_explicit_keys_replace_case_variants_and_are_never_filtered():
    env = process_runner.child_env({"Path": "/explicit", "FLOWGATE_SCRATCH": "s"},
                                   policy=process_runner.CHILD_ENV_POLICY_CLI, base=_BASE)
    assert [key for key in env if key.upper() == "PATH"] == ["Path"]
    assert env["Path"] == "/explicit" and env["FLOWGATE_SCRATCH"] == "s"


def test_operator_passthrough_cannot_readmit_server_secrets():
    base = {**_BASE, "CORP_TOOL_HOME": "/opt/corp",
            process_runner.CLI_ENV_PASSTHROUGH_VAR:
                " corp_tool_home, SECRET_KEY, flowgate_token_pepper_v1, DB_PASSWORD, FLOWGATE_TEST_PORT "}
    env = process_runner.child_env({}, policy=process_runner.CHILD_ENV_POLICY_CLI, base=base)
    assert env["CORP_TOOL_HOME"] == "/opt/corp"
    assert not [key for key in env if key.upper().startswith(("SECRET_KEY", "FLOWGATE_", "DB_"))]


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError):
        process_runner.child_env({}, policy="none")


# ── A. spawn-time capture per path ───────────────────────────────────────────

@pytest.mark.parametrize("env_arg", [None, {"FLOWGATE_TOKEN": "raw-fake"}])
def test_popen_kwargs_always_sets_a_filtered_env(fake_server_env, tmp_path, env_arg):
    kwargs = process_runner.popen_kwargs(tmp_path, env_arg)
    _assert_isolated(kwargs.get("env"), explicit=env_arg or {}, kept=DEFAULT_KEPT)
    shared = test_run_service._popen_kwargs(tmp_path, env_arg, include_stdio=False)
    _assert_isolated(shared.get("env"), explicit=env_arg or {}, kept=DEFAULT_KEPT)


def test_run_command_with_env_none_spawns_with_explicit_filtered_env(fake_server_env, monkeypatch, tmp_path):
    recorder = _record_popen(monkeypatch, raise_exc=_Stop())
    with pytest.raises(_Stop):
        process_runner.run_command("gate", tmp_path, 5, None)
    _assert_isolated(recorder.kwargs.get("env"), explicit={}, kept=DEFAULT_KEPT)


def _cli_run(tmp_path):
    return {"run_id": "r", "raw_token": "raw-fake", "scratch_dir": str(tmp_path),
            "token_scratch_dir": str(tmp_path / "token"), "api_base_url": "http://h/flowgate/api/v1",
            "cancel_event": threading.Event(), "doc_ref": "d", "action_scope": "new"}


def _patch_cli_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(provider_cli, "_canonicalize_cli_prompt",
                        lambda prompt, base: (prompt, "http://agent/flowgate/api/v1"))
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda *_a: (
        {"effective_command": "agent", "agent_cwd": str(tmp_path), "spawn_cwd": str(tmp_path)}, "valid"))
    monkeypatch.setattr(provider_cli, "_audit_cli_launch", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_start_progress_watchdog", lambda *_a: (None, None))
    monkeypatch.setattr(provider_cli, "_stop_progress_watchdog", lambda *_a: None)
    monkeypatch.setattr(provider_cli, "_absolute_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(provider_cli.process_runner, "WindowsProcessOwner", _Owner)
    monkeypatch.setattr(provider_cli.process_runner, "kill_process_tree", lambda *_a: None)

    def communicate(*_a, **_k):
        raise _Stop()

    monkeypatch.setattr(provider_cli.process_runner, "communicate_with_cleanup", communicate)


def test_cli_execute_spawns_with_cli_allowlist(fake_server_env, monkeypatch, tmp_path):
    _patch_cli_launch(monkeypatch, tmp_path)
    recorder = _record_popen(monkeypatch)
    with pytest.raises(_Stop):
        provider_cli._cli_execute({"cli_command": "claude", "kind": "claude"}, "PROMPT", _cli_run(tmp_path))
    scratch_env = provider_cli._cli_scratch_env(tmp_path)
    _assert_isolated(recorder.kwargs.get("env"), explicit={
        "FLOWGATE_TOKEN": "raw-fake", "FLOWGATE_SCRATCH": str(tmp_path / "token"),
        "FLOWGATE_API_BASE": "http://agent/flowgate/api/v1", **scratch_env,
    }, kept=CLI_KEPT, dropped=CLI_DROPPED)


def test_evaluation_probe_spawns_with_cli_allowlist(fake_server_env, monkeypatch, tmp_path):
    _patch_cli_launch(monkeypatch, tmp_path)
    recorder = _record_popen(monkeypatch)
    handle = SimpleNamespace(bind=lambda _kill: True, cancelled=False)
    with pytest.raises(_Stop):
        provider_cli.probe_cli_completion(project_id="p", kind="claude", command="claude", prompt="hi",
                                          timeout=5, handle=handle, scratch=tmp_path, run_id="r")
    _assert_isolated(recorder.kwargs.get("env"), explicit={
        "FLOWGATE_TOKEN": "", "FLOWGATE_API_BASE": "", "FLOWGATE_BUNDLE_API": "",
        "FLOWGATE_SCRATCH": str(tmp_path), **provider_cli._cli_scratch_env(tmp_path),
    }, kept=CLI_KEPT, dropped=CLI_DROPPED)


def test_connection_probe_spawns_with_cli_allowlist(fake_server_env, monkeypatch, tmp_path):
    recorder = _record_popen(monkeypatch)
    explicit = {"FLOWGATE_TOKEN": "", "FLOWGATE_SCRATCH": str(tmp_path), "FLOWGATE_API_BASE": "",
                "FLOWGATE_OS": "posix", "FLOWGATE_SHELL": "sh"}
    probe_service._run_probe("custom-cli", tmp_path, 5, dict(explicit), "hi")
    _assert_isolated(recorder.kwargs.get("env"), explicit=explicit, kept=CLI_KEPT, dropped=CLI_DROPPED)


def test_qa_command_dispatch_spawns_with_cli_allowlist(fake_server_env, monkeypatch, tmp_path):
    monkeypatch.setattr(qa_routes.db_commands, "get_by_id", lambda _cid: {"template": "t"})
    monkeypatch.setattr(qa_routes, "resolve_template", lambda _t: {"resolved": "agent"})
    recorder = _record_popen(monkeypatch)
    qa_routes._execute_command("cmd_1", "raw-fake", str(tmp_path))
    _assert_isolated(recorder.kwargs.get("env"),
                     explicit={"FLOWGATE_TOKEN": "raw-fake", "FLOWGATE_SCRATCH": str(tmp_path)},
                     kept=CLI_KEPT, dropped=CLI_DROPPED)


@pytest.mark.parametrize("body,explicit", [
    (None, {}),
    ({"env_overrides": {"FLOWGATE_TOKEN": "raw-fake", "FLOWGATE_SCRATCH": "s"}},
     {"FLOWGATE_TOKEN": "raw-fake", "FLOWGATE_SCRATCH": "s"}),
])
def test_settings_command_execute_spawns_with_cli_allowlist(fake_server_env, monkeypatch, body, explicit):
    monkeypatch.setattr(env_vars_commands._cmd_db, "get_by_id", lambda _cid: {"template": "t"})
    monkeypatch.setattr(env_vars_commands, "resolve_template", lambda _t: {"resolved": "agent"})
    captured = []

    def fake_run(args, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = env_vars_commands.execute_command("cmd_1", body=body, user={"is_admin": True})
    assert result["return_code"] == 0 and len(captured) == 1
    _assert_isolated(captured[0].get("env"), explicit=explicit, kept=CLI_KEPT, dropped=CLI_DROPPED)


def _spec_env(monkeypatch, tmp_path):
    captured = {}

    def fake_spawn(argv, root, env, timeout, run_id):
        captured.update(argv=argv, env=env)
        raise executor.OwnershipError("captured")

    monkeypatch.setattr(executor, "spawn", fake_spawn)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = execution.ExistingRunnerAdapter.run_pytest("tests/test_x.py::t", tmp_path, scratch, None)
    assert result[0] == "error"
    return captured, scratch


def _spec_explicit(env, scratch):
    expected = test_run_service._execution_env(int(env["FLOWGATE_TEST_PORT"]), scratch)
    assert set(expected) == {"FLOWGATE_TEST_PORT", "FLOWGATE_TEST_SCRATCH", "FLOWGATE_TEST_OS",
                             "FLOWGATE_TEST_SHELL", "PYTEST_ADDOPTS"}
    return expected


def test_spec_run_pytest_hands_spawn_a_denylisted_env(fake_server_env, monkeypatch, tmp_path):
    captured, scratch = _spec_env(monkeypatch, tmp_path)
    env = captured["env"]
    _assert_isolated(env, explicit=_spec_explicit(env, scratch), kept=DEFAULT_KEPT)
    assert captured["argv"][0] == (shutil.which("python", path=env["PATH"]) or sys.executable)


class _FakeSupervisor:
    """POSIX supervisor stand-in: records the stdin JSON line, then reports no start."""

    def __init__(self):
        self.written = b""
        self.stdin = SimpleNamespace(write=self._write, flush=lambda: None, close=lambda: None)
        self.stdout = SimpleNamespace(readline=lambda: b'{"event": "refused"}\n')

    def _write(self, data):
        self.written += data

    def wait(self, timeout=None):
        return 0


def test_spec_env_stays_isolated_in_every_spawn_sink(fake_server_env, monkeypatch, tmp_path):
    # The real executor.spawn (unchanged by this T) with the env run_pytest built.
    supervisors = []

    def supervisor():
        supervisors.append(_FakeSupervisor())
        return supervisors[-1]

    if os.name == "nt":
        recorder = _record_popen(monkeypatch, raise_exc=RuntimeError("captured"))
    else:
        recorder = _record_popen(monkeypatch, proc_factory=supervisor)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert execution.ExistingRunnerAdapter.run_pytest("tests/test_x.py::t", tmp_path, scratch, None)[0] == "error"
    sink_env = recorder.kwargs.get("env")
    explicit = _spec_explicit(sink_env, scratch)
    if os.name == "nt":
        _assert_isolated(sink_env, explicit=explicit, kept=DEFAULT_KEPT)  # Windows target Popen env=
        return
    _assert_isolated(sink_env, explicit={**explicit, "PYTHONPATH": sink_env["PYTHONPATH"]},
                     kept=DEFAULT_KEPT)  # POSIX supervisor Popen env=
    line = json.loads(supervisors[0].written.decode("utf-8"))
    _assert_isolated(line["env"], explicit=explicit, kept=DEFAULT_KEPT)  # supervisor -> target JSON


# ── B. real children compare their own environment ───────────────────────────

_STUB = """import json, os, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
names = {key.upper() for key in os.environ}
print(json.dumps({
    "leak": sum(1 for value in os.environ.values() if any(f in value for f in spec["secrets"])),
    "blocked_present": sorted(name for name in spec["blocked_names"] if name.upper() in names),
    "kept": {key: os.environ.get(key) == value for key, value in spec["expected"].items()},
}))
"""


def _stub(tmp_path, *, expected, blocked):
    stub = tmp_path / "env_stub.py"
    stub.write_text(_STUB, encoding="utf-8")
    spec = tmp_path / "env_spec.json"
    spec.write_text(json.dumps({"secrets": list(FAKE_VALUES), "blocked_names": list(blocked),
                                "expected": expected}), encoding="utf-8")
    return stub, spec


def _verdict(output: str) -> dict:
    line = [row for row in output.strip().splitlines() if row.startswith("{")][-1]
    verdict = json.loads(line)
    assert verdict["leak"] == 0
    assert verdict["blocked_present"] == []
    assert all(verdict["kept"].values()), verdict["kept"]
    return verdict


def test_real_cli_child_sees_no_server_secret(fake_server_env, tmp_path):
    explicit = {"FLOWGATE_TOKEN": "raw-fake", "FLOWGATE_SCRATCH": str(tmp_path)}
    stub, spec = _stub(tmp_path, expected={**explicit, **CLI_KEPT, "PATH": os.environ["PATH"]},
                       blocked=BLOCKED_NAMES + list(CLI_DROPPED))
    kwargs = provider_cli._cli_popen_kwargs({"agent_cwd": str(tmp_path), "spawn_cwd": str(tmp_path)},
                                            explicit)
    proc = subprocess.Popen(f'"{sys.executable}" -I "{stub}" "{spec}"', **kwargs)
    stdout, _stderr = proc.communicate(input=b"", timeout=60)
    assert proc.returncode == 0
    _verdict(process_runner.safe_decode(stdout))


def test_real_run_command_env_none_child_sees_no_server_secret(fake_server_env, tmp_path):
    stub, spec = _stub(tmp_path, expected={**DEFAULT_KEPT, "PATH": os.environ["PATH"]},
                       blocked=BLOCKED_NAMES)
    timed_out, exit_code, output = process_runner.run_command(
        f'"{sys.executable}" -I "{stub}" "{spec}"', tmp_path, 60, None)
    assert not timed_out and exit_code == 0
    _verdict(output)


def test_real_spec_spawn_child_sees_no_server_secret(fake_server_env, monkeypatch, tmp_path):
    real_spawn = executor.spawn
    captured, scratch = _spec_env(monkeypatch, tmp_path)
    env = captured["env"]
    stub, spec = _stub(tmp_path, expected={**_spec_explicit(env, scratch), **DEFAULT_KEPT,
                                           "PATH": os.environ["PATH"]}, blocked=BLOCKED_NAMES)
    control, _owner = real_spawn([sys.executable, "-I", str(stub), str(spec)], tmp_path, env, 60,
                                 "scr_0089")
    try:
        outcome = executor.wait(control, 60, threading.Event())
    finally:
        control.close()
    assert outcome.exit_code == 0, outcome.stderr_tail[-500:]
    _verdict(outcome.stdout_tail)
