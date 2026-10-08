"""flowgate.default.0641 T2#1 — durable TR2 ownership and mutation-guard foundation."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from group_lock_stub import group_store  # noqa: F401
from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.documents import tr2_precheck
from modules.flow_gate.services import tr2_file_policy as policy
from modules.flow_gate.storage.safe_path import (
    MutationPathAliasError,
    resolve_mutation_target_no_alias,
)


@pytest.fixture(autouse=True)
def _real_group_locks(group_store):
    """Ordinary mutation and TR2 take the Group's G in a real store."""
    yield


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


def test_ownership_rows_is_uncapped_and_keeps_terminal_live_shape(monkeypatch):
    seen = {}

    class Store:
        def _fetch_all(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return []

    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    monkeypatch.setattr(db_ledger, "get_store", lambda: Store())
    assert db_ledger.ownership_rows("flowgate.default.0641") == []
    assert "LIMIT" not in seen["sql"].upper()
    assert "reopened_terminal_at" in seen["sql"]
    assert seen["params"] == ["flowgate.default.0641"]


def test_managed_paths_tracks_live_reapply_lineage_only(monkeypatch):
    monkeypatch.setattr(
        policy.db_attempts,
        "successful_by_group",
        lambda _gid: [
            {"attempt_id": "a1", "ledger_row_id": 1,
             "commit_json": json.dumps({"paths": ["server/./a.py", "client/b.ts"]})},
            {"attempt_id": "a2", "ledger_row_id": 3,
             "commit_json": {"paths": ["server/c.py"]}},
        ],
    )
    monkeypatch.setattr(
        policy.db_ledger,
        "ownership_rows",
        lambda _gid: [
            {"id": 1, "group_id": "flowgate.default.0641", "doc_id": "tr2a",
             "state": "canceled", "restored_from_id": None, "reopened_terminal_at": None,
             "doc_type_code": "TR2"},
            {"id": 2, "group_id": "flowgate.default.0641", "doc_id": "tr2a",
             "state": "live", "restored_from_id": 1, "reopened_terminal_at": None,
             "doc_type_code": "TR2"},
            {"id": 3, "group_id": "flowgate.default.0641", "doc_id": "tr2b",
             "state": "canceled", "restored_from_id": None, "reopened_terminal_at": None,
             "doc_type_code": "TR2"},
            {"id": 4, "group_id": "flowgate.default.0641", "doc_id": "ordinary-tr",
             "state": "live", "restored_from_id": None, "reopened_terminal_at": None,
             "doc_type_code": "TR"},
        ],
    )
    assert policy.managed_paths("flowgate.default.0641") == {
        "server/a.py", "client/b.ts",
    }


def test_managed_paths_fails_closed_on_malformed_active_attempt(monkeypatch):
    monkeypatch.setattr(
        policy.db_attempts,
        "successful_by_group",
        lambda _gid: [{"attempt_id": "bad", "ledger_row_id": 1, "commit_json": "{}"}],
    )
    monkeypatch.setattr(
        policy.db_ledger,
        "ownership_rows",
        lambda _gid: [
            {"id": 1, "group_id": "flowgate.default.0641", "doc_id": "tr2",
             "state": "live", "restored_from_id": None, "reopened_terminal_at": None,
             "doc_type_code": "TR2"},
        ],
    )
    with pytest.raises(policy.Tr2OwnershipInvariantError) as exc:
        policy.managed_paths("flowgate.default.0641")
    assert exc.value.code == policy.TR2_OWNERSHIP_INVARIANT


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
        policy, "_acquire_group",
        lambda project_id, group_id: events.append("lock") or ("ctx", "key"),
    )
    monkeypatch.setattr(
        policy, "_release_group",
        lambda handle: events.append("unlock"),
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

    monkeypatch.setattr(policy, "_acquire_group", lambda *_: ("ctx", "key"))
    monkeypatch.setattr(policy, "_release_group", lambda *_: None)
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


def _install_ownership(monkeypatch, attempts, ledger_rows):
    monkeypatch.setattr(policy.db_attempts, "successful_by_group", lambda _gid: attempts)
    monkeypatch.setattr(policy.db_ledger, "ownership_rows", lambda _gid: ledger_rows)


def test_time_machine_cancel_unlocks_and_second_reapply_relocks(monkeypatch):
    attempt = {"attempt_id": "ok", "ledger_row_id": 1,
               "commit_json": {"paths": ["server/a.py"]}}
    root = {"id": 1, "group_id": "g", "doc_id": "tr2", "state": "canceled",
            "restored_from_id": None, "reopened_terminal_at": None, "doc_type_code": "TR2"}
    first = {"id": 2, "group_id": "g", "doc_id": "tr2", "state": "canceled",
             "restored_from_id": 1, "reopened_terminal_at": None, "doc_type_code": "TR2"}
    second = {"id": 3, "group_id": "g", "doc_id": "tr2", "state": "live",
              "restored_from_id": 2, "reopened_terminal_at": None, "doc_type_code": "TR2"}

    _install_ownership(monkeypatch, [attempt], [root])
    assert policy.managed_paths("g") == set()

    _install_ownership(monkeypatch, [attempt], [root, first, second])
    assert policy.managed_paths("g") == {"server/a.py"}


def test_terminal_reopen_remains_managed(monkeypatch):
    attempt = {"attempt_id": "ok", "ledger_row_id": 1,
               "commit_json": {"paths": ["server/a.py"]}}
    terminal = {"id": 1, "group_id": "g", "doc_id": "tr2", "state": "live",
                "restored_from_id": None, "reopened_terminal_at": "2026-09-29T00:00:00Z",
                "doc_type_code": "TR2"}
    _install_ownership(monkeypatch, [attempt], [terminal])
    assert policy.managed_paths("g") == {"server/a.py"}


def test_invalid_persisted_active_ownership_path_fails_invariant(monkeypatch):
    attempt = {"attempt_id": "bad-path", "ledger_row_id": 1,
               "commit_json": {"paths": ["../outside.py"]}}
    live = {"id": 1, "group_id": "g", "doc_id": "tr2", "state": "live",
            "restored_from_id": None, "reopened_terminal_at": None,
            "doc_type_code": "TR2"}
    _install_ownership(monkeypatch, [attempt], [live])

    with pytest.raises(policy.Tr2OwnershipInvariantError) as exc:
        policy.managed_paths("g")
    assert exc.value.code == policy.TR2_OWNERSHIP_INVARIANT
    assert exc.value.details["cause_code"] == policy.SOURCE_PATH_INVALID
    assert exc.value.details["path"] == "../outside.py"


def test_multiple_active_owners_release_only_after_last_cancel(monkeypatch):
    attempts = [
        {"attempt_id": "a", "ledger_row_id": 1, "commit_json": {"paths": ["server/a.py"]}},
        {"attempt_id": "b", "ledger_row_id": 2, "commit_json": {"paths": ["server/a.py"]}},
    ]
    rows = [
        {"id": 1, "group_id": "g", "doc_id": "a", "state": "canceled",
         "restored_from_id": None, "reopened_terminal_at": None, "doc_type_code": "TR2"},
        {"id": 2, "group_id": "g", "doc_id": "b", "state": "live",
         "restored_from_id": None, "reopened_terminal_at": None, "doc_type_code": "TR2"},
    ]
    _install_ownership(monkeypatch, attempts, rows)
    assert policy.managed_paths("g") == {"server/a.py"}
    rows[1]["state"] = "canceled"
    assert policy.managed_paths("g") == set()


@pytest.mark.parametrize("broken", ["missing", "cycle", "missing_root_attempt"])
def test_broken_live_tr2_lineage_fails_closed(monkeypatch, broken):
    attempt = {"attempt_id": "ok", "ledger_row_id": 1,
               "commit_json": {"paths": ["server/a.py"]}}
    root = {"id": 1, "group_id": "g", "doc_id": "tr2", "state": "canceled",
            "restored_from_id": None, "reopened_terminal_at": None, "doc_type_code": "TR2"}
    live = {"id": 2, "group_id": "g", "doc_id": "tr2", "state": "live",
            "restored_from_id": 1, "reopened_terminal_at": None, "doc_type_code": "TR2"}
    attempts = [attempt]
    rows = [root, live]
    if broken == "missing":
        rows = [live]
    elif broken == "cycle":
        root["restored_from_id"] = 2
    else:
        attempts = []

    _install_ownership(monkeypatch, attempts, rows)
    with pytest.raises(policy.Tr2OwnershipInvariantError) as exc:
        policy.managed_paths("g")
    assert exc.value.code == policy.TR2_OWNERSHIP_INVARIANT


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


def test_interleaving_ordinary_first_tr2_reads_latest(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    target = root / "a.py"
    target.write_text("before", encoding="utf-8")
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
