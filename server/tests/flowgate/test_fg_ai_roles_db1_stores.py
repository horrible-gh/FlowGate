"""flowgate.v02.0113 T#1 — DB-1 stores over a real SQLite DB (every migration, the live FlowGateStore).

- §4.3 CI (``FlowGateStore.insert_isolated``): T-1 lost first save keeps the earlier write and the first
  completion time, T-2 a CHECK violation is an error and not a conflict, T-3 the same record twice is one row;
  a savepoint opened as the transaction's first statement still nests (SQLite deferred mode).
- W-1 retry on lock failures (three attempts), no retry on other errors.
- ``role_store`` (R-1~R-3, W-2~W-5), ``assignment_store`` (R-4~R-9, W-7~W-9), ``record_store`` (R-10~R-18,
  W-10, W-12~W-14, unit key, create_seq issuer, provider fingerprint key).
- §5.4 O-1~O-3: creation order is ``create_seq``, never insert order or ``created_at``; R-11 uses ``idx_ari_unit``
  as a covering index.
- W-11 / W-15: the ledger request and the finished run row carry the new columns only when given and present;
  a DB before 164 / 165 writes exactly as before. I-17: the 90-day run sweep leaves the records.
PostgreSQL / MySQL concurrency (§5.4 T-4~T-7) needs live servers and is not run here.
"""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

T = "2026-10-30T00:00:00.000+00:00"
OLD = "2026-01-01T00:00:00+09:00"
HEX = "0123456789abcdef" * 4
_TEMPLATES: dict = {}


def _template(upto):
    if upto not in _TEMPLATES:
        memory = sqlite3.connect(":memory:")
        for mig in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if upto is not None and mig.name > upto:
                break
            memory.executescript(mig.read_text(encoding="utf-8"))
        path = Path(tempfile.mkdtemp(prefix="db1s_")) / "template.db"
        target = sqlite3.connect(str(path))
        memory.backup(target)
        target.close()
        memory.close()
        _TEMPLATES[upto] = str(path)
    return _TEMPLATES[upto]


def _seed(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES ('u1', 'u1', 'u1@example.com', 'x', ?, ?)", [T, T])
    conn.execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES ('p1', 'p1', ?, ?)",
                 [T, T])
    conn.execute("INSERT INTO groups (group_id, project_id, title, created_at, updated_at) "
                 "VALUES ('g1', 'p1', 'g1', ?, ?)", [T, T])
    conn.execute("INSERT INTO graph_revisions (group_id, revision_no, source_draft_version, confirmed_at) "
                 "VALUES ('g1', 1, 1, ?)", [T])
    conn.execute("INSERT INTO graph_runs (run_id, group_id, revision_no, status, started_at, policy_source, "
                 "policy_set_id, policy_set_version, policy_digest, updated_at) "
                 "VALUES ('r1', 'g1', 1, 'running', ?, 'flow', 'ps', '1', ?, ?)", [T, HEX, T])
    conn.execute("INSERT INTO graph_node_attempts (attempt_id, run_id, node_key, revision_no, status, created_at) "
                 "VALUES ('a1', 'r1', 'n1', 1, 'active', ?)", [T])
    conn.commit()
    conn.close()


def _open(tmp_path, monkeypatch, upto=None):
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.db import connection as conn_mod

    path = str(tmp_path / "stores.db")
    shutil.copyfile(_template(upto), path)
    _seed(path)
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    monkeypatch.setattr(conn_mod, "STORE", store)
    return store, path


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store, path = _open(tmp_path, monkeypatch)
    return {"store": store, "path": path}


@pytest.fixture()
def old_env(tmp_path, monkeypatch):
    """A DB before migration 163 (every file up to 162)."""
    store, path = _open(tmp_path, monkeypatch, upto="162_zzz")
    return {"store": store, "path": path}


def _rows(env, sql, params=()):
    conn = sqlite3.connect(env["path"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


# ── connection helpers ───────────────────────────────────────────────────────

class _PgError(Exception):
    def __init__(self, code):
        super().__init__("pg")
        self.pgcode = code


def test_error_classification_for_three_dialects():
    from modules.flow_gate.db.connection import is_transient_failure, is_unique_violation

    assert is_unique_violation(sqlite3.IntegrityError("UNIQUE constraint failed: t.k"))
    assert not is_unique_violation(sqlite3.IntegrityError("CHECK constraint failed: x"))
    assert is_unique_violation(_PgError("23505")) and not is_unique_violation(_PgError("23514"))
    assert is_unique_violation(Exception(1062, "Duplicate entry")) and not is_unique_violation(Exception(3819, "x"))
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = sqlite3.IntegrityError("UNIQUE constraint failed: t.k")
    assert is_unique_violation(wrapped)
    assert is_transient_failure(sqlite3.OperationalError("database is locked"))
    assert is_transient_failure(_PgError("40P01")) and is_transient_failure(_PgError("40001"))
    assert is_transient_failure(Exception(1213, "Deadlock")) and is_transient_failure(Exception(1205, "wait"))
    assert not is_transient_failure(sqlite3.OperationalError("no such table: x"))
    assert not is_transient_failure(_PgError("23505"))


def test_column_exists(env):
    store = env["store"]
    assert store.column_exists("graph_attempt_runs", "assignment_stage")
    assert store.column_exists("ai_invoke_runs", "graph_exec_id")
    assert not store.column_exists("ai_invoke_runs", "nope")
    assert not store.column_exists("no_such_table", "x")


def test_before_163_every_store_reports_off(old_env):
    from flowgate.ai_roles import assignment_store, record_store, role_store

    assert not role_store.available() and not assignment_store.available() and not record_store.available()
    assert not assignment_store.ledger_columns_available() and not record_store.run_link_columns_available()
    assert assignment_store.ledger_exec_for("a1", "result", 0) is None
    assert record_store.runs_for_exec("e") == []


def test_insert_isolated_needs_an_open_transaction_and_a_plain_savepoint_name(env):
    store = env["store"]
    with pytest.raises(RuntimeError):
        store.insert_isolated("INSERT INTO gate_data_migrations (name, applied_at) VALUES (?, ?)", ["x", T], "sp")
    with store.transaction():
        with pytest.raises(ValueError):
            store.insert_isolated("INSERT INTO gate_data_migrations (name, applied_at) VALUES (?, ?)", ["x", T],
                                  "sp; DROP TABLE x")


def test_a_savepoint_as_first_statement_still_nests_in_the_transaction(env):
    """Without the explicit BEGIN the RELEASE would commit the INSERT on its own and the rollback below
    would leave it behind."""
    store = env["store"]
    with pytest.raises(RuntimeError):
        with store.transaction():
            assert store.insert_isolated("INSERT INTO gate_data_migrations (name, applied_at) VALUES (?, ?)",
                                         ["first", T], "fg_t_ins")
            raise RuntimeError("later failure")
    assert _rows(env, "SELECT * FROM gate_data_migrations WHERE name = 'first'") == []


def test_t2_a_check_violation_inside_ci_is_an_error_and_the_transaction_goes_on(env):
    from flowgate.ai_roles import record_store

    store = env["store"]
    with store.transaction():
        store._execute("INSERT INTO gate_data_migrations (name, applied_at) VALUES ('before', ?)", [T])
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            record_store.insert(_record(inv_id="bad#1", ai_run_id="bad", create_seq=0))
        store._execute("INSERT INTO gate_data_migrations (name, applied_at) VALUES ('after', ?)", [T])
    names = {r["name"] for r in _rows(env, "SELECT name FROM gate_data_migrations")}
    assert {"before", "after"} <= names
    assert _rows(env, "SELECT * FROM ai_role_invocations") == []


# ── role_store ───────────────────────────────────────────────────────────────

def _values(instruction="p1", execution=None, review="p2"):
    from flowgate.ai_roles.role_store import ASSIGNED, NOT_USED, RoleValue

    def one(pid):
        return RoleValue(NOT_USED) if pid is None else RoleValue(ASSIGNED, pid, f"name of {pid}")
    return {"instruction": one(instruction), "execution": one(execution), "review": one(review)}


def test_account_roles_first_save_then_update_keeps_the_completion_time(env):
    from flowgate.ai_roles import role_store

    assert role_store.available()
    assert role_store.account_roles("u1") is None                                     # R-1: not set up
    first = role_store.save_account_roles("u1", _values(), now="2026-10-30T01:00:00.000+00:00")
    assert first.setup_completed_at == "2026-10-30T01:00:00.000+00:00"
    assert first.values["execution"].kind == "not_used" and first.values["instruction"].provider_id == "p1"
    second = role_store.save_account_roles("u1", _values(instruction=None, execution="p3"),
                                           now="2026-10-30T02:00:00.000+00:00")
    assert second.setup_completed_at == "2026-10-30T01:00:00.000+00:00"              # I-2
    assert second.updated_at == "2026-10-30T02:00:00.000+00:00"
    assert (second.values["instruction"].kind, second.values["execution"].provider_id) == ("not_used", "p3")
    assert len(_rows(env, "SELECT * FROM user_ai_role_defaults")) == 1


def test_account_name_snapshot_is_cut_to_100_and_all_three_roles_are_required(env):
    from flowgate.ai_roles import role_store
    from flowgate.ai_roles.role_store import ASSIGNED, RoleValue

    values = _values()
    values["review"] = RoleValue(ASSIGNED, "p2", "N" * 150)
    stored = role_store.save_account_roles("u1", values)
    assert stored.values["review"].provider_name == "N" * 100
    with pytest.raises(ValueError):
        role_store.save_account_roles("u1", {"instruction": values["instruction"]})


def test_t1_a_lost_first_save_updates_the_winner_and_keeps_the_earlier_write(env, monkeypatch):
    """W-1 ② hits the unique key (a competitor committed between ① and ②): CI-4, then ③ in the same
    transaction. The competitor's completion time stays, our three values win, the earlier write survives."""
    from flowgate.ai_roles import role_store

    store = env["store"]
    real = store._execute_affected
    calls = {"n": 0}

    def racing(sql, params=None):
        calls["n"] += 1
        if calls["n"] == 1 and sql.startswith("UPDATE user_ai_role_defaults"):
            store._execute(
                "INSERT INTO user_ai_role_defaults (user_id, instruction_kind, execution_kind, review_kind, "
                "setup_completed_at, updated_at) VALUES ('u1', 'not_used', 'not_used', 'not_used', ?, ?)",
                ["2026-10-30T00:00:01.000+00:00", "2026-10-30T00:00:01.000+00:00"])
            return 0
        return real(sql, params)

    monkeypatch.setattr(store, "_execute_affected", racing)
    with store.transaction():
        store._execute("INSERT INTO gate_data_migrations (name, applied_at) VALUES ('earlier', ?)", [T])
        stored = role_store.save_account_roles("u1", _values(), now="2026-10-30T00:00:02.000+00:00")
    assert stored.setup_completed_at == "2026-10-30T00:00:01.000+00:00"
    assert stored.values["instruction"].provider_id == "p1"
    row = _rows(env, "SELECT * FROM user_ai_role_defaults")[0]
    assert row["instruction_provider_id"] == "p1" and row["updated_at"] == "2026-10-30T00:00:02.000+00:00"
    assert _rows(env, "SELECT * FROM gate_data_migrations WHERE name = 'earlier'")


def test_w1_runs_again_on_a_lock_failure_and_gives_up_after_three(env, monkeypatch):
    from flowgate.ai_roles import role_store

    attempts = {"n": 0}
    real = role_store._save_account_once

    def flaky(*args):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return real(*args)

    monkeypatch.setattr(role_store, "_save_account_once", flaky)
    assert role_store.save_account_roles("u1", _values()).values["review"].provider_id == "p2"
    assert attempts["n"] == 3

    attempts["n"] = -10
    with pytest.raises(role_store.AccountRolesWriteError):
        role_store.save_account_roles("u1", _values())

    def broken(*args):
        attempts["n"] += 1
        raise sqlite3.IntegrityError("CHECK constraint failed")

    attempts["n"] = 0
    monkeypatch.setattr(role_store, "_save_account_once", broken)
    with pytest.raises(sqlite3.IntegrityError):
        role_store.save_account_roles("u1", _values())
    assert attempts["n"] == 1


def test_account_row_goes_with_the_user_and_keeps_a_deleted_ai_id(env):
    from flowgate.ai_roles import role_store

    role_store.save_account_roles("u1", _values(instruction="deleted-ai"))
    assert role_store.account_roles("u1").values["instruction"].provider_id == "deleted-ai"   # I-7: no FK
    conn = sqlite3.connect(env["path"])
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM users WHERE user_id = 'u1'")
    conn.commit()
    conn.close()
    assert role_store.account_roles("u1") is None


def test_slots_replace_report_change_and_only_wp_plan_slots_are_removed(env):
    from flowgate.ai_roles import role_store
    from flowgate.ai_roles.role_store import ASSIGNED, NOT_USED, RoleSlot, RoleValue

    store = env["store"]
    with store.transaction():
        assert role_store.write_slot("g1", "n1", RoleSlot("execution", RoleValue(ASSIGNED, "p1", "P1"),
                                                          "wp_plan", "u1", T))
        assert role_store.write_slot("g1", "n1", RoleSlot("review", RoleValue(NOT_USED), "pending_fill", None, T))
        assert not role_store.write_slot("g1", "n1", RoleSlot("execution", RoleValue(ASSIGNED, "p1", "P1"),
                                                              "wp_plan", "u1", T))
    found = role_store.slots("g1", "n1")
    assert set(found) == {"execution", "review"} and found["review"].set_by is None
    assert not role_store.remove_wp_plan_slot("g1", "n1", "review")            # pending_fill stays (I-4)
    assert role_store.remove_wp_plan_slot("g1", "n1", "execution")
    assert set(role_store.slots("g1", "n1")) == {"review"}
    with store.transaction():
        assert role_store.write_slot("g1", "n1", RoleSlot("review", RoleValue(ASSIGNED, "p9"), "card_explicit",
                                                          "u1", T))
    assert role_store.slots("g1", "n1")["review"].source == "card_explicit"


def test_card_init_legacy_pending_then_initialized_only(env):
    from flowgate.ai_roles import role_store

    assert role_store.card_init("g1", "n1") is None                                           # LEGACY
    role_store.create_card_init("g1", "n1", role_store.PENDING_INIT, "u1", now=T)
    with pytest.raises(sqlite3.IntegrityError):
        role_store.create_card_init("g1", "n1", role_store.INITIALIZED, "u1", now=T)
    assert role_store.mark_initialized("g1", "n1", now="2026-10-30T03:00:00.000+00:00")
    assert not role_store.mark_initialized("g1", "n1")
    init = role_store.card_init("g1", "n1")
    assert (init.state, init.creator_id, init.initialized_at) == ("initialized", "u1", "2026-10-30T03:00:00.000+00:00")
    legacy = role_store.create_card_init("g1", "n2", role_store.INITIALIZED, None, now=T)
    assert legacy.initialized_at == T and role_store.card_init("g1", "n2").creator_id is None


# ── assignment_store ─────────────────────────────────────────────────────────

def _assignment(**over):
    row = {"attempt_id": "a1", "stage": "result", "seq": 0, "run_id": "r1", "group_id": "g1", "node_key": "n1",
           "role": "execution", "actor": "ai", "provider_id": "p1", "provider_name": "P1", "source": "card_slot",
           "decided_at": T, "stage_state": "active"}
    row.update(over)
    return row


def test_w7_first_writer_wins_and_the_loser_gets_the_stored_row(env):
    from flowgate.ai_roles import assignment_store

    store = env["store"]
    assert assignment_store.available() and not assignment_store.has_any("a1")
    with store.transaction():
        stored, inserted = assignment_store.insert_if_absent(_assignment())
    assert inserted and stored["provider_id"] == "p1" and stored["updated_at"] == T
    with store.transaction():
        again, inserted = assignment_store.insert_if_absent(_assignment(provider_id="other"))
    assert not inserted and again["provider_id"] == "p1"
    assert assignment_store.has_any("a1") and assignment_store.read("a1", "result")["actor"] == "ai"
    with pytest.raises(ValueError):
        assignment_store.insert_if_absent({**_assignment(), "bogus": 1})


def test_w7_a_conflict_past_the_read_returns_the_committed_winner(env, monkeypatch):
    """The guard-skipping race of DB-1 §4.3: ① saw nothing, ② hits the key, ④ reads the winner (R-4L)."""
    from flowgate.ai_roles import assignment_store

    store = env["store"]
    with store.transaction():
        assignment_store.insert_if_absent(_assignment())
    monkeypatch.setattr(assignment_store, "read", lambda *key: None)
    with store.transaction():
        store._execute("INSERT INTO gate_data_migrations (name, applied_at) VALUES ('loser-before', ?)", [T])
        winner, inserted = assignment_store.insert_if_absent(_assignment(provider_id="loser"))
    assert not inserted and winner["provider_id"] == "p1"
    assert _rows(env, "SELECT * FROM gate_data_migrations WHERE name = 'loser-before'")
    assert len(_rows(env, "SELECT * FROM graph_stage_assignments")) == 1


def test_w7_needs_the_callers_transaction_and_a_check_violation_is_not_a_conflict(env):
    from flowgate.ai_roles import assignment_store

    with pytest.raises(RuntimeError):
        assignment_store.insert_if_absent(_assignment())
    with pytest.raises(sqlite3.IntegrityError):
        with env["store"].transaction():
            assignment_store.insert_if_absent(_assignment(provider_id=None))


def test_w8_state_cas_w9_intent_once_and_the_reads(env):
    from flowgate.ai_roles import assignment_store

    store = env["store"]
    with store.transaction():
        assignment_store.insert_if_absent(_assignment())
        for seq in (1, 2):
            assignment_store.insert_if_absent(_assignment(
                stage="result_review", seq=seq, role="review", review_rounds=2, author="ai", author_provider_id="p1",
                basis_doc_id="d", basis_revision_no=seq, basis_rejection_count=0))
    assert not assignment_store.update_state("a1", "result", 0, "waiting", "done")
    assert assignment_store.update_state("a1", "result", 0, "active", "failed", fail_cause="start_refused:busy")
    assert not assignment_store.update_state("a1", "result", 0, "active", "done")
    assert assignment_store.read("a1", "result")["fail_cause"] == "start_refused:busy"
    assert assignment_store.set_intent("a1", "result", 0, "execution", "exe_1")
    assert not assignment_store.set_intent("a1", "result", 0, "approval")
    assert assignment_store.read("a1", "result")["intent_exec_id"] == "exe_1"
    assert assignment_store.latest("a1", "result_review")["seq"] == 2                              # R-5
    assert assignment_store.latest("a1", "instruction_review") is None
    assert [(r["stage"], r["seq"]) for r in assignment_store.for_node("r1", "n1", "a1")] == [
        ("result", 0), ("result_review", 1), ("result_review", 2)]
    with pytest.raises(sqlite3.IntegrityError):                                                       # CK-G14
        assignment_store.set_intent("a1", "result_review", 1, "execution", None)


def test_w11_the_ledger_request_carries_its_assignment_and_r9_finds_it(env):
    from flowgate.ai_roles import assignment_store
    from flowgate.graph_run import execution_store

    exec_id = execution_store.request("a1", "r1", "write", {"type": "write"}, assignment=("result", 0))
    row = _rows(env, "SELECT * FROM graph_attempt_runs WHERE exec_id = ?", [exec_id])[0]
    assert (row["assignment_stage"], row["assignment_seq"]) == ("result", 0)
    assert assignment_store.ledger_exec_for("a1", "result", 0) == exec_id
    assert assignment_store.ledger_exec_for("a1", "result", 1) is None


def test_w11_without_an_assignment_the_ledger_row_is_as_before(env):
    from flowgate.graph_run import execution_store

    exec_id = execution_store.request("a1", "r1", "write", {"type": "write"})
    row = _rows(env, "SELECT * FROM graph_attempt_runs WHERE exec_id = ?", [exec_id])[0]
    assert row["assignment_stage"] is None and row["assignment_seq"] is None


def test_w11_before_164_an_assignment_is_not_bound_and_the_request_still_lands(old_env):
    from flowgate.graph_run import execution_store

    exec_id = execution_store.request("a1", "r1", "write", {"type": "write"}, assignment=("result", 0))
    assert _rows(old_env, "SELECT exec_id FROM graph_attempt_runs WHERE exec_id = ?", [exec_id])


def test_attempt_delete_takes_its_assignments_invalidation_does_not(env):
    from flowgate.ai_roles import assignment_store

    with env["store"].transaction():
        assignment_store.insert_if_absent(_assignment())
    conn = sqlite3.connect(env["path"])
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("UPDATE graph_node_attempts SET status = 'invalidated' WHERE attempt_id = 'a1'")
    conn.commit()
    assert assignment_store.read("a1", "result") is not None
    conn.execute("DELETE FROM graph_runs WHERE run_id = 'r1'")
    conn.commit()
    conn.close()
    assert assignment_store.read("a1", "result") is None


# ── record_store ─────────────────────────────────────────────────────────────

def _record(**over):
    from flowgate.ai_roles import record_store

    row = {"inv_id": "run1#1", "create_seq": 1000, "ai_run_id": "run1", "launch_no": 1,
           "origin_kind": "card_assigned", "inv_role": "execution", "purpose": "write", "target_group_id": "g1",
           "target_graph_run_id": "r1", "target_node_key": "n1", "target_attempt_id": "a1",
           "target_stage": "result", "target_seq": 0, "requested_model_state": "value",
           "requested_model_value": "m", "result": "running", "link_kind": "first", "created_at": T}
    row.update(over)
    if "unit_kind" not in row:
        kind, parts = record_store.unit_key(row)
        row["unit_kind"], row["unit_key_hash"] = kind, record_store.unit_key_hash(parts)
    return row


_ENDED = {"result": "succeeded", "ended_at": T, "confirmed_model_state": "confirmed",
          "confirmed_model_values_json": '["m"]', "confirmed_coverage": "full", "exit_code": 0}


def test_unit_key_hash_is_the_canonical_json_sha256():
    import hashlib

    from flowgate.ai_roles import record_store

    kind, parts = record_store.unit_key(_record())
    assert kind == "assignment" and parts == ["assignment", "a1", "result", 0]
    expected = hashlib.sha256('["assignment","a1","result",0]'.encode("utf-8")).hexdigest()
    assert record_store.unit_key_hash(parts) == expected and len(expected) == 64
    assert record_store.unit_key_hash(["qa", "질문"]) == hashlib.sha256('["qa","질문"]'.encode("utf-8")).hexdigest()
    assert record_store.unit_key({"origin_kind": "non_card", "ai_run_id": None, "inv_id": "refused#x"}) == (
        "run", ["run", "refused#x"])
    assert record_store.unit_key({"origin_kind": "card_legacy_attempt", "target_graph_run_id": "r",
                                  "target_node_key": "n", "target_attempt_id": "a", "target_stage": "result"})[1] == [
        "legacy", "r", "n", "a", "result"]
    with pytest.raises(ValueError):
        record_store.unit_key({"origin_kind": "other"})


def test_create_seq_issuer_is_strictly_increasing_and_respects_floors():
    from flowgate.ai_roles import record_store

    a = record_store.issue_create_seq()
    b = record_store.issue_create_seq()
    assert b > a >= 1
    far = b + 10 ** 9
    assert record_store.issue_create_seq(far, None) == far
    assert record_store.issue_create_seq() == far + 1


def test_t3_w10_twice_is_one_row_and_w12_w13_w14_change_only_once(env):
    from flowgate.ai_roles import record_store

    store = env["store"]
    assert record_store.available()
    with store.transaction():
        assert record_store.insert(_record())
    with store.transaction():
        assert not record_store.insert(_record())
    assert len(_rows(env, "SELECT * FROM ai_role_invocations")) == 1
    assert record_store.finish("run1#1", _ENDED, now="2026-10-30T05:00:00.000+00:00")
    assert not record_store.finish("run1#1", {**_ENDED, "exit_code": 9})                    # I-14
    assert record_store.annotate_run("run1#1", "completed", None)
    assert not record_store.annotate_run("run1#1", "other", "x")
    assert record_store.fill_root("run1#1", "run1#1") and not record_store.fill_root("run1#1", "other")
    row = _rows(env, "SELECT * FROM ai_role_invocations")[0]
    assert (row["result"], row["exit_code"], row["run_end_reason"], row["chain_root_id"]) == (
        "succeeded", 0, "completed", "run1#1")
    assert row["updated_at"] != T


def test_w10_a_clash_with_another_record_of_the_same_launch_is_already_there_with_a_warning(env, caplog):
    from flowgate.ai_roles import record_store

    with env["store"].transaction():
        record_store.insert(_record())
    with env["store"].transaction():
        assert not record_store.insert(_record(inv_id="run1#1", create_seq=5))
    assert "differs" in caplog.text


def test_write_record_is_its_own_transaction_and_never_raises(env, caplog):
    from flowgate.ai_roles import record_store

    ended = _record(inv_id="run2#1", ai_run_id="run2", create_seq=2000, **_ENDED,
                    run_end_reason="completed", updated_at="2026-10-30T06:00:00.000+00:00")
    assert record_store.write_record(_record(inv_id="run2#1", ai_run_id="run2", create_seq=2000))
    assert record_store.write_record(ended)                                    # later write point: W-12 + W-13
    assert record_store.write_record(ended)                                    # repeat: same result
    row = _rows(env, "SELECT * FROM ai_role_invocations WHERE inv_id = 'run2#1'")[0]
    assert (row["result"], row["run_end_reason"], row["create_seq"]) == ("succeeded", "completed", 2000)
    assert not record_store.write_record(_record(inv_id="bad#1", ai_run_id="bad", provider_fingerprint="Z" * 64))
    assert "bad#1" in caplog.text and "Z" * 64 not in caplog.text
    assert not _rows(env, "SELECT * FROM ai_role_invocations WHERE inv_id = 'bad#1'")
    with env["store"].transaction():
        with pytest.raises(RuntimeError):
            record_store.write_record(_record(inv_id="run3#1", ai_run_id="run3"))


def test_admission_refusal_record_and_orphan_sweep(env):
    from flowgate.ai_roles import record_store

    refused = _record(inv_id="refused#abc", ai_run_id=None, launch_no=None, result="admission_refused",
                      requested_model_state="not_parsed", requested_model_value=None,
                      requested_model_reason="not_launched", confirmed_model_state="unverifiable",
                      unverifiable_reason="no_result", ended_at=T, result_detail="start_refused:busy")
    assert record_store.write_record(refused)
    for n in (1, 2):
        assert record_store.write_record(_record(inv_id=f"run4#{n}", ai_run_id="run4", launch_no=n,
                                                 create_seq=4000 + n))
    swept = {"result": "call_failed", "result_detail": "orphaned", "ended_at": T,
             "confirmed_model_state": "unverifiable", "unverifiable_reason": "no_result"}
    assert record_store.finish_orphans("run4", swept) == 2
    assert [r["launch_no"] for r in record_store.by_run("run4")] == [1, 2]                      # R-10
    assert {r["result"] for r in record_store.by_run("run4")} == {"call_failed"}


def test_o1_o2_creation_order_wins_over_insert_order_and_same_millisecond(env):
    from flowgate.ai_roles import record_store

    a = _record(inv_id="runA#1", ai_run_id="runA", create_seq=100, purpose="write")
    b = _record(inv_id="runB#1", ai_run_id="runB", create_seq=101, purpose="review")
    assert a["unit_key_hash"] == b["unit_key_hash"]
    assert record_store.write_record(b) and record_store.write_record(a)                  # B stored first
    unit = a["unit_key_hash"]
    assert record_store.first(unit)["inv_id"] == "runA#1"                                  # O-1 (R-11)
    assert record_store.latest(unit, 102)["inv_id"] == "runB#1"                            # O-2 (R-12)
    assert record_store.latest(unit, 101)["inv_id"] == "runA#1"
    assert record_store.latest(unit, 102, purposes=["write"])["inv_id"] == "runA#1"
    assert record_store.latest(unit, 102, purposes=[]) is None
    assert record_store.latest(unit, 100) is None
    assert record_store.max_create_seq(unit) == 101 and record_store.max_create_seq(HEX) is None   # R-17
    assert [r["inv_id"] for r in record_store.for_node("g1", "n1", 10)] == ["runB#1", "runA#1"]   # R-14
    assert record_store.get("runA#1")["create_seq"] == 100                                  # R-18


def test_o3_the_root_lookup_is_a_covering_index_scan(env):
    plan = " ".join(r["detail"] for r in _rows(env,
        "EXPLAIN QUERY PLAN SELECT inv_id, create_seq FROM ai_role_invocations WHERE unit_key_hash = ? "
        "ORDER BY create_seq ASC, inv_id ASC LIMIT 1", [HEX]))
    assert "COVERING INDEX idx_ari_unit" in plan and "TEMP B-TREE" not in plan


def test_r13_r15_w15_the_run_row_links_to_the_ledger_execution(env):
    from flowgate.ai_roles import record_store
    from modules.flow_gate.db import ai_invoke_runs

    assert record_store.write_record(_record(graph_exec_id="exe_9"))
    assert [r["inv_id"] for r in record_store.by_exec("exe_9")] == ["run1#1"]                   # R-13
    base = {"group_id": "g1", "project_id": "p1", "doc_ref": "d", "mode": "single", "started_at": T,
            "finished_at": T, "created_at": T, "updated_at": T}
    ai_invoke_runs.upsert({**base, "run_id": "run1", "graph_exec_id": "exe_9",
                           "invocation_origin_kind": "card_assigned"})
    ai_invoke_runs.upsert({**base, "run_id": "plain"})
    rows = {r["run_id"]: r for r in _rows(env, "SELECT * FROM ai_invoke_runs")}
    assert (rows["run1"]["graph_exec_id"], rows["run1"]["invocation_origin_kind"]) == ("exe_9", "card_assigned")
    assert rows["plain"]["graph_exec_id"] is None and rows["plain"]["invocation_origin_kind"] is None
    assert record_store.runs_for_exec("exe_9") == ["run1"]                                      # R-15


def test_w15_before_165_the_run_row_is_written_without_the_links(old_env):
    from modules.flow_gate.db import ai_invoke_runs

    ai_invoke_runs.upsert({"run_id": "old", "group_id": "g1", "project_id": "p1", "doc_ref": "d", "mode": "single",
                           "started_at": T, "finished_at": T, "created_at": T, "updated_at": T,
                           "graph_exec_id": "exe_1", "invocation_origin_kind": "card_assigned"})
    assert _rows(old_env, "SELECT run_id FROM ai_invoke_runs") == [{"run_id": "old"}]


def test_i17_the_run_retention_sweep_and_parent_deletes_leave_the_records(env):
    from flowgate.ai_roles import record_store
    from modules.flow_gate.db import ai_invoke_runs

    assert record_store.write_record(_record())
    ai_invoke_runs.upsert({"run_id": "run1", "group_id": "g1", "project_id": "p1", "doc_ref": "d",
                           "mode": "single", "started_at": OLD, "finished_at": OLD, "created_at": OLD,
                           "updated_at": OLD})
    ai_invoke_runs.purge_older_than("2026-06-01T00:00:00+09:00")
    assert _rows(env, "SELECT * FROM ai_invoke_runs") == []
    conn = sqlite3.connect(env["path"])
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM groups WHERE group_id = 'g1'")
    conn.commit()
    conn.close()
    assert [r["inv_id"] for r in _rows(env, "SELECT inv_id FROM ai_role_invocations")] == ["run1#1"]


def test_provider_fingerprint_is_a_keyed_lower_hex_64_or_none_without_a_key(env, monkeypatch):
    from flowgate.ai_roles import record_store
    from modules.flow_gate.utils import api_key_crypto

    monkeypatch.setenv(api_key_crypto.KEY_ENV, base64.b64encode(b"k" * 32).decode("ascii"))
    first = record_store.provider_fingerprint('{"exec_type":"cli","kind":"claude"}')
    assert first and len(first) == 64 and first == first.lower()
    assert first == record_store.provider_fingerprint('{"exec_type":"cli","kind":"claude"}')
    assert first != record_store.provider_fingerprint('{"exec_type":"cli","kind":"codex"}')
    monkeypatch.setenv(api_key_crypto.KEY_ENV, base64.b64encode(b"j" * 32).decode("ascii"))
    assert first != record_store.provider_fingerprint('{"exec_type":"cli","kind":"claude"}')

    def no_key(create=False):
        raise api_key_crypto.ApiKeyCryptoError("no key")

    monkeypatch.setattr(api_key_crypto, "_get_current_key", no_key)
    assert record_store.provider_fingerprint("x") is None
    assert record_store.write_record(_record(provider_fingerprint=None))                      # E-21: still written
