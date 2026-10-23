"""flowgate.v02.0090 T#1 — managed delete + delete journal for token scratch (NR0003 §6.2).

1. Representative success: a real Git ReadOnly loose object (the B0001 WinError 5)
   is removed by one retry; the journal is written before the delete and dropped
   after it; an external sentinel behind a junction and out-of-scope candidates stay.
2. Representative failure → resume: a partial delete that already lost the
   manifest fails on a held file; the external record survives, and after the
   in-memory state is reset the existing sweep finishes the target, the record
   and the row purge.  Short branches: record write failure starts no delete,
   a lookup failure that is not ENOENT keeps the record, an active reference
   or an identity mismatch refuses it, a vanished target only drops its record,
   and a snapshot record without a valid parent holds the row purge.
3. Journal location: a G inside a Git repository — G/.git, or S0 reached
   through a junction into a repository — is refused before any delete.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from modules.flow_gate.services import ai_invoke_service as ai
from modules.flow_gate.services import token_scratch
from modules.flow_gate.settings import scratch_retention
from modules.flow_gate.storage import filesystem as storage_fs

PROJECT = "project-0090"
OLD = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


@pytest.fixture
def owned(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(scratch_retention, "effective_retention", lambda: timedelta(days=7))
    monkeypatch.setattr(token_scratch.storage_paths, "get_storage_root", lambda *_a, **_k: storage)
    monkeypatch.setattr(token_scratch.db_projects, "get_by_id", lambda _: {"project_name": "Project"})
    monkeypatch.setattr(token_scratch.storage_paths, "resolve_storage_dir",
                        lambda value, _: Path(value).resolve(strict=False))
    rows, leases, purged = {}, {}, []
    monkeypatch.setattr(token_scratch.db_tokens, "get_by_id", lambda token_id: rows.get(token_id))
    monkeypatch.setattr(token_scratch.db_leases, "get_by_token_id", lambda token_id: leases.get(token_id))
    monkeypatch.setattr(token_scratch.db_tokens, "list_expired_for_purge", lambda: list(rows.values()))
    monkeypatch.setattr(token_scratch.db_tokens, "delete_expired_one",
                        lambda token_id: purged.append(token_id) or rows.pop(token_id))
    with ai._runs_lock:
        old_runs = dict(ai._runs)
        ai._runs.clear()
    yield storage, rows, purged
    with ai._runs_lock:
        ai._runs.clear()
        ai._runs.update(old_runs)


def _scratch(storage, number, *, old=True):
    token_id = f"tok_20261023_{number:06d}"
    path = storage / "work" / "Project" / token_id
    token_scratch.create(PROJECT, token_id, path)
    if old:
        manifest_path = path / token_scratch.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = OLD
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return token_id, path


def _row(token_id, path, *, consumed_at=OLD, expires_at=OLD):
    return {"token_id": token_id, "project": PROJECT, "scratch_dir": str(path),
            "consumed_at": consumed_at, "revoked_at": None, "expires_at": expires_at}


def _record_path(storage, token_id):
    return (storage / ".flowgate-gc" / "token" / storage_fs.project_key(PROJECT)
            / f"{token_id}.json")


def _readonly_objects(repo):
    found = []
    for directory, _, files in os.walk(repo / ".git" / "objects"):
        for name in files:
            path = Path(directory) / name
            if getattr(os.lstat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
                found.append(path)
    return found


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True, timeout=60)


def _junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return True
    except (ImportError, AttributeError, OSError):
        return False


@pytest.mark.skipif(os.name != "nt", reason="Windows ReadOnly loose objects")
@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_readonly_git_objects_are_deleted_once_and_journaled_first(owned, monkeypatch, tmp_path):
    storage, rows, _ = owned
    token_id, path = _scratch(storage, 1)
    repo = path / "probe"
    repo.mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "probe")
    readonly = _readonly_objects(repo)
    assert readonly, "git did not leave a ReadOnly loose object"
    # The B0001 condition: the plain unlink that rmtree issues is refused (WinError 5).
    with pytest.raises(PermissionError) as refused:
        os.unlink(readonly[0])
    assert getattr(refused.value, "winerror", None) == 5 and readonly[0].exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep", encoding="utf-8")
    has_junction = _junction(path / "to_outside", outside)
    active_id, active = _scratch(storage, 2)
    rows[active_id] = _row(active_id, active, consumed_at=None, expires_at=FUTURE)
    unmanaged = storage / "work" / "Project" / "tok_20261023_000003"
    unmanaged.mkdir()

    calls = []
    real_remove = storage_fs.remove_managed_tree

    def journaled_first(target, root, **kwargs):
        record = json.loads(_record_path(storage, token_id).read_text(encoding="utf-8"))
        calls.append(record)
        return real_remove(target, root, **kwargs)

    monkeypatch.setattr(storage_fs, "remove_managed_tree", journaled_first)
    assert token_scratch.sweep(PROJECT) == 1

    assert not path.exists()
    assert not _record_path(storage, token_id).exists()
    assert len(calls) == 1
    record = calls[0]
    assert (record["owner"], record["kind"], record["record_id"]) == ("token", "delete", token_id)
    assert record["manifest"]["token_id"] == token_id and record["manifest"]["created_at"] == OLD
    assert record["eligibility"] == {"basis": "orphan_manifest_created_at", "at": OLD}
    assert record["target_identity"]["ino"] and record["root_identity"]["ino"]
    assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    if has_junction:
        assert outside.is_dir()
    assert active.is_dir() and unmanaged.is_dir()


def _dir_link(link, target):
    if _junction(link, target):
        return True
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_failed_partial_delete_resumes_from_external_record(owned, monkeypatch, caplog):
    storage, rows, purged = owned
    token_id, path = _scratch(storage, 10)
    rows[token_id] = _row(token_id, path)
    (path / "sub").mkdir()
    (path / "sub" / "locked.txt").write_text("busy", encoding="utf-8")
    (path / "top.txt").write_text("top", encoding="utf-8")
    manifest = path / token_scratch.MANIFEST_NAME
    record = _record_path(storage, token_id)

    # (a) The record cannot be persisted: nothing is deleted and no record is left.
    def no_fsync(_fd):
        raise OSError("simulated fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage_fs.os, "fsync", no_fsync)
        assert token_scratch.sweep(PROJECT) == 0
    assert manifest.is_file() and (path / "top.txt").is_file() and (path / "sub" / "locked.txt").is_file()
    leftovers = list(record.parent.glob("*")) if record.parent.exists() else []
    assert leftovers == []  # no record, .tmp or .lock left behind

    # Partial delete that already took the manifest, then a held file stops it.
    real_rmtree = shutil.rmtree
    handle = open(path / "sub" / "locked.txt", "rb")

    def partial_then_held(target, *args, **kwargs):
        if Path(target).resolve() == path.resolve():
            manifest.unlink()
            (path / "top.txt").unlink()
            if os.name != "nt":
                raise PermissionError(13, "simulated sharing violation")
        return real_rmtree(target, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(storage_fs.shutil, "rmtree", partial_then_held)
            assert token_scratch.sweep(PROJECT) == 0
        assert path.is_dir() and not manifest.exists()
        assert (path / "sub" / "locked.txt").is_file()
        assert record.is_file()  # the evidence survived outside the deleted tree
        assert token_scratch.purge_expired_rows() == 0 and token_id in rows
    finally:
        handle.close()

    # Fresh process state: the next existing sweep must rely on the record alone.
    monkeypatch.setattr(token_scratch, "_last_sweep", {})
    monkeypatch.setattr(token_scratch, "_sweeping", set())
    monkeypatch.setattr(storage_fs, "_held", set())

    # (d) The target cannot be looked up (access denied is not ENOENT): the
    # record, the target and the row stay, and the shared delete never reports "gone".
    real_lstat = os.lstat
    denied_paths = {os.path.normcase(os.path.abspath(path)), os.path.normcase(str(path.resolve()))}

    def denied(target, *args, **kwargs):
        if os.path.normcase(os.path.abspath(os.fspath(target))) in denied_paths:
            raise PermissionError(13, "simulated access denied", os.fspath(target))
        return real_lstat(target, *args, **kwargs)

    with monkeypatch.context() as patch, caplog.at_level(logging.WARNING, logger=token_scratch.__name__):
        patch.setattr(os, "lstat", denied)
        assert token_scratch.sweep(PROJECT) == 0
        assert token_scratch.purge_expired_rows() == 0
        with pytest.raises(storage_fs.PresenceUnknown):
            storage_fs.remove_managed_tree(path, path.parent, missing_ok=True)
    assert path.is_dir() and record.is_file() and token_id in rows
    assert "code=presence_unknown" in caplog.text

    # (b1) An active AI reference still refuses the resumed delete.
    with ai._runs_lock:
        ai._runs["aiv_20261023_000001"] = {"status": "running", "project_id": PROJECT,
                                             "token_scratch_dir": str(path)}
    assert token_scratch.sweep(PROJECT) == 0
    assert path.is_dir() and record.is_file()
    with ai._runs_lock:
        ai._runs.clear()

    assert token_scratch.sweep(PROJECT) == 1
    assert not path.exists() and not record.exists()
    assert token_scratch.purge_expired_rows() == 1 and purged == [token_id]

    # (b2) A different directory under the same id never inherits the record.
    swap_id, swap = _scratch(storage, 11)
    rows[swap_id] = _row(swap_id, swap)
    with monkeypatch.context() as patch:
        patch.setattr(storage_fs.shutil, "rmtree",
                      lambda *_a, **_k: (_ for _ in ()).throw(PermissionError(13, "held")))
        assert token_scratch.sweep(PROJECT) == 0
    swap_record = _record_path(storage, swap_id)
    assert swap_record.is_file()
    saved = (swap / token_scratch.MANIFEST_NAME).read_bytes()
    swap.rename(swap.with_name("moved_away"))
    swap.mkdir()
    (swap / token_scratch.MANIFEST_NAME).write_bytes(saved)
    assert token_scratch.sweep(PROJECT) == 0
    assert swap.is_dir() and swap_record.is_file()
    assert token_scratch.purge_expired_rows() == 0 and swap_id in rows

    # (c) The target vanished: only the record is dropped, then the row may go.
    real_rmtree(swap)
    assert token_scratch.sweep(PROJECT) == 0
    assert not swap_record.exists()

    # (e) A snapshot record whose parent is missing or incomplete cannot name
    # its token: write_journal refuses it, and left on disk it is a damaged
    # record that holds the project's token row purge.
    moved = swap.with_name("moved_away")
    broken = storage_fs.new_journal_record(
        "snapshot", "delete", PROJECT, "snap_0001", managed_root=moved.parent, target=moved,
        reason="test", eligibility={"basis": "test", "at": OLD}, parent={"owner": "token"})
    with pytest.raises(storage_fs.DeleteJournalError, match="journal_parent_invalid"):
        storage_fs.write_journal(broken)
    snapshot_dir = storage / ".flowgate-gc" / "snapshot" / storage_fs.project_key(PROJECT)
    snapshot_dir.mkdir(parents=True)
    for parent in ({"owner": "token"}, None):
        broken["parent"] = parent
        (snapshot_dir / "snap_0001.json").write_text(json.dumps(broken), encoding="utf-8")
        assert [entry.error for entry in storage_fs.list_journals("snapshot", PROJECT)] == [
            "journal_parent_invalid"]
        assert storage_fs.pending_journals_for_parent("snapshot", PROJECT, "token", swap_id)
        assert token_scratch.purge_expired_rows() == 0 and swap_id in rows
    (snapshot_dir / "snap_0001.json").unlink()

    assert token_scratch.purge_expired_rows() == 1 and swap_id not in rows
    assert moved.is_dir()  # never part of any record


def test_create_refuses_an_id_with_a_pending_record(owned, monkeypatch):
    storage, rows, _ = owned
    token_id, path = _scratch(storage, 20)
    rows[token_id] = _row(token_id, path)
    with monkeypatch.context() as patch:
        patch.setattr(storage_fs.shutil, "rmtree",
                      lambda *_a, **_k: (_ for _ in ()).throw(PermissionError(13, "held")))
        assert token_scratch.sweep(PROJECT) == 0
    real = path.with_name("kept")
    path.rename(real)
    with pytest.raises(ValueError, match="pending delete record"):
        token_scratch.create(PROJECT, token_id, path)
    assert not path.exists()


def test_journal_inside_a_repository_is_refused_before_any_delete(owned, monkeypatch, tmp_path):
    storage, _, _ = owned
    gc_root = storage / ".flowgate-gc"

    # (1) G itself holds .git: no record directory, no delete, no resume.
    token_id, path = _scratch(storage, 30)
    (gc_root / ".git").mkdir(parents=True)
    assert token_scratch.sweep(PROJECT) == 0
    assert (path / token_scratch.MANIFEST_NAME).is_file()
    assert [entry.name for entry in gc_root.iterdir()] == [".git"]
    with pytest.raises(storage_fs.DeleteJournalError, match="journal_in_repository"):
        storage_fs.list_journals("token", PROJECT)

    # (2) S0 = alias/storage where alias is a junction into repository/data.
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    (repository / "data").mkdir()
    if not _dir_link(tmp_path / "alias", repository / "data"):
        pytest.skip("directory junction/symlink unavailable")
    linked = tmp_path / "alias" / "storage"
    linked.mkdir()
    monkeypatch.setattr(token_scratch.storage_paths, "get_storage_root", lambda *_a, **_k: linked)
    root = linked / "work" / "Project"
    root.mkdir(parents=True)
    linked_id = "tok_20261023_000031"
    token_scratch.create(PROJECT, linked_id, (root / linked_id).resolve())
    manifest_path = root / linked_id / token_scratch.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = OLD
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert token_scratch.sweep(PROJECT) == 0
    assert manifest_path.is_file()
    assert not (repository / "data" / "storage" / ".flowgate-gc").exists()
    with pytest.raises(storage_fs.DeleteJournalError, match="journal_in_repository"):
        storage_fs.list_journals("token", PROJECT)
