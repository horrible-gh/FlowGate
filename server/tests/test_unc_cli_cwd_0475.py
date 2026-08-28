from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import process_runner  # noqa: E402


def _run(scratch: Path, source_root, *, token="RAW_TOKEN_SENTINEL", run_id="run-0475"):
    return {
        "run_id": run_id,
        "scratch_dir": str(scratch),
        "source_root": source_root,
        "raw_token": token,
        "api_base_url": "",
    }


def _decision(caplog):
    record = next(r for r in caplog.records if "cli spawn decision" in r.getMessage())
    return json.loads(record.getMessage().split("decision ", 1)[1])


def test_windows_unc_cli_uses_managed_scratch_and_safe_observation(monkeypatch, caplog):
    run_id = "aiv_20260828_999998"
    managed_base = Path.home() / ".flowgate-test-managed-0475" / run_id
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _project_id: {"project_name": "p0475"})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *_a, **_kw: managed_base)
    scratch = svc._create_scratch("project-0475", run_id)
    unc = r"\\server\share with space\한글"
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: True if str(self) == unc else real_is_dir(self))
    monkeypatch.setattr(process_runner.os, "name", "nt")
    monkeypatch.setattr(process_runner.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    seen = {}
    secret = "PROVIDER_SECRET_SENTINEL"
    command = "worker COMMAND_SENTINEL"
    prompt = "PROMPT_SENTINEL"

    def reject_spawn(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        raise OSError("spawn failed " + secret + " COMMAND_SENTINEL RAW_TOKEN_SENTINEL")

    monkeypatch.setattr("subprocess.Popen", reject_spawn)
    caplog.set_level(logging.INFO, logger=svc.__name__)
    try:
        status, detail = svc._cli_execute(
            {"kind": "custom", "cli_command": command, "api_key": secret}, prompt,
            _run(scratch, unc),
        )
        assert status == "spawn_failed"
        assert detail == "unable to start CLI process"
        assert seen["cmd"] == f'pushd "{unc}" && {command}'
        expected = str(scratch.resolve())
        assert seen["kwargs"]["cwd"] == expected
        assert seen["kwargs"]["cwd"] is not None
        assert expected != unc
        assert seen["kwargs"]["shell"] is True
        assert seen["kwargs"]["creationflags"] == 512
        assert seen["cmd"].endswith(command)
        assert seen["kwargs"]["env"]["FLOWGATE_SCRATCH"] == str(scratch)
        event = _decision(caplog)
        assert event == {
            "run_id": "run-0475",
            "resolved_root": unc,
            "effective_cwd": expected,
            "is_unc": True,
        }
        assert event["effective_cwd"] == seen["kwargs"]["cwd"]
        assert Path(tempfile.gettempdir()).resolve() not in scratch.resolve().parents
        assert Path.cwd().resolve() not in scratch.resolve().parents
        exposed = caplog.text + detail
        for sentinel in ("RAW_TOKEN_SENTINEL", "PROMPT_SENTINEL", "COMMAND_SENTINEL", secret):
            assert sentinel not in exposed
    finally:
        shutil.rmtree(managed_base.parent, ignore_errors=True)


@pytest.mark.parametrize("platform_name", ["nt", "posix"])
def test_local_root_keeps_root_cwd_without_pushd(monkeypatch, tmp_path, caplog, platform_name):
    root = tmp_path / "local root"
    scratch = tmp_path / "scratch"
    root.mkdir()
    scratch.mkdir()
    seen = {}
    if platform_name == "posix":
        monkeypatch.setattr(process_runner, "unc_safe_shell", lambda cmd, cwd: (cmd, str(cwd)))
    monkeypatch.setattr(process_runner.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)

    def reject_spawn(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        raise OSError("expected")

    monkeypatch.setattr("subprocess.Popen", reject_spawn)
    caplog.set_level(logging.INFO, logger=svc.__name__)
    status, _ = svc._cli_execute(
        {"kind": "custom", "cli_command": "worker"}, "prompt", _run(scratch, str(root))
    )
    assert status == "spawn_failed"
    assert seen["cmd"] == "worker"
    assert seen["kwargs"]["cwd"] == str(root)
    event = _decision(caplog)
    assert event["resolved_root"] == str(root)
    assert event["effective_cwd"] == seen["kwargs"]["cwd"]
    assert event["is_unc"] is False


def test_missing_root_falls_back_to_scratch_and_warns(monkeypatch, tmp_path, caplog):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    missing = tmp_path / "missing"
    seen = {}

    def reject_spawn(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        raise OSError("expected")

    monkeypatch.setattr("subprocess.Popen", reject_spawn)
    caplog.set_level(logging.INFO, logger=svc.__name__)
    status, _ = svc._cli_execute(
        {"kind": "custom", "cli_command": "worker"}, "prompt", _run(scratch, str(missing))
    )
    assert status == "spawn_failed"
    assert seen["kwargs"]["cwd"] == str(scratch)
    assert seen["kwargs"]["env"]["FLOWGATE_SCRATCH"] == str(scratch)
    assert "source mirror missing" in caplog.text
    event = _decision(caplog)
    assert event["resolved_root"] == str(missing)
    assert event["effective_cwd"] == seen["kwargs"]["cwd"]
    assert event["is_unc"] is False


def test_observation_escapes_crlf_as_one_log_event(monkeypatch, tmp_path, caplog):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    malicious = str(tmp_path / "missing\r\nFAKE_LOG_SENTINEL")
    monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("x")))
    caplog.set_level(logging.INFO, logger=svc.__name__)
    svc._cli_execute({"kind": "custom", "cli_command": "worker"}, "prompt", _run(scratch, malicious))
    records = [r for r in caplog.records if "cli spawn decision" in r.getMessage()]
    assert len(records) == 1
    assert "\\r\\nFAKE_LOG_SENTINEL" in records[0].getMessage()
    assert "\r\nFAKE_LOG_SENTINEL" not in records[0].getMessage()


@pytest.mark.parametrize("scratch_value", ["relative-scratch", r"\\server\share\scratch"])
def test_unc_rejects_nonlocal_or_relative_scratch_without_spawn(monkeypatch, scratch_value):
    unc = r"\\server\share\source"
    monkeypatch.setattr(process_runner, "unc_safe_shell", lambda cmd, root: (f'pushd "{root}" && {cmd}', None))
    monkeypatch.setattr(Path, "is_dir", lambda self: str(self) == unc)
    monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: pytest.fail("must not spawn"))
    status, detail = svc._cli_execute(
        {"kind": "custom", "cli_command": "worker"}, "prompt", _run(Path(scratch_value), unc)
    )
    assert (status, detail) == ("spawn_failed", "managed scratch directory is unavailable")