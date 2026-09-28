"""flowgate.default.0641 T2#1 — durable TR2 ownership and mutation-guard foundation."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.documents import tr2_precheck
from modules.flow_gate.services import tr2_file_policy as policy
from modules.flow_gate.storage.safe_path import (
    MutationPathAliasError,
    resolve_mutation_target_no_alias,
)


def test_successful_by_group_uses_group_state_index_shape(monkeypatch):
    seen = {}

    class Store:
        def _fetch_all(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return [{"attempt_id": "a1"}]

    monkeypatch.setattr(db_attempts, "get_store", lambda: Store())
    assert db_attempts.successful_by_group("flowgate.default.0641") == [{"attempt_id": "a1"}]
    assert "group_id = ?" in seen["sql"]
    assert "state = 'succeeded'" in seen["sql"]
    assert seen["params"] == ["flowgate.default.0641"]


def test_managed_paths_uses_succeeded_attempt_commit_paths_without_ledger(monkeypatch):
    monkeypatch.setattr(
        policy.db_attempts,
        "successful_by_group",
        lambda _gid: [
            {"attempt_id": "a1", "ledger_row_id": None,
             "commit_json": json.dumps({"paths": ["server/./a.py", "client/b.ts"]})},
            {"attempt_id": "a2", "ledger_row_id": 999,
             "commit_json": {"paths": ["server/a.py", "server/c.py"]}},
        ],
    )
    assert policy.managed_paths("flowgate.default.0641") == {
        "server/a.py", "server/c.py", "client/b.ts",
    }


def test_managed_paths_fails_closed_on_malformed_succeeded_attempt(monkeypatch):
    monkeypatch.setattr(
        policy.db_attempts,
        "successful_by_group",
        lambda _gid: [{"attempt_id": "bad", "commit_json": "{}"}],
    )
    with pytest.raises(policy.Tr2OwnershipInvariantError):
        policy.managed_paths("flowgate.default.0641")


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def test_no_alias_resolver_rejects_internal_and_external_alias(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "inside.txt"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("y", encoding="utf-8")

    _symlink_or_skip(root / "internal.txt", inside)
    _symlink_or_skip(root / "external.txt", outside)

    with pytest.raises(MutationPathAliasError):
        resolve_mutation_target_no_alias(root, "internal.txt", allow_missing_leaf=False)
    with pytest.raises(MutationPathAliasError):
        resolve_mutation_target_no_alias(root, "external.txt", allow_missing_leaf=False)


def test_no_alias_resolver_rejects_missing_leaf_under_alias_parent(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    real_dir = root / "real"
    real_dir.mkdir()
    _symlink_or_skip(root / "alias", real_dir, target_is_directory=True)

    with pytest.raises(MutationPathAliasError):
        resolve_mutation_target_no_alias(root, "alias/new.txt", allow_missing_leaf=True)


def test_general_source_mutation_lock_resolve_authority_order(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    events = []

    monkeypatch.setattr(
        policy.git_service, "_acquire_lock",
        lambda project_id, holder: events.append("lock") or True,
    )
    monkeypatch.setattr(
        policy.db_git, "release_lock",
        lambda project_id, holder: events.append("unlock"),
    )
    monkeypatch.setattr(
        policy, "_group_root",
        lambda project_id, group_id: events.append("resolve") or root,
    )
    monkeypatch.setattr(
        policy, "managed_paths",
        lambda group_id: events.append("managed") or set(),
    )

    with policy.general_source_mutation(
        "flowgate", "flowgate.default.0641", exact_paths=["a.py"]
    ):
        events.append("act")

    assert events == ["lock", "resolve", "managed", "act", "unlock"]


def test_general_source_mutation_blocks_exact_and_recursive(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    (root / "server").mkdir(parents=True)
    (root / "server" / "a.py").write_text("x", encoding="utf-8")

    monkeypatch.setattr(policy.git_service, "_acquire_lock", lambda *_: True)
    monkeypatch.setattr(policy.db_git, "release_lock", lambda *_: None)
    monkeypatch.setattr(policy, "_group_root", lambda *_: root)
    monkeypatch.setattr(policy, "managed_paths", lambda _gid: {"server/a.py"})

    with pytest.raises(policy.Tr2FilePolicyError) as exact:
        with policy.general_source_mutation(
            "flowgate", "flowgate.default.0641", exact_paths=["server/a.py"]
        ):
            pass
    assert exact.value.code == policy.TR2_MANAGED_FILE

    with pytest.raises(policy.Tr2FilePolicyError) as recursive:
        with policy.general_source_mutation(
            "flowgate", "flowgate.default.0641", recursive_paths=["server"]
        ):
            pass
    assert recursive.value.code == policy.TR2_MANAGED_FILE


def _server_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_sqlite_migration_preserves_attempt_after_document_delete():
    root = _server_root()
    sqlite_dir = root / "sql" / "migrations" / "sqlite"
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE documents (doc_id TEXT PRIMARY KEY);
        CREATE TABLE tr_commit_ledger (
            id INTEGER PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE
        );
    """)
    con.executescript((sqlite_dir / "114_tr2_approval_attempts.sql").read_text(encoding="utf-8"))
    con.execute("INSERT INTO documents(doc_id) VALUES ('d')")
    con.execute("INSERT INTO tr_commit_ledger(id,doc_id) VALUES (1,'d')")
    con.execute(
        """INSERT INTO tr2_approval_attempts(
            attempt_id,tr2_doc_id,project_id,group_id,document_revision,approval_round,
            actor_user_id,spec_fingerprint,baseline_fingerprint,state,phase,commit_json,
            ledger_row_id,started_at,heartbeat_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "a1", "d", "flowgate", "flowgate.default.0641", 1, 1, "u",
            "s" * 64, "sha256:" + "b" * 64, "succeeded", "complete",
            json.dumps({"paths": ["server/a.py"]}), 1, "now", "now", "now",
        ),
    )
    con.commit()

    con.executescript(
        (sqlite_dir / "125_tr2_attempt_durable_ownership.sql").read_text(encoding="utf-8")
    )
    con.execute("DELETE FROM documents WHERE doc_id='d'")
    con.commit()

    row = con.execute(
        "SELECT tr2_doc_id,state,commit_json,ledger_row_id "
        "FROM tr2_approval_attempts WHERE attempt_id='a1'"
    ).fetchone()
    assert row is not None
    assert row[0] == "d"
    assert row[1] == "succeeded"
    assert json.loads(row[2])["paths"] == ["server/a.py"]
    assert row[3] is None


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_durable_ownership_migrations_drop_document_fk_and_set_null_ledger(dialect):
    path = _server_root() / "sql" / "migrations" / dialect / "125_tr2_attempt_durable_ownership.sql"
    sql = path.read_text(encoding="utf-8")
    assert "DROP" in sql and "document" in sql.lower()
    assert "ON DELETE SET NULL" in sql


def test_lifecycle_ownership_ignores_failed_and_recovery_required(monkeypatch):
    rows = [
        {"attempt_id": "ok", "state": "succeeded",
         "commit_json": {"paths": ["server/ok.py"]}},
    ]
    monkeypatch.setattr(policy.db_attempts, "successful_by_group", lambda _gid: rows)
    assert policy.managed_paths("flowgate.default.0641") == {"server/ok.py"}
    assert policy.is_managed("flowgate.default.0641", "server/ok.py")
    assert not policy.is_managed("flowgate.default.0641", "server/failed.py")
    assert not policy.is_managed("flowgate.default.0641", "server/recovery.py")


def test_time_machine_ledger_state_is_not_ownership_authority(monkeypatch):
    row = {
        "attempt_id": "ok",
        "state": "succeeded",
        "ledger_row_id": 1,
        "ledger_json": {"id": 1, "state": "live"},
        "commit_json": {"paths": ["server/a.py"]},
    }
    monkeypatch.setattr(
        policy.db_attempts, "successful_by_group", lambda _gid: [dict(row)]
    )
    assert policy.managed_paths("flowgate.default.0641") == {"server/a.py"}
    row["ledger_json"] = {"id": 1, "state": "canceled"}
    assert policy.managed_paths("flowgate.default.0641") == {"server/a.py"}
    row["ledger_row_id"] = 2
    row["ledger_json"] = {"id": 2, "state": "live", "restored_from_id": 1}
    assert policy.managed_paths("flowgate.default.0641") == {"server/a.py"}


def test_historical_backfill_zero_contract():
    root = _server_root()
    sqlite_dir = root / "sql" / "migrations" / "sqlite"
    seed = (sqlite_dir / "113_seed_t2_tr2_doctypes.sql").read_text(encoding="utf-8")
    attempts = (sqlite_dir / "114_tr2_approval_attempts.sql").read_text(encoding="utf-8")
    approval = (
        root / "modules" / "flow_gate" / "documents" / "tr2_approval_service.py"
    ).read_text(encoding="utf-8")
    assert "TR2" in seed
    assert "tr2_approval_attempts" not in seed
    assert "CREATE TABLE IF NOT EXISTS tr2_approval_attempts" in attempts
    assert "db_attempts.create(" in approval
    assert "adapter.apply_all(" in approval
    assert "_commit_exact(" in approval
    for path in sqlite_dir.glob("*.sql"):
        try:
            number = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        if number < 114:
            assert "tr2_approval_attempts" not in path.read_text(encoding="utf-8")


def _shared_project_lock(monkeypatch):
    lock = threading.Lock()
    owner = {"value": None}

    def acquire(_project, holder, wait_sec=None):
        ok = lock.acquire(timeout=3)
        if ok:
            owner["value"] = holder
        return ok

    def release(_project, holder):
        assert owner["value"] == holder
        owner["value"] = None
        lock.release()

    monkeypatch.setattr(policy.git_service, "_acquire_lock", acquire)
    monkeypatch.setattr(policy.db_git, "release_lock", release)


def test_interleaving_ordinary_first_tr2_reads_latest(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    target = root / "a.py"
    target.write_text("before", encoding="utf-8")
    _shared_project_lock(monkeypatch)
    managed = set()
    monkeypatch.setattr(policy, "_group_root", lambda *_: root)
    monkeypatch.setattr(policy, "managed_paths", lambda _gid: set(managed))
    monkeypatch.setattr(tr2_precheck, "_approval_root", lambda *_: root)

    entered = threading.Event()
    finish = threading.Event()
    seen = {}

    def ordinary():
        with policy.general_source_mutation(
            "flowgate", "flowgate.default.0641", exact_paths=["a.py"]
        ):
            entered.set()
            assert finish.wait(3)
            target.write_text("ordinary-new", encoding="utf-8")

    def tr2():
        with tr2_precheck.source_lock("flowgate", "flowgate.default.0641"):
            seen["source"] = target.read_text(encoding="utf-8")
            managed.add("a.py")

    a = threading.Thread(target=ordinary)
    a.start()
    assert entered.wait(3)
    b = threading.Thread(target=tr2)
    b.start()
    finish.set()
    a.join(3)
    b.join(3)
    assert not a.is_alive() and not b.is_alive()
    assert seen["source"] == "ordinary-new"
    assert managed == {"a.py"}


def test_interleaving_tr2_first_ordinary_rereads_and_blocks(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    target = root / "a.py"
    target.write_text("before", encoding="utf-8")
    _shared_project_lock(monkeypatch)
    managed = set()
    monkeypatch.setattr(policy, "_group_root", lambda *_: root)
    monkeypatch.setattr(policy, "managed_paths", lambda _gid: set(managed))
    monkeypatch.setattr(tr2_precheck, "_approval_root", lambda *_: root)

    entered = threading.Event()
    finish = threading.Event()
    result = {}

    def tr2():
        with tr2_precheck.source_lock("flowgate", "flowgate.default.0641"):
            entered.set()
            assert finish.wait(3)
            managed.add("a.py")

    def ordinary():
        try:
            with policy.general_source_mutation(
                "flowgate", "flowgate.default.0641", exact_paths=["a.py"]
            ):
                target.write_text("must-not-happen", encoding="utf-8")
        except policy.Tr2FilePolicyError as exc:
            result["code"] = exc.code

    a = threading.Thread(target=tr2)
    a.start()
    assert entered.wait(3)
    b = threading.Thread(target=ordinary)
    b.start()
    finish.set()
    a.join(3)
    b.join(3)
    assert not a.is_alive() and not b.is_alive()
    assert result["code"] == policy.TR2_MANAGED_FILE
    assert target.read_text(encoding="utf-8") == "before"
