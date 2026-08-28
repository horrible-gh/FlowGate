from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import process_runner  # noqa: E402


def _run(monkeypatch, tmp_path, *, kind="codex", with_group=True):
    project_id = "project-0475"
    run_id = "aiv_20260828_000011"
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _p: {"project_name": "p0475"})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path / "storage")
    scratch = svc._create_scratch(project_id, run_id)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(svc.db_git, "get_config", lambda _p: {"enabled": with_group})
    monkeypatch.setattr(svc, "_is_group_worktree", lambda *_a: with_group)
    run = {
        "project_id": project_id, "group_id": "flowgate.default.0475" if with_group else None,
        "run_id": run_id, "scratch_dir": str(scratch), "source_root": str(worktree),
        "raw_token": "TOKEN_SENTINEL", "api_base_url": "",
    }
    provider = {"kind": kind, "cli_command": f"{kind} COMMAND_SENTINEL"}
    return run, provider, scratch, worktree


def _audit(caplog):
    records = [r.getMessage() for r in caplog.records if "cli spawn decision" in r.getMessage()]
    assert len(records) == 1
    return json.loads(records[0].split("decision ", 1)[1])


@pytest.mark.parametrize("kind", ["codex", "claude"])
def test_local_group_worktree_is_the_only_spawn_and_agent_cwd(monkeypatch, tmp_path, caplog, kind):
    run, provider, scratch, worktree = _run(monkeypatch, tmp_path, kind=kind)
    seen = {}
    def stop(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        raise OSError("expected")
    monkeypatch.setattr("subprocess.Popen", stop)
    caplog.set_level(logging.INFO, logger=svc.__name__)
    status, _ = svc._cli_execute(provider, "PROMPT_SENTINEL", run)
    assert status == "spawn_failed"
    event = _audit(caplog)
    expected = str(worktree.resolve())
    assert seen["kwargs"]["cwd"] == expected
    assert event["decision"] == "launch"
    assert event["provider_kind"] == kind and event["cwd_source"] == "group_worktree"
    assert event["spawn_cwd"] == event["agent_cwd"] == expected
    assert event["cwd_transition"] == "none" and event["is_unc"] is False
    assert seen["kwargs"]["cwd"] not in {os.getcwd(), os.getenv("TEMP"), str(scratch)}


def test_manifest_owned_scratch_is_used_without_group(monkeypatch, tmp_path):
    run, provider, scratch, _ = _run(monkeypatch, tmp_path, kind="claude", with_group=False)
    decision, reason = svc._resolve_cli_launch(provider, run, "claude -p -")
    assert reason == "valid"
    expected = str(scratch.resolve())
    assert decision["cwd_source"] == "run_scratch"
    assert decision["spawn_cwd"] == decision["agent_cwd"] == expected


@pytest.mark.parametrize("mutation", ["relative", "missing", "wrong_run", "root"])
def test_invalid_scratch_blocks_before_popen(monkeypatch, tmp_path, caplog, mutation):
    run, provider, scratch, _ = _run(monkeypatch, tmp_path, with_group=False)
    if mutation == "relative":
        run["scratch_dir"] = "relative"
    elif mutation == "missing":
        (scratch / svc.SCRATCH_MANIFEST_NAME).unlink()
    elif mutation == "wrong_run":
        data = json.loads((scratch / svc.SCRATCH_MANIFEST_NAME).read_text())
        data["run_id"] = "aiv_20260828_999999"
        (scratch / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(data))
    else:
        run["scratch_dir"] = str(scratch.parent)
    called = []
    monkeypatch.setattr("subprocess.Popen", lambda *_a, **_k: called.append(True))
    caplog.set_level(logging.INFO, logger=svc.__name__)
    status, detail = svc._cli_execute(provider, "PROMPT_SENTINEL", run)
    assert status == "spawn_failed" and not called
    event = _audit(caplog)
    assert event["decision"] == "blocked" and event["reason"].startswith("scratch_")
    assert "relative" not in json.dumps(event)
    assert "PROMPT_SENTINEL" not in detail


def test_integrated_group_never_falls_back_to_base_or_scratch(monkeypatch, tmp_path):
    run, provider, _, _ = _run(monkeypatch, tmp_path)
    monkeypatch.setattr(svc, "_is_group_worktree", lambda *_a: False)
    decision, reason = svc._resolve_cli_launch(provider, run, "codex exec -")
    assert decision is None and reason == "group_worktree_identity_invalid"


def test_unc_separates_bootstrap_and_agent_cwd(monkeypatch, tmp_path):
    run, provider, scratch, _ = _run(monkeypatch, tmp_path)
    unc = Path(r"\\server\share\group")
    run["source_root"] = str(unc)
    monkeypatch.setattr(Path, "is_dir", lambda self: True if str(self).startswith("\\\\") else Path.exists(self))
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self if str(self).startswith("\\\\") else Path(os.path.abspath(self)))
    monkeypatch.setattr(process_runner, "unc_safe_shell", lambda cmd, root: (f'pushd "{root}" && {cmd}', None))
    monkeypatch.setattr(svc, "_shell_kind", lambda: "windows_cmd")
    decision, reason = svc._resolve_cli_launch(provider, run, "codex exec -")
    assert reason == "valid"
    assert decision["spawn_cwd"] == str(scratch.resolve())
    assert decision["agent_cwd"] == str(unc)
    assert decision["cwd_transition"] == "pushd"
    assert decision["shell_kind"] == "windows_cmd" and decision["is_unc"] is True
    assert decision["effective_command"].startswith("pushd ") and "&& codex" in decision["effective_command"]


def test_audit_is_one_json_event_and_excludes_all_secrets(monkeypatch, tmp_path, caplog):
    run, provider, _, _ = _run(monkeypatch, tmp_path, kind="claude")
    provider["display_name"] = "PROVIDER_SECRET\r\nFORGED"
    run["authorization"] = "Authorization: Bearer AUTH_SECRET"
    caplog.set_level(logging.INFO, logger=svc.__name__)
    monkeypatch.setattr("subprocess.Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError()))
    status, detail = svc._cli_execute(provider, "PROMPT_SENTINEL", run)
    assert status == "spawn_failed"
    event = _audit(caplog)
    assert event["schema"] == "flowgate.external-cli-launch.v1"
    text = "\n".join(r.getMessage() for r in caplog.records) + str(detail)
    for secret in ("TOKEN_SENTINEL", "PROMPT_SENTINEL", "COMMAND_SENTINEL", "PROVIDER_SECRET", "AUTH_SECRET"):
        assert secret not in text
    assert not ({"command", "argv", "env", "prompt", "token"} & set(event))


def test_posix_shell_is_fact_not_command_guess(monkeypatch, tmp_path):
    run, provider, _, _ = _run(monkeypatch, tmp_path, kind="claude")
    provider["cli_command"] = "git-bash-looking-name pwsh powershell"
    monkeypatch.setattr(svc, "_shell_kind", lambda: "posix_sh")
    decision, _ = svc._resolve_cli_launch(provider, run, provider["cli_command"])
    assert decision["shell_kind"] == "posix_sh"
    assert decision["cwd_transition"] == "none"