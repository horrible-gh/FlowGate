from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402


def _root(monkeypatch, tmp_path, project="project-0475"):
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _p: {"project_name": "p0475"})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    return svc._project_scratch_root(project)


def _new(monkeypatch, tmp_path, serial=1):
    _root(monkeypatch, tmp_path)
    run_id = f"aiv_20260828_{serial:06d}"
    return run_id, svc._create_scratch("project-0475", run_id)


def _manifest(path):
    return json.loads((path / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))


def _complete(path, when):
    data = _manifest(path)
    data["completed_at"] = when.isoformat()
    (path / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")


def test_create_scratch_has_atomic_manifest_and_owned_subdirs(monkeypatch, tmp_path):
    run_id, scratch = _new(monkeypatch, tmp_path)
    manifest = _manifest(scratch)
    assert (scratch / "tmp").is_dir() and (scratch / "cache").is_dir()
    assert not (scratch / (svc.SCRATCH_MANIFEST_NAME + ".tmp")).exists()
    assert manifest == {
        "schema": 1, "owner": "flowgate.ai-invoke", "project_id": "project-0475",
        "run_id": run_id, "scratch_path": str(scratch.resolve()),
        "created_at": manifest["created_at"], "completed_at": None,
        "policy": {"retention_days": 7, "delete_on_complete": True},
    }
    serialized = json.dumps(manifest)
    for secret in ("TOKEN_SENTINEL", "PROMPT_SENTINEL", "COMMAND_SENTINEL"):
        assert secret not in serialized


def test_create_rejects_path_injection(monkeypatch, tmp_path):
    _root(monkeypatch, tmp_path)
    for value in ("../escape", "aiv_20260828_000001/escape", "C:\\escape"):
        with pytest.raises(ValueError):
            svc._create_scratch("project-0475", value)


def test_cli_env_is_fully_run_owned(monkeypatch, tmp_path):
    run_id, scratch = _new(monkeypatch, tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    seen = {}
    def popen(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        raise OSError("expected")
    monkeypatch.setattr("subprocess.Popen", popen)
    status, _ = svc._cli_execute(
        {"kind": "custom", "cli_command": "worker"}, "PROMPT_SENTINEL",
        {"run_id": run_id, "scratch_dir": str(scratch), "source_root": str(source),
         "raw_token": "TOKEN_SENTINEL", "api_base_url": ""},
    )
    assert status == "spawn_failed"
    env = seen["kwargs"]["env"]
    assert env["FLOWGATE_SCRATCH"] == str(scratch)
    for key in ("TMP", "TEMP", "TMPDIR", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "NPM_CONFIG_CACHE"):
        assert Path(env[key]).is_relative_to(scratch)


def test_actual_mention_points_all_artifacts_and_doc_path_to_same_boundary():
    scratch = r"C:\managed\scratch\aiv_20260828_000001"
    out = mention_service.build_mention(
        project="flowgate", module="default", group="0475", parent_type="T",
        parent_doc_number="T0009", parent_title="scratch", parent_doc_id="T0009",
        parent_canonical_doc_id="flowgate.default.0475.0009-T", head_type="TR",
        head_status="pending", scratch_dir=scratch, raw_token="token",
        api_base_url="http://localhost/api/v1", continuous=True, locale="en",
    )
    assert scratch in out
    assert "every non-source artifact" in out
    assert "source tree, server cwd, or OS temp" in out
    assert "absolute path as `doc_path`" in out


def test_review_mention_keeps_all_review_artifacts_inside_run_scratch():
    scratch = r"C:\managed\scratch\aiv_20260828_000001"
    out = mention_service.build_review_mention(
        token_rec={
            "project": "flowgate",
            "group_id": "flowgate.default.0475",
            "scratch_dir": scratch,
        },
        target_doc={
            "doc_id": "flowgate.default.0475.0010-TR",
            "type_code": "TR",
            "seq": 10,
            "title": "scratch report",
            "module": "default",
            "project_id": "project-0475",
        },
        api_base_url="http://localhost/api/v1",
        raw_token="token",
        locale="en",
    )
    assert out is not None
    assert scratch in out
    assert "every non-source artifact" in out
    assert "review dump, JSON, cache, and note" in out
    assert "source tree, server cwd, or OS temp" in out


@pytest.mark.parametrize("mutation", ["missing", "run", "project", "path", "policy"])
def test_delete_requires_exact_manifest_identity(monkeypatch, tmp_path, mutation):
    run_id, scratch = _new(monkeypatch, tmp_path)
    if mutation == "missing":
        (scratch / svc.SCRATCH_MANIFEST_NAME).unlink()
    else:
        data = _manifest(scratch)
        data[{"run":"run_id", "project":"project_id", "path":"scratch_path", "policy":"policy"}[mutation]] = "bad"
        (scratch / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")
    called = []
    monkeypatch.setattr(svc.shutil, "rmtree", lambda *_a, **_k: called.append(True))
    deleted, _ = svc._delete_owned_scratch("project-0475", run_id, scratch)
    assert not deleted and not called and scratch.exists()


def test_valid_success_delete_and_idempotent_absent(monkeypatch, tmp_path):
    run_id, scratch = _new(monkeypatch, tmp_path)
    assert svc._delete_owned_scratch("project-0475", run_id, scratch)[0]
    assert not scratch.exists()
    assert not svc._delete_owned_scratch("project-0475", run_id, scratch)[0]


def test_sweep_seven_day_boundary_and_legacy_preservation(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    old_id, old = _new(monkeypatch, tmp_path, 1)
    young_id, young = _new(monkeypatch, tmp_path, 2)
    edge_id, edge = _new(monkeypatch, tmp_path, 3)
    future_id, future = _new(monkeypatch, tmp_path, 4)
    now = datetime.now(timezone.utc)
    _complete(old, now - timedelta(days=8))
    _complete(young, now - timedelta(days=7) + timedelta(seconds=5))
    _complete(edge, now - timedelta(days=7, seconds=1))
    _complete(future, now + timedelta(days=1))
    legacy = root / "legacy"; legacy.mkdir()
    ordinary = root / "ordinary.txt"; ordinary.write_text("sentinel")
    svc._cleanup_retained_scratches("project-0475")
    assert not old.exists() and not edge.exists()
    assert young.exists() and future.exists() and legacy.exists() and ordinary.exists()


def test_sweep_rechecks_identity_before_delete(monkeypatch, tmp_path):
    run_id, scratch = _new(monkeypatch, tmp_path)
    _complete(scratch, datetime.now(timezone.utc) - timedelta(days=8))
    original = svc._delete_owned_scratch
    def corrupt_then_delete(project_id, candidate_run_id, candidate):
        data = _manifest(candidate); data["run_id"] = "aiv_20260828_999999"
        (candidate / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")
        return original(project_id, candidate_run_id, candidate)
    monkeypatch.setattr(svc, "_delete_owned_scratch", corrupt_then_delete)
    svc._cleanup_retained_scratches("project-0475")
    assert scratch.exists()


def test_symlink_candidate_never_deletes_external_sentinel(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path); root.mkdir(parents=True)
    external = tmp_path / "external"; external.mkdir(); sentinel = external / "keep"; sentinel.write_text("safe")
    candidate = root / "aiv_20260828_000099"
    forged = svc._manifest_for("project-0475", candidate.name, external)
    forged["scratch_path"] = str(external.resolve())
    forged["completed_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    (external / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(forged), encoding="utf-8")
    try:
        candidate.symlink_to(external, target_is_directory=True)
    except OSError:
        monkeypatch.setattr(svc, "_is_reparse_or_symlink", lambda p: p == candidate)
        candidate.mkdir()
    real_rmtree = svc.shutil.rmtree
    def dangerous_following_delete(path):
        resolved = Path(path).resolve()
        if resolved == external.resolve():
            sentinel.unlink(missing_ok=True)
            return
        real_rmtree(path)
    monkeypatch.setattr(svc.shutil, "rmtree", dangerous_following_delete)
    svc._cleanup_retained_scratches("project-0475")
    assert sentinel.read_text() == "safe" and external.exists()


def test_delete_exception_and_partial_failure_are_retained(monkeypatch, tmp_path):
    run_id, scratch = _new(monkeypatch, tmp_path)
    monkeypatch.setattr(svc.shutil, "rmtree", lambda _p: (_ for _ in ()).throw(OSError("secret")))
    assert svc._delete_owned_scratch("project-0475", run_id, scratch) == (False, "delete_failed")
    assert scratch.exists()


def test_safe_log_escapes_crlf_and_excludes_secrets(monkeypatch, tmp_path, caplog):
    _root(monkeypatch, tmp_path)
    caplog.set_level(logging.INFO, logger=svc.__name__)
    svc._safe_scratch_log("project-0475", "bad\r\nTOKEN_SENTINEL", tmp_path / "outside", "skipped", "invalid")
    records = [r.getMessage() for r in caplog.records if "ai-invoke scratch" in r.getMessage()]
    assert len(records) == 1 and "\\r" not in records[0] and "\n" not in records[0]
    for secret in ("TOKEN_SENTINEL", "PROMPT_SENTINEL", "COMMAND_SENTINEL", "PROVIDER_SECRET"):
        assert secret not in records[0]