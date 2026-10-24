"""T#2 short adapters for T#1's isolated delete-state fixture; no Git/lock experiment."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_token_scratch_delete_journal_0090 import owned, PROJECT
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import runtime
from modules.flow_gate.storage import filesystem as storage_fs
from modules.flow_gate.settings import scratch_retention

CREATED = "2026-08-28T01:00:00+00:00"
COMPLETED = "2026-08-28T02:00:00+00:00"


@pytest.fixture
def ai_owned(owned, monkeypatch, tmp_path):
    storage, _, _ = owned
    project_storage = tmp_path / "project-storage"
    project_storage.mkdir()
    monkeypatch.setattr(runtime.storage_paths, "get_storage_root",
                        lambda project_id=None, **_k: project_storage if project_id else storage)
    rows, leases, state = {}, {}, {"live": False}
    monkeypatch.setattr(runtime.db_ai_invoke_runs, "get", lambda run_id: rows.get(run_id))
    monkeypatch.setattr(runtime.db_group_ai_leases, "get_by_run_id", lambda run_id: leases.get(run_id))
    monkeypatch.setattr(runtime.db_projects, "list_projects", lambda: [{"project_id": PROJECT}])
    monkeypatch.setattr(svc, "is_run_live", lambda _run_id: state["live"])
    return storage, project_storage, rows, leases, state


def _new(serial=1):
    run_id = f"aiv_20260828_{serial:06d}"
    path = runtime._create_scratch(PROJECT, run_id)
    manifest_path = path / runtime.SCRATCH_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(created_at=CREATED, completed_at=COMPLETED)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return run_id, path


def _journal(storage, run_id):
    return storage / ".flowgate-gc" / "ai-run" / storage_fs.project_key(PROJECT) / f"{run_id}.json"


def test_owned_delete_uses_global_journal_and_excludes_completion(ai_owned, monkeypatch):
    storage, project_storage, _, _, _ = ai_owned
    run_id, path = _new()
    real_remove = storage_fs.remove_managed_tree
    records = []
    checked = []
    real_check = svc._is_reparse_or_symlink

    def above_sp_is_link(candidate):
        checked.append(candidate)
        return candidate == project_storage.parent or real_check(candidate)

    monkeypatch.setattr(svc, "_is_reparse_or_symlink", above_sp_is_link)

    def observe(target, root):
        record = json.loads(_journal(storage, run_id).read_text(encoding="utf-8"))
        records.append(record)
        assert record["manifest"]["created_at"] == CREATED
        assert record["completed_at"] == COMPLETED
        assert record["eligibility"]["project_storage_root"] == str(project_storage)
        assert not runtime._mark_scratch_completed(PROJECT, run_id, path, CREATED)
        return real_remove(target, root)

    monkeypatch.setattr(storage_fs, "remove_managed_tree", observe)
    assert runtime._delete_owned_scratch(PROJECT, run_id, path) == (True, "deleted")
    assert len(records) == 1 and records[0]["owner"] == "ai-run"
    assert not path.exists() and not _journal(storage, run_id).exists()
    assert not (project_storage / ".flowgate-gc").exists()
    assert project_storage.parent not in checked
    assert all(part in checked for part in (project_storage, project_storage / "scratch", path.parent, path))


def test_pending_precedes_missing_manifest_and_rechecks_retention_and_owners(ai_owned, monkeypatch):
    storage, project_storage, rows, leases, state = ai_owned
    run_id, path = _new()
    real_remove = storage_fs.remove_managed_tree

    def partial(target, _root):
        (Path(target) / runtime.SCRATCH_MANIFEST_NAME).unlink()
        raise PermissionError(13, "injected")

    monkeypatch.setattr(storage_fs, "remove_managed_tree", partial)
    assert runtime._delete_owned_scratch(PROJECT, run_id, path) == (False, "delete_failed")
    assert path.is_dir() and _journal(storage, run_id).is_file()
    monkeypatch.setattr(storage_fs, "remove_managed_tree", real_remove)
    monkeypatch.setattr(runtime, "_validate_scratch_manifest",
                        lambda *_a: pytest.fail("ordinary classification preceded pending"))
    # Restart equivalent: no in-memory authorization is used, just the persisted record.
    with monkeypatch.context() as m:
        m.setattr(scratch_retention, "effective_retention", lambda: timedelta(days=10000))
        runtime._cleanup_retained_scratches(PROJECT)
    assert path.exists() and _journal(storage, run_id).exists()
    state["live"] = True
    runtime._cleanup_retained_scratches(PROJECT)
    state["live"] = False
    leases[run_id] = {"run_id": run_id}
    runtime._cleanup_retained_scratches(PROJECT)
    leases.clear()
    rows[run_id] = {"project_id": PROJECT, "status": "running"}
    runtime._cleanup_retained_scratches(PROJECT)
    rows.clear()
    assert path.exists() and _journal(storage, run_id).exists()
    with monkeypatch.context() as m:
        m.setattr(runtime.storage_paths, "get_storage_root",
                  lambda project_id=None, **_k: project_storage / "changed" if project_id else storage)
        runtime._cleanup_retained_scratches(PROJECT)
    assert path.exists() and _journal(storage, run_id).exists()
    with monkeypatch.context() as m:
        real_lstat = storage_fs.os.lstat
        def unreadable(candidate, *args, **kwargs):
            if os.path.normcase(os.path.abspath(candidate)) == os.path.normcase(str(path)):
                raise PermissionError(13, "injected")
            return real_lstat(candidate, *args, **kwargs)
        m.setattr(storage_fs.os, "lstat", unreadable)
        runtime._cleanup_retained_scratches(PROJECT)
    assert path.exists() and _journal(storage, run_id).exists()
    # Startup reaches exactly the same pending-first adapter.
    assert runtime.startup_sweep_run_scratches() == 1
    assert not path.exists() and not _journal(storage, run_id).exists()


def test_legacy_resume_keeps_predelete_eligible_time(ai_owned, monkeypatch):
    storage, _, rows, _, _ = ai_owned
    run_id = "aiv_20260827_000003"
    path = runtime._project_scratch_root(PROJECT) / run_id
    path.mkdir(parents=True)
    (path / "payload").write_text("delete", encoding="utf-8")
    old = datetime(2026, 8, 27, 2, tzinfo=timezone.utc)
    os.utime(path, (old.timestamp(), old.timestamp()))
    before = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    rows[run_id] = {"project_id": PROJECT, "status": "finished",
                    "started_at": "2026-08-27T01:00:00+00:00", "finished_at": old.isoformat()}
    real_remove = storage_fs.remove_managed_tree

    def partial(target, _root):
        (Path(target) / "payload").unlink()
        os.utime(target, None)
        raise PermissionError(13, "injected")

    monkeypatch.setattr(storage_fs, "remove_managed_tree", partial)
    assert runtime._delete_legacy_scratch(PROJECT, run_id, path, datetime.now(timezone.utc)) == (False, "delete_failed")
    record = json.loads(_journal(storage, run_id).read_text(encoding="utf-8"))
    assert record["eligibility"]["at"] == before
    assert not runtime._legacy_candidate(PROJECT, run_id, path, datetime.now(timezone.utc))[0]
    monkeypatch.setattr(storage_fs, "remove_managed_tree", real_remove)
    runtime._cleanup_retained_scratches(PROJECT)
    assert not path.exists() and not _journal(storage, run_id).exists()


def test_create_and_complete_when_global_journal_is_unavailable(ai_owned, monkeypatch, caplog):
    storage, _, _, _, _ = ai_owned
    # A repository marker makes the real common journal guard reject G.
    (storage / ".git").mkdir()
    run_id = "aiv_20261023_000005"
    with pytest.raises(storage_fs.DeleteJournalError) as unavailable:
        with storage_fs.journal_file_lock("ai-run", PROJECT, run_id):
            pytest.fail("G must be unavailable")
    assert unavailable.value.code == "journal_in_repository"
    path = runtime._create_scratch(PROJECT, run_id)
    assert path.is_dir() and not (storage / ".flowgate-gc").exists()
    with pytest.raises(FileExistsError):
        runtime._create_scratch(PROJECT, run_id)
    assert runtime._mark_scratch_completed(PROJECT, run_id, path, COMPLETED)
    assert json.loads((path / runtime.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))["completed_at"] == COMPLETED
    assert not (storage / ".flowgate-gc").exists()
    # An unavailable rollback journal must preserve the original creation error.
    original = OSError("original creation error")
    monkeypatch.setattr(runtime, "_atomic_write_manifest", lambda *_a: (_ for _ in ()).throw(original))
    monkeypatch.setattr(storage_fs, "remove_managed_tree", lambda *_a: pytest.fail("delete before journal"))
    with pytest.raises(OSError) as caught:
        runtime._create_scratch(PROJECT, "aiv_20261023_000006")
    assert caught.value is original
    # Completion failures use a distinct, safe event even when G is unavailable.
    caplog.clear()
    real_replace = Path.replace
    def fail_update(candidate, target):
        if candidate.name.endswith(".update"):
            raise PermissionError(13, "SECRET_SENTINEL")
        return real_replace(candidate, target)
    monkeypatch.setattr(Path, "replace", fail_update)
    assert not runtime._mark_scratch_completed(PROJECT, run_id, path, CREATED)
    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "ai scratch completion update failed:" in message
    assert "ai scratch delete failed:" not in message and "SECRET_SENTINEL" not in message


def test_rollback_keeps_creation_exception_and_delays_resume(ai_owned, monkeypatch):
    storage, _, rows, leases, state = ai_owned
    run_id = "aiv_20261023_000004"
    original = OSError("original creation error")
    monkeypatch.setattr(runtime, "_atomic_write_manifest", lambda *_a: (_ for _ in ()).throw(original))
    real_remove = storage_fs.remove_managed_tree
    monkeypatch.setattr(storage_fs, "remove_managed_tree", lambda *_a: (_ for _ in ()).throw(PermissionError(13, "injected")))
    with pytest.raises(OSError) as caught:
        runtime._create_scratch(PROJECT, run_id)
    assert caught.value is original
    record = json.loads(_journal(storage, run_id).read_text(encoding="utf-8"))
    assert record["kind"] == "rollback" and record["planned"]["id"] == run_id
    assert record["planned"]["owner"] == "flowgate.ai-invoke"
    assert record["eligibility"]["at"] == record["planned"]["created_at"]
    path = Path(record["target"])
    monkeypatch.setattr(storage_fs, "remove_managed_tree", real_remove)
    runtime._cleanup_retained_scratches(PROJECT)
    assert path.exists() and _journal(storage, run_id).exists()
    with pytest.raises(ValueError, match="pending delete"):
        runtime._create_scratch(PROJECT, run_id)
    with monkeypatch.context() as m:
        m.setattr(scratch_retention, "effective_retention", lambda: timedelta(0))
        rows[run_id] = {"status": "running"}
        runtime._cleanup_retained_scratches(PROJECT)
        assert path.exists()
        rows.clear()
        runtime._cleanup_retained_scratches(PROJECT)
    assert not path.exists() and not _journal(storage, run_id).exists()
