import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import snapshot_materialization_service as materialize
from modules.flow_gate.services import snapshot_request_service
from modules.flow_gate.services import snapshot_access_service
from modules.flow_gate.services import api_server_tools
from modules.flow_gate.api.v1 import snapshot_routes


class _Txn:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Store:
    def transaction(self):
        return _Txn()


class _State:
    def __init__(self, row):
        self.row = row
        self.events = []

    def get(self, snapshot_id):
        return dict(self.row) if self.row["snapshot_id"] == snapshot_id else None

    def mark_created(self, snapshot_id, created_at, expires_at, revision, fingerprint,
                     copied_file_count, copied_byte_size):
        if self.row["status"] != "approved":
            return dict(self.row), False
        self.row.update(
            status="created", created_at=created_at, expires_at=expires_at,
            source_revision=revision, source_fingerprint=fingerprint,
            copied_file_count=copied_file_count, copied_byte_size=copied_byte_size,
            stale=False, cleanup_failed=False, cleanup_attempts=0,
        )
        return dict(self.row), True

    def mark_failed(self, snapshot_id, code, reason):
        changed = self.row["status"] == "approved"
        if changed:
            self.row.update(status="failed", failure_code=code, failure_reason=reason)
        return dict(self.row), changed

    def mark_stale(self, snapshot_id, detected_at):
        changed = self.row["status"] == "created" and not self.row.get("stale")
        if changed:
            self.row.update(stale=True, stale_detected_at=detected_at)
        return dict(self.row), changed

    def mark_cleanup_failed(self, snapshot_id, reason, next_at):
        self.row.update(
            cleanup_failed=True,
            cleanup_attempts=int(self.row.get("cleanup_attempts") or 0) + 1,
            cleanup_last_error=reason,
            cleanup_next_at=next_at,
        )
        return dict(self.row)

    def mark_deleted(self, snapshot_id, deleted_at):
        changed = self.row["status"] == "created"
        if changed:
            self.row.update(
                status="deleted", deleted_at=deleted_at, cleanup_failed=False,
                cleanup_last_error=None, cleanup_next_at=None,
            )
        return dict(self.row), changed

    def list_created(self, project_id=None, group_id=None, run_id=None):
        if self.row["status"] != "created":
            return []
        if project_id and self.row["project_id"] != project_id:
            return []
        if group_id and self.row["group_id"] != group_id:
            return []
        if run_id and self.row["run_id"] != run_id:
            return []
        return [dict(self.row)]

    def list_materialization_candidates(self):
        return [dict(self.row)] if self.row["status"] in {"approved", "created"} else []

    def list_unmaterialized(self, run_id=None, group_id=None):
        if self.row["status"] not in {"requested", "approved"}:
            return []
        if run_id and self.row["run_id"] != run_id:
            return []
        if group_id and self.row["group_id"] != group_id:
            return []
        return [dict(self.row)]


@pytest.fixture
def snapshot_env(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").mkdir()
    (worktree / "one.txt").write_text("one", encoding="utf-8")
    (worktree / "other.txt").write_text("other", encoding="utf-8")
    (worktree / "dir" / "sub").mkdir(parents=True)
    (worktree / "dir" / "a.txt").write_text("A", encoding="utf-8")
    (worktree / "dir" / "sub" / "b.txt").write_text("B", encoding="utf-8")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "package.js").write_text("excluded", encoding="utf-8")
    (worktree / ".github").mkdir()
    (worktree / ".github" / "workflow.yml").write_text("kept", encoding="utf-8")

    scratch = tmp_path / "token-scratch"
    scratch.mkdir()
    row = {
        "snapshot_id": "snap_test",
        "project_id": "project",
        "group_id": "project.default.0517",
        "run_id": "run",
        "token_id": "token",
        "provider_id": "provider",
        "reason": "a real file tree is required",
        "purpose": "run tests",
        "scope": "single_file",
        "requested_paths": ["one.txt"],
        "source_kind": "current_worktree",
        "status": "approved",
        "requested_at": "2026-09-20T00:00:00+00:00",
        "approved_at": "2026-09-20T00:01:00+00:00",
        "approved_by": "human",
        "stale": False,
        "cleanup_failed": False,
        "cleanup_attempts": 0,
    }
    state = _State(row)

    monkeypatch.setattr(materialize, "get_store", lambda: _Store())
    monkeypatch.setattr(materialize.db, "get", state.get)
    monkeypatch.setattr(materialize.db, "mark_created", state.mark_created)
    monkeypatch.setattr(materialize.db, "mark_failed", state.mark_failed)
    monkeypatch.setattr(materialize.db, "mark_stale", state.mark_stale)
    monkeypatch.setattr(materialize.db, "mark_cleanup_failed", state.mark_cleanup_failed)
    monkeypatch.setattr(materialize.db, "mark_deleted", state.mark_deleted)
    monkeypatch.setattr(materialize.db, "list_created", state.list_created)
    monkeypatch.setattr(
        materialize.db, "list_materialization_candidates", state.list_materialization_candidates
    )
    monkeypatch.setattr(materialize.db, "list_unmaterialized", state.list_unmaterialized)
    monkeypatch.setattr(materialize.workflow_events, "create", state.events.append)
    monkeypatch.setattr(
        materialize.db_groups, "get_by_id",
        lambda group_id: {"group_id": group_id, "project_id": "project"},
    )
    monkeypatch.setattr(
        materialize.git_service, "effective_src_root_ex",
        lambda project_id, group_id: (worktree, "registered"),
    )
    monkeypatch.setattr(materialize.git_service, "_worktree_link_ok", lambda root: True)
    monkeypatch.setattr(
        materialize.token_service, "scratch_dir_path",
        lambda project_id, token_id: str(scratch),
    )

    def fake_git(args, cwd):
        if args[:2] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(materialize.git_service, "_run_git", fake_git)
    return SimpleNamespace(
        worktree=worktree, scratch=scratch, row=row, state=state,
        final=lambda: scratch / materialize.SNAPSHOT_NAMESPACE / row["snapshot_id"],
    )


@pytest.mark.parametrize(
    ("scope", "paths", "expected"),
    [
        ("single_file", ["one.txt"], {"one.txt"}),
        ("selected_files", ["one.txt", "dir/sub/b.txt"], {"one.txt", "dir/sub/b.txt"}),
        ("directory", ["dir"], {"dir/a.txt", "dir/sub/b.txt"}),
        (
            "whole_source", [],
            {"one.txt", "other.txt", "dir/a.txt", "dir/sub/b.txt", ".github/workflow.yml"},
        ),
    ],
)
def test_scope_materialization_preserves_relative_paths(snapshot_env, scope, paths, expected):
    snapshot_env.row.update(scope=scope, requested_paths=paths)
    result = materialize.materialize("snap_test", "human")
    source = snapshot_env.final() / "source"
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert actual == expected
    assert result["status"] == "created"
    assert snapshot_env.final().is_relative_to(snapshot_env.scratch)
    assert not snapshot_env.final().is_relative_to(snapshot_env.worktree)


def test_selected_file_failure_is_terminal_and_never_publishes_partial(snapshot_env):
    snapshot_env.row.update(
        scope="selected_files", requested_paths=["one.txt", "missing.txt"]
    )
    with pytest.raises(snapshot_request_service.SnapshotRequestError):
        materialize.materialize("snap_test", "human")
    assert snapshot_env.row["status"] == "failed"
    assert not snapshot_env.final().exists()
    namespace = snapshot_env.scratch / materialize.SNAPSHOT_NAMESPACE
    assert not list(namespace.glob("*.tmp"))
    failure = snapshot_env.state.events[-1]
    assert failure["event_type"] == "state_changed"
    assert json.loads(failure["metadata"])["error_code"] == "snapshot_create_failed"


def test_exact_worktree_failure_never_falls_back(snapshot_env, monkeypatch):
    monkeypatch.setattr(
        materialize.git_service, "effective_src_root_ex",
        lambda project_id, group_id: (None, "unregistered"),
    )
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as caught:
        materialize.materialize("snap_test", "human")
    assert caught.value.code == "group_worktree_unavailable"
    assert "base checkout was not used" in caught.value.message
    assert not snapshot_env.final().exists()


@pytest.mark.parametrize(
    "bad_path",
    ["../secret", "/absolute", "C:/windows", r"\\server\share\x", "file:stream", "a//b", "a/./b", "bad\x00name"],
)
def test_request_and_materializer_reject_abnormal_paths(bad_path):
    base = {
        "project_id": "p", "group_id": "g", "run_id": "r", "token_id": "t",
        "provider_id": "v", "reason": "tree", "purpose": "test",
        "scope": "single_file", "requested_paths": [bad_path],
        "source_kind": "current_worktree",
    }
    with pytest.raises(snapshot_request_service.SnapshotRequestError):
        snapshot_request_service.validate_request(base)


def test_links_are_skipped_recursively_and_rejected_when_explicit(snapshot_env, monkeypatch):
    linked = snapshot_env.worktree / "dir" / "outside_link"
    linked.write_text("pretend-link", encoding="utf-8")
    original = materialize._is_reparse_or_symlink
    monkeypatch.setattr(
        materialize, "_is_reparse_or_symlink",
        lambda path, st=None: Path(path).name == "outside_link" or original(path, st),
    )
    snapshot_env.row.update(scope="directory", requested_paths=["dir"])
    materialize.materialize("snap_test", "human")
    assert not (snapshot_env.final() / "source" / "dir" / "outside_link").exists()
    manifest = json.loads((snapshot_env.final() / "snapshot.json").read_text(encoding="utf-8"))
    assert {"path": "dir/outside_link", "reason": "symlink_or_reparse"} in manifest["excluded"]["paths"]
    explicit = dict(snapshot_env.row)
    explicit.update(scope="single_file", requested_paths=["dir/outside_link"])
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as caught:
        materialize._collect_scope(explicit, snapshot_env.worktree)
    assert caught.value.code == "snapshot_link_blocked"


def test_manifest_readme_exclusions_and_provenance(snapshot_env):
    snapshot_env.row.update(scope="whole_source", requested_paths=[])
    materialize.materialize("snap_test", "human")
    manifest = json.loads((snapshot_env.final() / "snapshot.json").read_text(encoding="utf-8"))
    readme = (snapshot_env.final() / "README.md").read_text(encoding="utf-8")
    for key in (
        "schema", "snapshot_id", "project_id", "group_id", "run_id", "token_id",
        "provider_id", "source_kind", "scope", "requested_paths", "reason", "purpose",
        "created_at", "source_revision", "source_fingerprint", "worktree_fingerprint",
        "copied_file_count", "copied_byte_size", "excluded",
    ):
        assert key in manifest
    assert manifest["source_kind"] == "current_worktree"
    assert "node_modules" in manifest["excluded"]["policy"]["directory_names"]
    assert any(item["path"] == "node_modules" for item in manifest["excluded"]["paths"])
    for sentence in (
        "disposable snapshot", "NOT the source of truth", "NOT persistent",
        "Never copy modified scratch files back", "FlowGate CRUD/tools",
        "only validate this snapshot", "may be stale", "automatically deleted",
        "final deliverables",
    ):
        assert sentence in readme


def test_size_limit_fails_closed_without_partial(snapshot_env, monkeypatch):
    monkeypatch.setattr(materialize, "SNAPSHOT_MAX_TOTAL_BYTES", 2)
    with pytest.raises(snapshot_request_service.SnapshotRequestError) as caught:
        materialize.materialize("snap_test", "human")
    assert caught.value.code == "snapshot_total_size_limit"
    assert snapshot_env.row["status"] == "failed"
    assert not snapshot_env.final().exists()


def test_stale_uses_scope_fingerprint_and_survives_restart(snapshot_env):
    materialize.materialize("snap_test", "human")
    assert materialize.refresh_stale("snap_test")["stale"] is False
    snapshot_env.worktree.joinpath("other.txt").write_text("outside changed", encoding="utf-8")
    assert materialize.refresh_stale("snap_test")["stale"] is False
    snapshot_env.worktree.joinpath("one.txt").write_text("included changed", encoding="utf-8")
    result = materialize.refresh_stale("snap_test")
    assert result["status"] == "created" and result["stale"] is True
    assert [event["event_type"] for event in snapshot_env.state.events].count("snapshot_stale") == 1
    assert materialize.refresh_stale("snap_test")["stale"] is True


def test_cleanup_deletes_snapshot_and_internal_garbage_with_audit(snapshot_env):
    materialize.materialize("snap_test", "human")
    garbage = snapshot_env.final() / "source" / "__pycache__"
    garbage.mkdir()
    (garbage / "x.pyc").write_bytes(b"x")
    result = materialize.cleanup("snap_test", "human")
    assert result["status"] == "deleted"
    assert not snapshot_env.final().exists()
    event = snapshot_env.state.events[-1]
    assert event["event_type"] == "snapshot_deleted"
    assert "__pycache__" in json.loads(event["metadata"])["garbage"]["categories"]
    assert materialize.cleanup("snap_test", "human")["status"] == "deleted"


def test_delete_failure_warns_audits_and_retry_succeeds(snapshot_env, monkeypatch, caplog):
    materialize.materialize("snap_test", "human")
    real_rmtree = materialize.shutil.rmtree
    calls = {"count": 0}

    def fail_once(path, *args, **kwargs):
        if Path(path) == snapshot_env.final() and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError("locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(materialize.shutil, "rmtree", fail_once)
    first = materialize.cleanup("snap_test", "human")
    assert first["status"] == "created" and first["cleanup_failed"] is True
    assert first["cleanup_attempts"] == 1
    event = snapshot_env.state.events[-1]
    assert event["event_type"] == "snapshot_delete_failed"
    assert json.loads(event["metadata"])["error_code"] == "snapshot_cleanup_failed"
    assert "snapshot cleanup failed" in caplog.text
    second = materialize.cleanup("snap_test", "human")
    assert second["status"] == "deleted"


def test_ttl_run_and_group_cleanup_entry_points(snapshot_env, monkeypatch):
    monkeypatch.setattr(materialize.db, "close_unmaterialized_for_run", lambda *args: [])
    monkeypatch.setattr(materialize.db, "close_unmaterialized_for_group", lambda *args: [])
    materialize.materialize("snap_test", "human")
    snapshot_env.row["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    summary = materialize.sweep_expired()
    assert summary["deleted"] == 1
    assert snapshot_env.row["status"] == "deleted"

    snapshot_env.row.update(status="created", deleted_at=None, cleanup_failed=False)
    snapshot_env.final().mkdir(parents=True)
    assert materialize.cleanup_for_run("run")["deleted"] == 1


def test_cleanup_cannot_touch_another_snapshot_or_worktree(snapshot_env):
    materialize.materialize("snap_test", "human")
    other = snapshot_env.scratch / materialize.SNAPSHOT_NAMESPACE / "snap_other"
    other.mkdir()
    (other / "sentinel").write_text("keep", encoding="utf-8")
    source_before = (snapshot_env.worktree / "one.txt").read_text(encoding="utf-8")
    materialize.cleanup("snap_test", "human")
    assert (other / "sentinel").read_text(encoding="utf-8") == "keep"
    assert (snapshot_env.worktree / "one.txt").read_text(encoding="utf-8") == source_before


def test_concurrent_materialize_publishes_once(snapshot_env):
    results = []
    errors = []

    def run():
        try:
            results.append(materialize.materialize("snap_test", "human"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(results) == 2
    assert snapshot_env.final().is_dir()
    assert [event["event_type"] for event in snapshot_env.state.events].count("snapshot_created") == 1


def test_publish_then_db_failure_recovers_without_second_tree(snapshot_env, monkeypatch):
    real_mark = snapshot_env.state.mark_created
    calls = {"count": 0}

    def fail_once(*args):
        if calls["count"] == 0:
            calls["count"] += 1
            raise RuntimeError("db down")
        return real_mark(*args)

    monkeypatch.setattr(materialize.db, "mark_created", fail_once)
    with pytest.raises(RuntimeError):
        materialize.materialize("snap_test", "human")
    assert snapshot_env.row["status"] == "approved"
    assert snapshot_env.final().is_dir()
    result = materialize.materialize("snap_test", "human")
    assert result["status"] == "created"
    finals = [path for path in snapshot_env.final().parent.iterdir() if path.name == "snap_test"]
    assert len(finals) == 1


def test_delete_then_db_failure_converges_on_retry(snapshot_env, monkeypatch):
    materialize.materialize("snap_test", "human")
    real_mark = snapshot_env.state.mark_deleted
    calls = {"count": 0}

    def fail_once(*args):
        if calls["count"] == 0:
            calls["count"] += 1
            raise RuntimeError("db down")
        return real_mark(*args)

    monkeypatch.setattr(materialize.db, "mark_deleted", fail_once)
    with pytest.raises(RuntimeError):
        materialize.cleanup("snap_test", "human")
    assert snapshot_env.row["status"] == "created"
    assert not snapshot_env.final().exists()
    assert materialize.cleanup("snap_test", "human")["status"] == "deleted"


def test_startup_removes_orphan_stage_and_marks_creation_failed(snapshot_env):
    namespace = snapshot_env.scratch / materialize.SNAPSHOT_NAMESPACE
    namespace.mkdir()
    orphan = namespace / ".snap_test.crash.tmp"
    orphan.mkdir()
    assert materialize.cleanup_orphans(startup=True) == 1
    assert not orphan.exists()
    assert snapshot_env.row["status"] == "failed"


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_lifecycle_migrations_include_cleanup_and_provenance_columns(dialect):
    migration = (
        Path(__file__).parents[1] / "sql" / "migrations" / dialect
        / "116_snapshot_materialization.sql"
    ).read_text(encoding="utf-8")
    for column in (
        "created_at", "expires_at", "deleted_at", "stale", "stale_detected_at",
        "cleanup_failed", "cleanup_attempts", "cleanup_last_error", "cleanup_next_at",
        "failure_code", "failure_reason", "copied_file_count", "copied_byte_size",
    ):
        assert column in migration


def test_copy_failure_removes_staging_and_never_publishes(snapshot_env, monkeypatch):
    snapshot_env.row.update(
        scope="selected_files", requested_paths=["one.txt", "other.txt"]
    )
    real_copy = materialize._copy_and_hash
    calls = {"count": 0}

    def fail_second(*args):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("copy interrupted")
        return real_copy(*args)

    monkeypatch.setattr(materialize, "_copy_and_hash", fail_second)
    with pytest.raises(OSError):
        materialize.materialize("snap_test", "human")
    namespace = snapshot_env.scratch / materialize.SNAPSHOT_NAMESPACE
    assert snapshot_env.row["status"] == "failed"
    assert not snapshot_env.final().exists()
    assert not list(namespace.glob(".*.tmp"))


def test_sqlite_lifecycle_migration_executes_after_request_schema(tmp_path):
    request_sql = (
        Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"
        / "115_snapshot_requests.sql"
    ).read_text(encoding="utf-8")
    lifecycle_sql = (
        Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"
        / "116_snapshot_materialization.sql"
    ).read_text(encoding="utf-8")
    database = tmp_path / "snapshot.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(request_sql)
        connection.executescript(lifecycle_sql)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(snapshot_requests)")
        }
    assert {
        "created_at", "expires_at", "deleted_at", "stale", "cleanup_failed",
        "cleanup_attempts", "failure_code", "copied_file_count", "copied_byte_size",
    } <= columns


def test_cleanup_missing_namespace_stays_created_and_audits_warning(snapshot_env):
    materialize.materialize("snap_test", "human")
    materialize.shutil.rmtree(snapshot_env.scratch)
    result = materialize.cleanup("snap_test", "human")
    assert result["status"] == "created"
    assert result["cleanup_failed"] is True
    event = snapshot_env.state.events[-1]
    metadata = json.loads(event["metadata"])
    assert event["event_type"] == "snapshot_delete_failed"
    assert metadata["remaining_path_count"] is None


def test_sqlite_lifecycle_state_machine_queries(tmp_path, monkeypatch):
    request_sql = (
        Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"
        / "115_snapshot_requests.sql"
    ).read_text(encoding="utf-8")
    lifecycle_sql = (
        Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"
        / "116_snapshot_materialization.sql"
    ).read_text(encoding="utf-8")
    lineage_sql = (
        Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"
        / "118_snapshot_lineage.sql"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(tmp_path / "lifecycle.sqlite")
    connection.row_factory = sqlite3.Row
    connection.executescript(request_sql)
    connection.executescript(lifecycle_sql)
    connection.executescript(lineage_sql)

    class Store:
        def _execute(self, sql, params):
            connection.execute(sql, params)
            connection.commit()

        def _execute_affected(self, sql, params):
            cursor = connection.execute(sql, params)
            connection.commit()
            return cursor.rowcount

        def _fetch_one(self, sql, params):
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params):
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    store = Store()
    monkeypatch.setattr(materialize.db, "get_store", lambda: store)
    materialize.db.create({
        "snapshot_id": "snap_database", "project_id": "p", "group_id": "g",
        "run_id": "r", "token_id": "t", "provider_id": "v", "reason": "reason",
        "scope": "single_file", "requested_paths": ["a.py"], "purpose": "test",
        "source_kind": "current_worktree",
    })
    materialize.db.transition("snap_database", "approved", "human")
    row, changed = materialize.db.mark_created(
        "snap_database", "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00", "a" * 40, "b" * 64, 1, 3,
    )
    assert changed and row["status"] == "created" and row["stale"] is False
    row, changed = materialize.db.mark_stale(
        "snap_database", "2026-01-01T00:01:00+00:00"
    )
    assert changed and row["stale"] is True
    row = materialize.db.mark_cleanup_failed(
        "snap_database", "locked", "2026-01-01T00:02:00+00:00"
    )
    assert row["status"] == "created" and row["cleanup_attempts"] == 1
    row, changed = materialize.db.mark_deleted(
        "snap_database", "2026-01-01T00:03:00+00:00"
    )
    assert changed and row["status"] == "deleted" and row["cleanup_failed"] is False
    connection.close()


@pytest.mark.parametrize(
    ("cleanup_scope", "close_name"),
    [
        ("run", "close_unmaterialized_for_run"),
        ("group", "close_unmaterialized_for_group"),
    ],
)
def test_lifecycle_close_waits_for_inflight_publish_and_deletes(
    snapshot_env, monkeypatch, cleanup_scope, close_name,
):
    published = threading.Event()
    release_commit = threading.Event()
    cleanup_done = threading.Event()
    errors = []
    real_mark_created = snapshot_env.state.mark_created

    def blocked_mark_created(*args):
        assert snapshot_env.final().is_dir()
        published.set()
        assert release_commit.wait(5)
        return real_mark_created(*args)

    def close_unmaterialized(owner_id, actor):
        expected = "run" if cleanup_scope == "run" else "project.default.0517"
        assert owner_id == expected
        if snapshot_env.row["status"] not in {"requested", "approved"}:
            return []
        snapshot_env.row.update(status="rejected", rejected_at="now", rejected_by=actor)
        return [dict(snapshot_env.row)]

    monkeypatch.setattr(materialize.db, "mark_created", blocked_mark_created)
    monkeypatch.setattr(materialize.db, close_name, close_unmaterialized)

    def create():
        try:
            materialize.materialize("snap_test", "human")
        except Exception as exc:
            errors.append(exc)

    def close():
        try:
            if cleanup_scope == "run":
                materialize.cleanup_for_run("run")
            else:
                materialize.cleanup_for_group("project.default.0517")
        except Exception as exc:
            errors.append(exc)
        finally:
            cleanup_done.set()

    creator = threading.Thread(target=create)
    cleaner = threading.Thread(target=close)
    creator.start()
    assert published.wait(5)
    cleaner.start()
    assert not cleanup_done.wait(0.1)
    release_commit.set()
    creator.join()
    cleaner.join()

    assert not errors
    assert snapshot_env.row["status"] == "deleted"
    assert not snapshot_env.final().exists()


def test_materialize_cleanup_race_converges_to_deleted(snapshot_env, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    real_copy = materialize._copy_and_hash
    results = []

    def blocked_copy(*args):
        entered.set()
        assert release.wait(5)
        return real_copy(*args)

    monkeypatch.setattr(materialize, "_copy_and_hash", blocked_copy)
    creator = threading.Thread(
        target=lambda: results.append(materialize.materialize("snap_test", "human"))
    )
    cleaner = threading.Thread(
        target=lambda: results.append(materialize.cleanup("snap_test", "human"))
    )
    creator.start()
    assert entered.wait(5)
    cleaner.start()
    release.set()
    creator.join()
    cleaner.join()
    assert [row["status"] for row in results] == ["created", "deleted"]
    assert snapshot_env.row["status"] == "deleted"
    assert not snapshot_env.final().exists()


def test_real_symlink_escape_is_not_followed(snapshot_env):
    outside = snapshot_env.scratch.parent / "outside-secret"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = snapshot_env.worktree / "dir" / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    snapshot_env.row.update(scope="directory", requested_paths=["dir"])
    materialize.materialize("snap_test", "human")
    assert not (snapshot_env.final() / "source" / "dir" / "external").exists()
    manifest = json.loads((snapshot_env.final() / "snapshot.json").read_text(encoding="utf-8"))
    assert {"path": "dir/external", "reason": "symlink_or_reparse"} in manifest["excluded"]["paths"]


def test_c1_to_c13_connected_request_pending_approve_read_stale_cleanup(
    snapshot_env, monkeypatch,
):
    snapshot_env.row.update(
        status="requested", stale=False, stale_detected_at=None,
        created_at=None, source_revision=None, source_fingerprint=None,
    )
    request_events=[]
    monkeypatch.setattr(snapshot_request_service, "get_store", lambda: _Store())
    monkeypatch.setattr(
        snapshot_request_service.workflow_events, "create",
        lambda event: request_events.append(event) or event,
    )
    monkeypatch.setattr(snapshot_request_service, "_notify", lambda *args: None)

    def create(data):
        snapshot_env.row.update(data)
        snapshot_env.row.update(
            snapshot_id="snap_test", status="requested",
            requested_at="2026-09-23T00:00:00+00:00",
        )
        return dict(snapshot_env.row)

    def transition(snapshot_id, decision, actor):
        if snapshot_env.row["status"] != "requested":
            return dict(snapshot_env.row), False
        snapshot_env.row["status"] = decision
        snapshot_env.row["approved_at" if decision == "approved" else "rejected_at"] = "now"
        snapshot_env.row["approved_by" if decision == "approved" else "rejected_by"] = actor
        return dict(snapshot_env.row), True

    monkeypatch.setattr(snapshot_request_service.db, "create", create)
    monkeypatch.setattr(snapshot_request_service.db, "transition", transition)
    monkeypatch.setattr(
        snapshot_routes.db, "list_pending",
        lambda project_id=None, group_id=None: (
            [dict(snapshot_env.row)] if snapshot_env.row["status"] == "requested" else []
        ),
    )
    token = {
        "token_id": "token", "ai_run_id": "run", "project": "project",
        "group_id": "project.default.0517", "issued_to": "worker",
    }
    run = {
        "token_id": "token", "current_token_id": "token", "run_id": "run",
        "chain_id": "chain-0517", "project_id": "project",
        "group_id": "project.default.0517", "provider_id": "provider",
    }
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: token)
    monkeypatch.setattr(
        snapshot_request_service, "validate_request_authority",
        lambda candidate, active: candidate,
    )
    status, requested = api_server_tools.request_source_snapshot(
        run, "raw-token",
        {
            "reason": "test runner requires a directory",
            "scope": "single_file",
            "requested_paths": ["one.txt"],
            "purpose": "run connected verification",
        },
    )
    assert status == 201
    assert requested["status"] == "requested"
    assert snapshot_env.row["chain_id"] == "chain-0517"
    assert not snapshot_env.final().exists()
    # run A exits before approval. The chain capability remains pending for a verified successor.
    closed = materialize.cleanup_for_run("run")
    assert closed == {"matched": 0, "closed": 0, "deleted": 0, "cleanup_failed": 0}
    assert snapshot_env.row["status"] == "requested"
    assert snapshot_routes.pending(
        project_id="project", group_id="project.default.0517", user={"user_id": "human"}
    )["requests"][0]["snapshot_id"] == "snap_test"
    detail = snapshot_routes.detail("snap_test", user={"user_id": "human"})
    assert detail["request"]["status"] == "requested"
    assert detail["request"]["available"] is False

    # Cross the real HTTP/UI-facing production boundary. One human [approve] request
    # must perform decide -> materialize and return the durable created row.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "human"}
    client = TestClient(app)
    approved = client.post("/api/v1/snapshots/snap_test/approve", json={})
    assert approved.status_code == 200
    created = approved.json()
    assert created["request"]["status"] == "created"
    assert created["request"]["snapshot_path"] == str(snapshot_env.final())

    usages=[]
    monkeypatch.setattr(
        snapshot_access_service.usage_db, "record",
        lambda data: usages.append(dict(data)) or dict(data),
    )
    # Server-admitted run B carries the same chain; it consumes after run A has ended.
    access_run = {
        "project_id": "project", "group_id": "project.default.0517",
        "run_id": "run-successor", "chain_id": "chain-0517",
        "token_id": "token-successor",
    }
    read_status, read_result = snapshot_access_service.access(
        access_run,
        {"snapshot_id": "snap_test", "operation": "read", "path": "one.txt"},
    )
    assert read_status == 200
    assert read_result["content"] == "one"
    assert read_result["snapshot"]["source_kind"] == "current_worktree"

    # The same delayed-approval successor consumes through the external CLI HTTP boundary.
    successor_token = {
        **token, "token_id": "token-successor", "ai_run_id": "run-successor",
    }
    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: successor_token)
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: access_run)
    cli_read = client.post(
        "/api/v1/snapshots/cli/snap_test/access",
        headers={"Authorization": "Bearer successor-token"},
        json={"operation": "read", "path": "one.txt"},
    )
    assert cli_read.status_code == 200
    assert cli_read.json()["content"] == "one"
    monkeypatch.setattr(
        ai_invoke_service, "get_run_record",
        lambda run_id: {**access_run, "run_id": "unrelated", "chain_id": "other-chain"},
    )
    cli_denied = client.get(
        "/api/v1/snapshots/cli/snap_test/status",
        headers={"Authorization": "Bearer successor-token"},
    )
    assert cli_denied.status_code == 403

    (snapshot_env.worktree / "one.txt").write_text("changed", encoding="utf-8")
    stale_status, stale_result = snapshot_access_service.access(
        access_run,
        {"snapshot_id": "snap_test", "operation": "read", "path": "one.txt"},
    )
    assert stale_status == 200
    assert stale_result["content"] == "one"
    assert stale_result["snapshot"]["status"] == "stale"
    assert stale_result["snapshot"]["warning"] == "ACTIVE SNAPSHOT IS STALE"

    deleted = snapshot_routes.cleanup_snapshot("snap_test", user={"user_id": "human"})
    assert deleted["request"]["status"] == "deleted"
    assert not snapshot_env.final().exists()
    with pytest.raises(snapshot_access_service.SnapshotAccessError) as caught:
        snapshot_access_service.access(
            access_run, {"snapshot_id": "snap_test", "operation": "status"}
        )
    assert caught.value.code == "snapshot_deleted"
    assert usages and usages[0]["snapshot_id"] == "snap_test"

@pytest.mark.parametrize("initial_status", ["requested", "approved"])
def test_run_finish_closes_unmaterialized_and_late_http_approval_cannot_create(
    snapshot_env, monkeypatch, initial_status,
):
    snapshot_env.row.update(status=initial_status, created_at=None)
    events = []

    def close_for_run(run_id, actor):
        assert run_id == "run"
        assert actor == "snapshot-run-cleanup"
        if snapshot_env.row["status"] not in {"requested", "approved"}:
            return []
        snapshot_env.row.update(
            status="rejected", rejected_at="now", rejected_by=actor,
        )
        return [dict(snapshot_env.row)]

    monkeypatch.setattr(materialize.db, "close_unmaterialized_for_run", close_for_run)
    monkeypatch.setattr(materialize, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(materialize.db, "list_created", lambda **kwargs: [])

    result = materialize.cleanup_for_run("run")
    assert result == {"matched": 1, "closed": 1, "deleted": 0, "cleanup_failed": 0}
    assert snapshot_env.row["status"] == "rejected"
    assert not snapshot_env.final().exists()
    assert events[0][1]["reason"] == "owner_lifecycle_finished"

    monkeypatch.setattr(snapshot_request_service, "get_store", lambda: _Store())
    monkeypatch.setattr(snapshot_request_service.db, "transition", lambda *args: (dict(snapshot_env.row), False))
    monkeypatch.setattr(snapshot_request_service, "_notify", lambda *args: None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "late-human"}
    response = TestClient(app).post("/api/v1/snapshots/snap_test/approve", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "snapshot_not_approved"
    assert not snapshot_env.final().exists()
