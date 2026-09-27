"""Focused regression coverage for the T#1 Source Bundle core."""
from __future__ import annotations

import json
import sqlite3
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import source_bundle_materializer as m


def _worktree(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr(m, "_identity", lambda _root, _deadline: ("a" * 40, True))
    return root


def test_same_file_state_handles_missing_windows_identity():
    def state(dev, ino, *, size=8, mtime=123):
        return SimpleNamespace(st_mode=stat.S_IFREG, st_size=size,
                               st_mtime_ns=mtime, st_dev=dev, st_ino=ino)

    scan = state(0, 0)
    disk = state(4, 99)
    assert m._same(scan, disk)
    assert m._same(disk, scan)
    assert not m._same(disk, state(4, 100))
    assert not m._same(scan, state(4, 99, size=9))
    assert not m._same(scan, state(4, 99, mtime=124))


def test_whole_source_exclusions_and_atomic_publish(tmp_path, monkeypatch):
    root = _worktree(tmp_path, monkeypatch)
    (root / "app.py").write_text("print('ok')", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    excluded_names = (
        "credentials-2024.json", "database-credentials.txt", "prod-secrets.yaml",
        "my-secret-key.txt", "private_key.txt", "aws_credentials.ini",
        "app.secret", "secrets.yml", "secret.txt",
    )
    for name in excluded_names:
        (root / name).write_text("credential", encoding="utf-8")
    (root / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (root / "client.crt").write_text("certificate", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "config.json").write_text("secret", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(m, "bundle_path", lambda _project, _bundle: tmp_path / "durable" / _bundle)
    deadline = time.monotonic() + 30
    baseline = m.inspect_source(root, deadline)
    result = m.materialize(root, "project", "sb_" + "a" * 32, baseline, deadline)
    final = tmp_path / "durable" / ("sb_" + "a" * 32)
    assert (final / "source" / "app.py").read_text() == "print('ok')"
    assert not (final / "source" / ".env").exists()
    assert all(not (final / "source" / name).exists() for name in excluded_names)
    assert not (final / "source" / ".git").exists()
    assert not (final / "source" / "client.crt").exists()
    assert not (final / "source" / "secrets").exists()
    assert not (final / "source" / "node_modules").exists()
    assert result["file_count"] == 1
    assert json.loads((final / "manifest.json").read_text())["policy"] == m.POLICY_VERSION
    assert not list(final.parent.glob("*.building"))
    (final / "source" / "app.py").chmod(stat.S_IWRITE | stat.S_IREAD)
    (final / "source").chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("value", ["../escape", "/absolute", "C:/drive", "//server/share", "a/./b", "a//b", "a\\b"])
def test_unsafe_relative_path(value):
    with pytest.raises(m.SourceBundleError) as error:
        m._safe_name(value)
    assert error.value.code == "unsafe_path"


def test_symlink_and_special_file_blocked(tmp_path, monkeypatch):
    root = _worktree(tmp_path, monkeypatch)
    (root / "valid.txt").write_text("ok")
    try:
        (root / "link").symlink_to(root / "valid.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(m.SourceBundleError, match="link"):
        m.inspect_source(root, time.monotonic() + 10)


def test_source_change_does_not_publish(tmp_path, monkeypatch):
    root = _worktree(tmp_path, monkeypatch)
    target = root / "app.py"
    target.write_text("original")
    monkeypatch.setattr(m, "bundle_path", lambda _project, _bundle: tmp_path / "durable" / _bundle)
    baseline = m.inspect_source(root, time.monotonic() + 10)
    target.write_text("changed")
    with pytest.raises(m.SourceBundleError):
        m.materialize(root, "project", "sb_" + "b" * 32, baseline, time.monotonic() + 10)
    assert not (tmp_path / "durable" / ("sb_" + "b" * 32)).exists()
    assert not (tmp_path / "durable" / ("." + "sb_" + "b" * 32 + ".building")).exists()


def test_hard_limits_and_ceiling(tmp_path, monkeypatch):
    root = _worktree(tmp_path, monkeypatch)
    (root / "one").write_text("1")
    (root / "two").write_text("2")
    monkeypatch.setattr(m, "MAX_FILES", 1)
    with pytest.raises(m.SourceBundleError) as error:
        m.inspect_source(root, time.monotonic() + 10)
    assert error.value.code == "resource_limit"
    with pytest.raises(m.SourceBundleError) as error:
        m.inspect_source(root, time.monotonic() - 1)
    assert error.value.code == "build_timeout"


def test_sqlite_migration_db_uniqueness():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite" /
           "122_source_bundles.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    extension = (Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite" /
                 "123_source_bundle_cleanup_metrics.sql").read_text(encoding="utf-8")
    conn.executescript(extension)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"source_bundles", "source_bundle_builds", "source_bundle_usages", "source_bundle_scratches"} <= tables
    conn.execute("INSERT INTO source_bundle_builds VALUES (?,?,?,?,?,?,?,?)",
                 ("p", "g", m.POLICY_VERSION, "owner1", "b1", "tomorrow", "building", None))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_bundle_builds VALUES (?,?,?,?,?,?,?,?)",
                     ("p", "g", m.POLICY_VERSION, "owner2", "b2", "tomorrow", "building", None))


def test_exact_worktree_missing_fails_closed(monkeypatch):
    from modules.flow_gate.services import git_service
    from modules.flow_gate.db import groups
    monkeypatch.setattr(groups, "get_by_id", lambda _group: {"project_id": "project"})
    monkeypatch.setattr(git_service, "effective_src_root_ex",
                        lambda _project, _group: (None, "worktree_unregistered"))
    with pytest.raises(m.SourceBundleError) as error:
        m.resolve_worktree("project", "group")
    assert error.value.code == "group_worktree_unavailable"


def test_reuse_skips_build_and_returns_no_host_path(monkeypatch, tmp_path):
    from modules.flow_gate.services import source_bundle_service as service
    baseline = {"source_revision": "a" * 40, "source_dirty": True,
                "content_fingerprint": "b" * 64}
    row = {"bundle_id": "sb_" + "a" * 32, "status": "created", "scope": "whole_source",
           "source_revision": baseline["source_revision"], "source_dirty": True,
           "content_fingerprint": baseline["content_fingerprint"],
           "bundle_sha256": "c" * 64, "exclusion_policy_version": m.POLICY_VERSION,
           "file_count": 1, "byte_size": 1, "created_at": "now", "expires_at": "later"}
    monkeypatch.setattr(m, "resolve_worktree", lambda _project, _group: tmp_path)
    monkeypatch.setattr(m, "inspect_source", lambda _root, _deadline: baseline)
    monkeypatch.setattr(service.db, "reusable", lambda *_args: [row])
    monkeypatch.setattr(service, "_integrity", lambda *_args: True)
    monkeypatch.setattr(service.db, "claim", lambda *_args: pytest.fail("reuse copied source"))
    result = service.ensure("project", "group")
    assert result["reused"] is True
    assert "path" not in result
    assert str(tmp_path) not in str(result)


def test_waiter_receives_builders_failure(monkeypatch, tmp_path):
    from modules.flow_gate.services import source_bundle_service as service
    monkeypatch.setattr(m, "resolve_worktree", lambda _project, _group: tmp_path)
    monkeypatch.setattr(m, "inspect_source", lambda _root, _deadline: {
        "source_revision": "a" * 40, "source_dirty": False, "content_fingerprint": "b" * 64
    })
    monkeypatch.setattr(service.db, "reusable", lambda *_args: [])
    monkeypatch.setattr(service.db, "claim", lambda *_args: (None, "sb_" + "a" * 32))
    monkeypatch.setattr(service.db, "get", lambda _bundle: {
        "status": "failed", "failure_code": "source_changed", "failure_reason": "source changed"
    })
    with pytest.raises(m.SourceBundleError) as error:
        service.ensure("project", "group")
    assert error.value.code == "source_changed"
