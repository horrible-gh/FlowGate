"""flowgate.v02.0085 T#1 (D0007 §7.1, §7.2, §8-11) — the persistent data and the settings base of the confirm gate.

On a real SQLite DB with every migration: the gate decision / effect / archive checkpoint / cleanup tables and their
uniqueness and no-cascade rules; the operation job kind ``gate_publish``; migrations 158 / 159 run twice; the old
``merge_target`` branch names moving to ``legacy_merge_target`` once; the legacy ``gate_confirmed`` / ``gate_rejected``
events served with unknown Git / archive facts; the check card's settings (archive flag, ``merge_target`` as a remote,
null switches as false, no ``usage``); the settings version and the gate-only settings lock.

Nothing here decides a gate: the intake, the Git work and the screens are later cards. The harness (the real DB, the
Graph routes, the card settings routes) is the one of ``test_fg_card_settings``.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from test_fg_card_settings import (  # noqa: E402,F401  (fixtures are used by name)
    FA, FB, _seed, _sql, _start, db_path, decided, edge, env, get_settings, node, patch, put_draft, setting, shown,
    stored_rows, _h)

MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
DIALECTS = ("sqlite", "postgres", "mysql")
GATE_TABLES = ["gate_decisions", "gate_decision_effects", "gate_archive_checkpoints", "gate_cleanup_records"]
HUMAN = {"kind": "human", "user_id": "u_lee", "provider_id": None}
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture()
def gate_db(db_path, monkeypatch):
    """``db_path`` with the transaction adapter's live cursor exposed, as the card settings ``env`` does, so a
    conditional update can read its affected row count on the test DB."""
    from modules.flow_gate.db import _SqliteTxnAdapter

    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    return db_path


def _ts(n: int) -> str:
    return f"2026-01-01T00:00:{n:02d}.000+00:00"


def _row(request="req-1", attempt="att_1", node_key="gn_g", run="run_a", actor="u_kim", n=1, **extra):
    return {"group_id": FA, "run_id": run, "node_key": node_key, "attempt_id": attempt, "revision_no": 1,
            "policy_version": "human_confirm_v1", "actor_user_id": actor, "request_id": request,
            "request_fingerprint": "f" * 64, "decision": "confirm", "accepted_at": _ts(n), **extra}


def _build(path, before=None):
    """A SQLite DB with the migrations in the loader's order, up to (not including) the file starting with ``before``.
    Returns (connection, [(file, error)]) - an OperationalError of an old file is kept, as the test harness does."""
    conn = sqlite3.connect(str(path))
    failed = []
    for mig in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
        if before is not None and mig.name >= before:
            break
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError as exc:
            failed.append((mig.name, str(exc)))
    conn.execute("PRAGMA foreign_keys = OFF")      # a migration leaves it ON; these tests insert bare rows
    return conn, failed


def _script(name, dialect="sqlite"):
    return (MIGRATIONS / dialect / name).read_text(encoding="utf-8")


M158, M159 = "158_gate_decision_persistence.sql", "159_operation_job_gate_publish_kind.sql"
M161 = "161_operation_job_gate_cleanup_kind.sql"           # T#3 rework: the end cleanup job kind


# ── migrations ───────────────────────────────────────────────────────────────

def test_the_three_dialects_define_the_same_gate_objects():
    from modules.flow_gate.services.git import job_store

    tables, kinds, kinds159 = {}, {}, {}
    for dialect in DIALECTS:
        sql158, sql159, sql161 = _script(M158, dialect), _script(M159, dialect), _script(M161, dialect)
        tables[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql158)
        kinds159[dialect] = re.findall(r"'(\w+)'", re.search(r"kind IN \(([^)]*)\)", sql159).group(1))
        kinds[dialect] = re.findall(r"'(\w+)'", re.search(r"kind IN \(([^)]*)\)", sql161).group(1))
        for needle in ("valid_attempt_key", "run_id, node_key, actor_user_id, request_id", "legacy_merge_target",
                       "158_legacy_merge_target", "recovery_reset_required", "end_head_sha"):
            assert needle in sql158, (dialect, needle)
        # The slot of a decision that counts can never be NULL: a NULL passes a CHECK (UNKNOWN) and a unique index.
        assert ("(status <> 'canceled' AND valid_attempt_key IS NOT NULL AND valid_attempt_key = attempt_id)"
                in sql158), dialect
        blocks = re.split(r"CREATE TABLE IF NOT EXISTS ", sql158)[1:]
        for block in blocks:
            name = block.split(" ", 1)[0]
            # no delete cascades into the history; the settings version is a current-state table like 146's
            assert ("ON DELETE CASCADE" in block) == (name == "graph_node_settings_version"), (dialect, name)
    assert tables["sqlite"] == tables["postgres"] == tables["mysql"]
    assert set(GATE_TABLES) <= set(tables["sqlite"]) and "graph_node_settings_version" in tables["sqlite"]
    assert kinds["sqlite"] == kinds["postgres"] == kinds["mysql"] == list(job_store.KINDS)
    assert kinds159["sqlite"] == kinds159["postgres"] == kinds159["mysql"] == [
        k for k in job_store.KINDS if k != "gate_cleanup"]                # 161 only adds the end cleanup job


def test_the_gate_tables_keep_history_and_enforce_their_keys(tmp_path):
    conn, failed = _build(tmp_path / "gate.db")
    assert not [f for f in failed if f[0] in (M158, M159)], failed
    conn.execute("PRAGMA foreign_keys = ON")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(GATE_TABLES) | {"graph_node_settings_version", "gate_data_migrations"} <= names
    # No gate table points at a group, a run or an attempt; effects and checkpoints point at their decision, RESTRICT.
    for table in GATE_TABLES:
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert {(fk[2], fk[6]) for fk in fks} <= {("gate_decisions", "RESTRICT")}, (table, fks)

    def insert(decision_id, request, attempt, status="accepted", slot="same"):
        slot = attempt if slot == "same" else slot
        conn.execute(
            "INSERT INTO gate_decisions (decision_id, group_id, run_id, node_key, attempt_id, valid_attempt_key, "
            "revision_no, actor_user_id, request_id, request_fingerprint, decision, status, accepted_at, updated_at) "
            "VALUES (?, 'g', 'run_a', 'gn_g', ?, ?, 1, 'u', ?, ?, 'confirm', ?, 't', 't')",
            (decision_id, attempt, slot, request, "f" * 64, status))

    insert("d1", "r1", "att_1")
    with pytest.raises(sqlite3.IntegrityError):
        insert("d2", "r1", "att_2")                                  # the request key is unique
    with pytest.raises(sqlite3.IntegrityError):
        insert("d3", "r3", "att_1")                                  # one valid decision per attempt
    with pytest.raises(sqlite3.IntegrityError):
        insert("d4", "r4", "att_4", status="canceled")               # a canceled decision gives its slot up (NULL)
    insert("d5", "r5", "att_1", status="canceled", slot=None)        # ... and then does not hold the attempt
    insert("d6", "r6", "att_1", status="canceled", slot=None)        # any number of canceled ones
    # A decision that counts cannot leave the slot NULL: NULLs would pass the unique index, so two accepted
    # decisions could share one attempt.
    for status in ("accepted", "failed", "recovery_required", "succeeded", "rejected", "abandoned"):
        with pytest.raises(sqlite3.IntegrityError):
            insert("n_" + status, "rn_" + status, "att_1", status=status, slot=None)
    with pytest.raises(sqlite3.IntegrityError):
        insert("n_second", "rn_second", "att_1", status="accepted", slot=None)       # same attempt, other request key
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE gate_decisions SET valid_attempt_key = NULL WHERE decision_id = 'd1'")
    with pytest.raises(sqlite3.IntegrityError):                      # a canceled one coming back needs its slot
        conn.execute("UPDATE gate_decisions SET status = 'abandoned' WHERE decision_id = 'd5'")
    conn.execute("UPDATE gate_decisions SET status = 'abandoned' WHERE decision_id = 'd1'")   # abandoned keeps it
    assert conn.execute("SELECT valid_attempt_key FROM gate_decisions WHERE decision_id = 'd1'").fetchone() == ("att_1",)
    conn.execute("UPDATE gate_decisions SET status = 'accepted' WHERE decision_id = 'd1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE gate_decisions SET recovery_reset_required = 1 WHERE decision_id = 'd1'")  # abandoned only
    conn.execute("INSERT INTO gate_decision_effects (decision_id, phase, status, created_at, updated_at) "
                 "VALUES ('d1', 'merge', 'pending', 't', 't')")
    with pytest.raises(sqlite3.IntegrityError):                      # one row per (decision, phase)
        conn.execute("INSERT INTO gate_decision_effects (decision_id, phase, status, created_at, updated_at) "
                     "VALUES ('d1', 'merge', 'pending', 't', 't')")
    with pytest.raises(sqlite3.IntegrityError):                      # a decision with history cannot be deleted
        conn.execute("DELETE FROM gate_decisions WHERE decision_id = 'd1'")
    # A deleted Flow, run or attempt leaves the decision: nothing refers to them.
    conn.execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                 "VALUES ('p', 'p', 1, 't', 't')")
    conn.execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                 "VALUES ('g', 'p', 'm', 't', 'OPEN', 't', 't')")
    conn.execute("DELETE FROM groups WHERE group_id = 'g'")
    assert conn.execute("SELECT COUNT(*) FROM gate_decisions WHERE group_id = 'g'").fetchone()[0] == 3


def test_a_rerun_of_the_migrations_changes_nothing_and_keeps_the_job_table_whole(tmp_path):
    from modules.flow_gate.services.git import job_store

    conn, failed = _build(tmp_path / "pre159.db", before="159_")
    assert not [f for f in failed if f[0] == M158], failed
    columns = lambda: conn.execute("PRAGMA table_info(operation_job)").fetchall()   # noqa: E731
    indexes = lambda: sorted(r[0] for r in conn.execute(                              # noqa: E731
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'operation_job' AND sql IS NOT NULL"))
    old_columns, old_indexes = columns(), indexes()
    assert len(old_columns) == 43 and "idx_job_group" in old_indexes
    stamp = "2026-01-01T00:00:00.000"
    conn.execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                 "VALUES ('p', 'p', 1, 't', 't')")
    conn.execute("INSERT INTO operation_job (job_id, kind, project_id, request_key, request_key_hash, "
                 "request_fingerprint, status, phase, available_at, created_at, updated_at) "
                 "VALUES ('opj_old', 'archive_preserve', 'p', 'k', ?, ?, 'pending', 'start', ?, ?, ?)",
                 ("1" * 64, "2" * 64, stamp, stamp, stamp))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):                      # before 159 the kind is not allowed
        conn.execute("INSERT INTO operation_job (job_id, kind, project_id, request_key, request_key_hash, "
                     "request_fingerprint, status, available_at, created_at, updated_at) "
                     "VALUES ('opj_g0', 'gate_publish', 'p', 'k0', ?, ?, 'pending', ?, ?, ?)",
                     ("3" * 64, "4" * 64, stamp, stamp, stamp))
    rows = lambda: [tuple(r) for r in conn.execute("SELECT * FROM operation_job ORDER BY job_id")]  # noqa: E731
    before_rows = rows()
    conn.execute("INSERT INTO gate_data_migrations (name, applied_at) VALUES ('probe', 't')")
    conn.commit()
    for _ in range(2):                                               # 159 twice (and 161), then 158 again
        conn.executescript(_script(M159))
        conn.executescript(_script(M161))
    conn.executescript(_script(M158))
    conn.execute("PRAGMA foreign_keys = OFF")
    assert rows() == before_rows and columns() == old_columns        # every row and column kept
    assert indexes() == old_indexes
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'operation_job_new'").fetchall() == []
    # The tables that point at operation_job still do: the rebuild did not rename them to a throwaway name.
    pointing = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql LIKE '%REFERENCES operation_job%'")]
    assert pointing and not any("operation_job_new" in sql for sql in pointing)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for kind in job_store.KINDS:                                     # the CHECK is the code's list, nothing less
        conn.execute("INSERT INTO operation_job (job_id, kind, project_id, request_key, request_key_hash, "
                     "request_fingerprint, status, available_at, created_at, updated_at) "
                     "VALUES (?, ?, 'p', ?, ?, ?, 'pending', ?, ?, ?)",
                     (f"opj_{kind}", kind, kind, kind.ljust(64, "x"), "5" * 64, stamp, stamp, stamp))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO operation_job (job_id, kind, project_id, request_key, request_key_hash, "
                     "request_fingerprint, status, available_at, created_at, updated_at) "
                     "VALUES ('opj_bad', 'not_a_kind', 'p', 'x', ?, ?, 'pending', ?, ?, ?)",
                     ("6" * 64, "7" * 64, stamp, stamp, stamp))
    # 158 again keeps what is in its tables
    assert conn.execute("SELECT COUNT(*) FROM gate_data_migrations WHERE name = 'probe'").fetchone()[0] == 1


def test_an_old_merge_target_branch_moves_to_legacy_once(tmp_path):
    conn, failed = _build(tmp_path / "pre158.db", before="158_")
    assert failed == [] or all(f[0] < "158_" for f in failed)
    stored = [(FA, "gn_a", "merge_target", '"release/v0.2"'), (FA, "gn_b", "merge_target", '"origin"'),
              (FA, "gn_a", "usage", '"end"'), (FA, "gn_a", "push", "true"), (FA, "gn_b", "merge_mode", '"merge"'),
              (FB, "gn_c", "merge_target", '"main"'), (FB, "gn_c", "legacy_merge_target", '"older"')]
    conn.executemany("INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                     "VALUES (?, ?, ?, ?, 't')", stored)
    conn.commit()
    snapshot = lambda: {(g, n, f): v for g, n, f, v in conn.execute(               # noqa: E731
        "SELECT group_id, node_key, field, value_json FROM graph_node_settings")}
    conn.executescript(_script(M158))
    moved = snapshot()
    assert moved == {(FA, "gn_a", "legacy_merge_target"): '"release/v0.2"', (FA, "gn_b", "legacy_merge_target"): '"origin"',
                     (FA, "gn_a", "usage"): '"end"', (FA, "gn_a", "push"): "true", (FA, "gn_b", "merge_mode"): '"merge"',
                     (FB, "gn_c", "legacy_merge_target"): '"older"'}        # no active merge_target; usage kept; no overwrite
    assert conn.execute("SELECT COUNT(*) FROM gate_data_migrations WHERE name = '158_legacy_merge_target'").fetchone()[0] == 1
    # After the migration ``origin`` is the new meaning (a remote): running the script again leaves it alone.
    conn.execute("INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                 "VALUES (?, 'gn_d', 'merge_target', '\"origin\"', 't')", (FA,))
    conn.commit()
    conn.executescript(_script(M158))
    assert snapshot() == {**moved, (FA, "gn_d", "merge_target"): '"origin"'}
    assert conn.execute("SELECT COUNT(*) FROM gate_data_migrations").fetchone()[0] == 1


# ── the store ────────────────────────────────────────────────────────────────

def test_a_decision_is_unique_by_key_and_attempt_and_moves_by_version(gate_db):
    from flowgate.gate_decisions import repository as repo

    _seed(gate_db)
    first = repo.insert_decision(_row())
    assert (first["status"], first["decision_version"], first["valid_attempt_key"]) == ("accepted", 1, "att_1")
    with pytest.raises(repo.DecisionConflict) as same:               # the same request key, another attempt
        repo.insert_decision(_row(attempt="att_2", n=2))
    assert (same.value.reason, same.value.existing["decision_id"]) == ("request_key", first["decision_id"])
    with pytest.raises(repo.DecisionConflict) as other:              # another key, the same attempt
        repo.insert_decision(_row(request="req-2", n=2))
    assert (other.value.reason, other.value.existing["decision_id"]) == ("attempt", first["decision_id"])
    with pytest.raises(ValueError):
        repo.insert_decision(_row(request="req-9", attempt="att_9", status="failed"))   # born accepted / finished only
    assert repo.reservation("run_a", "gn_g")["decision_id"] == first["decision_id"]
    # A move names the version it read; a stale one changes nothing.
    assert repo.move(first["decision_id"], 1, repo.FAILED, last_error_code="merge_conflict") is True
    assert repo.move(first["decision_id"], 1, repo.FAILED) is False
    assert repo.get(first["decision_id"])["decision_version"] == 2
    assert repo.move(first["decision_id"], 2, repo.SUCCEEDED) is True                 # failed -> succeeded is allowed
    done = repo.get(first["decision_id"])
    assert (done["status"], done["decision_version"], bool(done["completed_at"])) == ("succeeded", 3, True)
    assert repo.move(first["decision_id"], 3, repo.FAILED) is False                   # but not back
    assert repo.reservation("run_a", "gn_g") is None                                  # a finished one holds nothing


def test_cancel_frees_the_attempt_and_abandon_does_not(gate_db):
    from flowgate.gate_decisions import repository as repo

    _seed(gate_db)
    first = repo.insert_decision(_row())
    assert repo.move(first["decision_id"], 1, repo.CANCELED) is True
    canceled = repo.get(first["decision_id"])
    assert (canceled["status"], canceled["valid_attempt_key"], canceled["decision_version"]) == ("canceled", None, 2)
    assert canceled["completed_at"] and repo.valid_for_attempt("att_1") is None and repo.reservation("run_a", "gn_g") is None
    assert repo.by_request("run_a", "gn_g", "u_kim", "req-1")["decision_id"] == first["decision_id"]   # its key replays it
    assert repo.move(first["decision_id"], 2, repo.ACCEPTED) is False                # a final status never moves again
    second = repo.insert_decision(_row(request="req-2", n=2))                         # the same attempt, a new key
    assert repo.valid_for_attempt("att_1")["decision_id"] == second["decision_id"]
    sid = second["decision_id"]
    assert repo.move(sid, 1, repo.RECOVERY_REQUIRED, last_error_code="push_unknown") is True
    assert repo.reservation("run_a", "gn_g")["status"] == repo.RECOVERY_REQUIRED       # still holds the gate
    assert repo.move(sid, 2, repo.ABANDONED, close_kind=repo.CLOSE_ABANDON, closed_by="u_lee",
                     close_reason="remote deleted", closed_at=_ts(9), recovery_reset_required=True) is True
    abandoned = repo.get(sid)
    assert (abandoned["status"], abandoned["close_kind"], abandoned["closed_by"], abandoned["actor_user_id"]) == (
        "abandoned", "abandon", "u_lee", "u_kim")                                      # both actors are kept
    assert abandoned["recovery_reset_required"] is True and abandoned["valid_attempt_key"] == "att_1"
    with pytest.raises(repo.DecisionConflict) as again:                                # abandoned still holds the attempt
        repo.insert_decision(_row(request="req-3", n=3))
    assert again.value.reason == "attempt" and repo.reservation("run_a", "gn_g") is None
    assert repo.reset_required(FA, "gn_g")["decision_id"] == sid
    assert repo.move(sid, 3, None, last_error_code="x") is False                       # a field of a finished one
    assert repo.move(sid, 3, None, recovery_reset_required=False, time_machine_ref="tm_1") is True   # the round split
    assert repo.reset_required(FA, "gn_g") is None and repo.get(sid)["time_machine_ref"] == "tm_1"
    reject = repo.insert_decision(_row(request="req-r", attempt="att_r", node_key="gn_r", decision="reject",
                                       status="rejected", n=4))
    assert reject["status"] == "rejected" and reject["completed_at"] == _ts(4)


def test_effects_checkpoints_and_cleanups_are_kept_per_decision(gate_db):
    from flowgate.gate_decisions import repository as repo

    _seed(gate_db)
    d1 = repo.insert_decision(_row(n=1))["decision_id"]
    d2 = repo.insert_decision(_row(request="req-2", attempt="att_2", n=2))["decision_id"]
    repo.record_effect(d1, "merge", status="pending", target_ref="refs/heads/main", sha_before=SHA_A)
    again = repo.record_effect(d1, "merge", status="succeeded", sha_after=SHA_B, evidence={"merge": "ff"},
                               new_attempt=True)
    assert len(repo.effects_of(d1)) == 1                                   # a retry updates the phase's one row
    assert (again["attempt_count"], again["evidence_version"], again["sha_before"], again["sha_after"]) == (
        2, 2, SHA_A, SHA_B)
    assert again["evidence"] == {"merge": "ff"}
    unknown = repo.record_effect(d1, "push", status="unknown", recovery_state="required",
                                 recovery_ref="refs/flowgate/recovery/" + d1)
    assert (unknown["status"], unknown["recovery_state"], unknown["recovery_ref"]) == (
        "unknown", "required", "refs/flowgate/recovery/" + d1)
    with pytest.raises(ValueError):
        repo.record_effect(d1, "merge", not_a_column="x")
    c1 = repo.insert_checkpoint({"decision_id": d1, "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                                 "attempt_id": "att_1", "source_sha": SHA_A, "applied_sha": SHA_B,
                                 "ref_name": "refs/flowgate/archive/" + d1, "restore_info": {"branch": "g"}})
    c2 = repo.insert_checkpoint({"decision_id": d2, "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                                 "attempt_id": "att_2", "source_sha": SHA_C, "ref_name": "refs/flowgate/archive/" + d2})
    assert [c["archive_id"] for c in repo.checkpoints_of_group(FA)] == sorted(
        [c1["archive_id"], c2["archive_id"]], key=lambda a: repo.checkpoint(a)["preserved_at"] + a)
    assert repo.checkpoint(c1["archive_id"])["source_sha"] == SHA_A and c1["restore_info"] == {"branch": "g"}
    with pytest.raises(Exception):                                         # one checkpoint per decision, one per ref
        repo.insert_checkpoint({"decision_id": d1, "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                                "attempt_id": "att_1", "source_sha": SHA_C, "ref_name": "refs/other"})
    with pytest.raises(Exception):
        repo.insert_checkpoint({"decision_id": d2, "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                                "attempt_id": "att_2", "source_sha": SHA_C, "ref_name": "refs/flowgate/archive/" + d1})
    assert repo.mark_restored(c1["archive_id"]) is True and repo.checkpoint(c1["archive_id"])["last_restored_at"]
    assert repo.checkpoint(c1["archive_id"]) is not None                   # a restore does not consume it
    rec = repo.insert_cleanup({"group_id": FA, "run_id": "run_a", "branch_name": "flowgate/g", "end_head_sha": SHA_C,
                               "basis_kind": "archive_checkpoint", "basis_ref": c1["ref_name"], "basis_sha": SHA_B,
                               "reachable": False, "keep_requested": False, "result": "retained_uncovered_head",
                               "result_reason": "head_not_covered", "evidence": {"observed_head": SHA_C}})
    assert (rec["end_head_sha"], rec["reachable"], rec["keep_requested"], rec["result"]) == (
        SHA_C, 0, 0, "retained_uncovered_head") and rec["evidence"] == {"observed_head": SHA_C}
    assert [c["cleanup_id"] for c in repo.cleanups_of_group(FA)] == [rec["cleanup_id"]]
    with pytest.raises(Exception):
        repo.insert_cleanup({"group_id": FA, "end_head_sha": SHA_C, "result": "deleted_for_sure"})


def test_legacy_gate_events_are_served_with_unknown_git_and_archive_facts(gate_db):
    from flowgate.gate_decisions import history, repository as repo

    _seed(gate_db)
    for run_id in ("run_a", "run_b"):
        _sql(gate_db, "INSERT INTO graph_runs (run_id, group_id, revision_no, status, started_at, policy_source, "
                      "policy_set_id, policy_set_version, policy_digest, updated_at) "
                      "VALUES (?, ?, 1, 'idle', ?, 'flow', 'ps', '1', ?, 't')", (run_id, FA, _ts(0), "0" * 64))
    for run_id, seq, cause, actor, reason, n in (("run_a", 4, "gate_confirmed", "u_lee", "looks good", 1),
                                                 ("run_b", 4, "gate_rejected", "u_kim", None, 9),
                                                 ("run_b", 2, "result_submitted", "u_kim", None, 3)):
        _sql(gate_db, "INSERT INTO graph_run_events (run_id, event_seq, event_type, node_key, from_status, to_status, "
                      "cause, actor_user_id, reason, payload_json, created_at) "
                      "VALUES (?, ?, 'graph_node_state_changed', 'gn_g', 'open', 'done', ?, ?, ?, '{}', ?)",
             (run_id, seq, cause, actor, reason, _ts(n)))
    _sql(gate_db, "INSERT INTO graph_run_events (run_id, event_seq, event_type, node_key, from_status, to_status, "
                  "cause, payload_json, created_at) VALUES ('run_a', 7, 'graph_node_state_changed', 'gn_other', "
                  "'open', 'done', 'gate_confirmed', '{}', ?)", (_ts(2),))
    [first, last] = history.legacy_events(FA, "gn_g")                       # the other node and other causes are not here
    assert (first["decision_id"], first["decision"], first["result"], first["actor_user_id"], first["comment"],
            first["event_seq"], first["decided_at"]) == ("legacy:run_a:4", "confirm", "confirmed", "u_lee",
                                                         "looks good", 4, _ts(1))
    assert (last["decision"], last["result"], last["comment"]) == ("reject", "rejected", None)
    for entry in (first, last):                                             # unknown, never a made-up success
        assert entry["source"] == "legacy_event" and entry["effects"] is None and entry["archived"] is False
        assert entry["frozen_sha"] is None and entry["settings"] is None and entry["target_sha"] is None
        assert {"effects", "frozen_sha", "settings", "target_branch"} <= set(entry["unknown"])
    # The new decisions come in the same ordered history; a cursor page stays put when newer ones are added.
    mid = repo.insert_decision(_row(n=5))
    assert repo.move(mid["decision_id"], 1, repo.SUCCEEDED) is True
    page = history.history(FA, "gn_g", limit=2)
    assert [e["decision_id"] for e in page["items"]] == ["legacy:run_a:4", mid["decision_id"]]
    assert page["latest"]["decision_id"] == "legacy:run_b:4" and page["next_cursor"]
    newer = repo.insert_decision(_row(request="req-n", attempt="att_n", n=7))
    rest = history.history(FA, "gn_g", cursor=page["next_cursor"], limit=2)
    assert [e["decision_id"] for e in rest["items"]] == [newer["decision_id"], "legacy:run_b:4"]
    assert rest["next_cursor"] is None
    assert history.history(FA, "gn_none")["items"] == [] and history.history(FA, "gn_none")["latest"] is None
    with pytest.raises(ValueError):
        history.history(FA, "gn_g", cursor="not-a-cursor")
    # The archive chip belongs to a finished confirm with its own checkpoint, and to nothing else.
    repo.insert_checkpoint({"decision_id": mid["decision_id"], "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                            "attempt_id": "att_1", "source_sha": SHA_A, "ref_name": "refs/flowgate/archive/m"})
    repo.insert_checkpoint({"decision_id": newer["decision_id"], "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                            "attempt_id": "att_n", "source_sha": SHA_B, "ref_name": "refs/flowgate/archive/n"})
    chips = {e["decision_id"]: e["archived"] for e in history.history(FA, "gn_g")["items"]}
    assert chips == {"legacy:run_a:4": False, mid["decision_id"]: True, newer["decision_id"]: False,
                     "legacy:run_b:4": False}


def test_the_gate_publish_job_kind_is_keyed_by_the_decision(gate_db):
    from modules.flow_gate.services.git import job_runner, job_store

    _seed(gate_db)
    assert "gate_publish" in job_store.KINDS and "gate_publish" not in job_store.FREEZE_KINDS
    request = {"decision_id": "gdc_1", "group_id": FA, "run_id": "run_a", "node_key": "gn_g"}
    assert job_store.derive_request_key("gate_publish", "flowgate", {**request, "client_request_key": "other"}) == "gpb:gdc_1"
    with pytest.raises(job_store.JobProgramError):
        job_store.derive_request_key("gate_publish", "flowgate", {"group_id": FA})
    made = job_store.create_or_get_job("gate_publish", "flowgate", request, group_id=FA, target_key="gn_g",
                                       requested_by="u_kim", wake=False)
    assert made.outcome == job_store.CREATED and (made.job["kind"], made.job["status"]) == ("gate_publish", "pending")
    same = job_store.create_or_get_job("gate_publish", "flowgate", request, group_id=FA, target_key="gn_g", wake=False)
    assert same.outcome == job_store.EXISTING and same.job["job_id"] == made.job["job_id"]
    other = job_store.create_or_get_job("gate_publish", "flowgate", {**request, "node_key": "gn_x"}, group_id=FA,
                                        wake=False)
    assert other.outcome == job_store.REJECTED and other.code == "request_key_conflict"
    # flowgate.v02.0085 T#3: the gate card's executor runs it now (installed with the gate rule).
    from flowgate.gate_decisions import publish
    publish.install()
    with job_runner._reg_guard:
        assert job_runner._executors.get("gate_publish") is publish.run
    assert job_store.GATE_CANCEL_REASONS == ("no_effect_canceled", "recovery_abandoned")
    for status in ("succeeded", "cancelled"):                           # a finished job is not resumed
        _sql(gate_db, "UPDATE operation_job SET status = ? WHERE job_id = ?", (status, made.job["job_id"]))
        assert job_store.mode_for(job_store.get_job(made.job["job_id"])) is None


# ── the check card's settings ────────────────────────────────────────────────

def _gate(env, flow=FB):
    return put_draft(env, flow, 0, [node("tmp_g", None, "gate")])["key_map"]["tmp_g"]


def test_the_check_card_settings_are_archive_a_remote_and_false_switches(env):
    g = _gate(env)
    body = get_settings(env, FB, g)
    # flowgate.v02.0098 T#1: the local merge branch (null: the work base) sits next to the remote
    assert list(body["fields"]) == ["archive", "merge_mode", "merge_target", "merge_target_branch", "push",
                                    "keep_branch", "hold"]
    assert shown(body) == {"archive": False, "merge_mode": None, "merge_target": None, "merge_target_branch": None,
                           "push": False, "keep_branch": False, "hold": False}
    assert (body["card_kind"], body["locked"], body["lock_reason"], body["settings_version"], body["legacy"]) == (
        "check", False, None, 0, {})
    assert body["fields"]["merge_target"]["choices"] == ["origin"]
    refused = setting(env, FB, g, "usage", "end", expect=422)                 # usage is no setting any more
    assert refused["error"]["params"]["errors"][0]["reason"] == "not_a_setting_of_this_card"
    assert stored_rows(env, FB) == {}
    for bad in ("main", "release/v0.2", "Origin", 1, True):                   # a remote, never a branch name
        setting(env, FB, g, "merge_target", bad, expect=422)
    assert shown(setting(env, FB, g, "merge_target", "origin"))["merge_target"] == "origin"
    assert shown(setting(env, FB, g, "merge_target", None))["merge_target"] is None
    for name in ("archive", "push", "keep_branch"):
        setting(env, FB, g, name, "yes", expect=422)
        assert shown(setting(env, FB, g, name, True))[name] is True
        back = setting(env, FB, g, name, None)                                # null: stored nothing, false
        assert (back["fields"][name]["value"], back["fields"][name]["stored"]) == (False, False)
    # flowgate.v02.0098 T#1 (D0004 §3.5): push true saved the remote with it; push null left the remote as history
    assert shown(get_settings(env, FB, g))["merge_target"] == "origin"
    assert shown(setting(env, FB, g, "merge_mode", "hold"))["merge_mode"] == "hold"
    # A stored null reads as false, and a branch name an old row still holds is not shown as the remote.
    for field, value in (("push", "null"), ("merge_target", '"release/v0.2"')):
        _sql(env["db"], "DELETE FROM graph_node_settings WHERE group_id = ? AND node_key = ? AND field = ?",
             (FB, g, field))
        _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                        "VALUES (?, ?, ?, ?, 't')", (FB, g, field, value))
    odd = get_settings(env, FB, g)
    assert (odd["fields"]["push"]["value"], odd["fields"]["merge_target"]["value"]) == (False, None)


def test_old_usage_and_merge_target_values_are_kept_and_shown_as_history(env):
    g = _gate(env)
    for field, value in (("usage", '"end"'), ("legacy_merge_target", '"release/v0.2"')):
        _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                        "VALUES (?, ?, ?, ?, 't')", (FB, g, field, value))
    body = get_settings(env, FB, g)
    assert "usage" not in body["fields"] and "legacy_merge_target" not in body["fields"]
    assert body["legacy"] == {"usage": "end", "legacy_merge_target": "release/v0.2"}
    after = setting(env, FB, g, "archive", True)                              # other writes leave them alone
    assert after["legacy"] == body["legacy"]
    assert {k: v for (n, k), v in stored_rows(env, FB, g).items() if k in ("usage", "legacy_merge_target")} == {
        "usage": '"end"', "legacy_merge_target": '"release/v0.2"'}
    setting(env, FB, g, "legacy_merge_target", None, expect=422)               # not writable


def test_the_settings_version_moves_with_each_change_and_guards_a_stale_screen(env):
    g = _gate(env)
    assert get_settings(env, FB, g)["settings_version"] == 0
    versions = [setting(env, FB, g, "push", True)["settings_version"],        # a change
                setting(env, FB, g, "push", True)["settings_version"],        # the same value: no change
                setting(env, FB, g, "archive", True)["settings_version"],
                setting(env, FB, g, "archive", None)["settings_version"],     # back to the default is a change
                setting(env, FB, g, "archive", None)["settings_version"],     # nothing stored: no change
                setting(env, FB, g, "hold", True)["settings_version"]]        # Hold is the Graph's, not the version's
    assert versions == [1, 1, 2, 3, 3, 3]
    ok = patch(env, FB, g, {"field": "keep_branch", "value": True, "expected_settings_version": 3})
    assert ok["settings_version"] == 4
    stale = patch(env, FB, g, {"field": "keep_branch", "value": False, "expected_settings_version": 3}, expect=409)
    assert stale["error"]["code"] == "gate_context_changed"
    assert stale["error"]["params"]["reason"] == "settings_version" and stale["error"]["params"]["settings_version"] == 4
    assert stored_rows(env, FB, g)[(g, "keep_branch")] == "true"               # nothing was written
    assert get_settings(env, FB, g)["settings_version"] == 4
    for bad in ("3", True, -1, 1.5, None):
        res = patch(env, FB, g, {"field": "push", "value": False, "expected_settings_version": bad}, expect=422)
        assert res["error"]["params"]["errors"] == [{"field": "expected_settings_version", "reason": "not_a_version"}]
    # Every card has the version; a card that is no gate is checked the same way.
    t = put_draft(env, FA, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    assert setting(env, FA, t, "restart", 1)["settings_version"] == 1
    assert patch(env, FA, t, {"field": "restart", "value": 2, "expected_settings_version": 0}, expect=409)[
        "error"]["code"] == "gate_context_changed"


def _locked(env, g, reason):
    body = get_settings(env, FA, g)
    assert (body["locked"], body["lock_reason"]) == (True, reason)
    version = body["settings_version"]
    refused = setting(env, FA, g, "archive", True, expect=409)
    assert refused["error"]["code"] == "gate_settings_locked" and refused["error"]["params"]["reason"] == reason
    assert stored_rows(env, FA, g).get((g, "archive")) is None and get_settings(env, FA, g)["settings_version"] == version


def test_a_running_flows_gate_settings_change_only_while_nothing_holds_the_gate(env, decided, monkeypatch):
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.graph_workflow.domain import policies

    # The product's gate_node slot is a later card's: like test_graph_gate_0045, the validator is told it is decided.
    monkeypatch.setitem(policies._registered, "gate_node", object())

    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN), node("tmp_g", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_t"), edge("tmp_e2", "tmp_t", "tmp_g")])
    t, g = first["key_map"]["tmp_t"], first["key_map"]["tmp_g"]
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    assert setting(env, FA, g, "merge_mode", "commit")["locked"] is False         # no run yet
    _start(env)
    run_id = _sql(env["db"], "SELECT run_id FROM graph_runs WHERE group_id = ?", (FA,))[0]["run_id"]
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (g,))[0]["status"] == "pending"
    # The Graph's lock still holds the other cards (and the definition); the pending gate is the exception.
    assert setting(env, FA, t, "restart", 1, expect=409)["error"]["code"] != "gate_settings_locked"
    body = setting(env, FA, g, "push", True)
    assert (body["locked"], body["lock_reason"], body["settings_version"]) == (False, None, 2)
    # A reserved decision holds the gate: the settings are locked until it is canceled.
    held = repo.insert_decision(_row(run=run_id, node_key=g, request="req-1", attempt="att_1"))
    _locked(env, g, "decision_reserved")
    assert repo.move(held["decision_id"], 1, repo.FAILED, last_error_code="push_failed") is True
    _locked(env, g, "decision_reserved")                                          # a failed one holds it too
    assert repo.move(held["decision_id"], 2, repo.CANCELED) is True
    assert setting(env, FA, g, "archive", True)["fields"]["archive"]["value"] is True   # the same attempt, open again
    setting(env, FA, g, "archive", None)
    # An abandoned recovery asks for a round split first; the Time Machine clearing the flag opens the settings.
    again = repo.insert_decision(_row(run=run_id, node_key=g, request="req-2", attempt="att_1", n=2))
    assert repo.move(again["decision_id"], 1, repo.RECOVERY_REQUIRED) is True
    _locked(env, g, "decision_reserved")
    assert repo.move(again["decision_id"], 2, repo.ABANDONED, close_kind=repo.CLOSE_ABANDON, closed_by="u_lee",
                     close_reason="gone", closed_at=_ts(5), recovery_reset_required=True) is True
    _locked(env, g, "recovery_reset_required")
    assert repo.move(again["decision_id"], 3, None, recovery_reset_required=False) is True
    assert setting(env, FA, g, "archive", True)["locked"] is False


@pytest.mark.parametrize("run_status, node_status, expected", [
    ("running", "pending", None), ("running", "open", None), ("idle", "open", None), ("idle", "pending", None),
    ("paused", "open", "run_not_editable"), ("stopping", "pending", "run_not_editable"),
    ("running", "done", "gate_decided"), ("idle", "rejected", "gate_decided"),
])
def test_the_gate_lock_follows_the_run_and_the_gate_state(db_path, monkeypatch, run_status, node_status, expected):
    from flowgate.gate_decisions import settings_lock

    _seed(db_path)
    monkeypatch.setattr(settings_lock.run_store, "current_run", lambda flow: {"run_id": "run_a", "status": run_status})
    monkeypatch.setattr(settings_lock.run_store, "node_runs", lambda run: [{"node_key": "gn_g", "status": node_status}])
    monkeypatch.setattr("flowgate.graph_time_machine.operation.busy", lambda flow: False)
    assert settings_lock.lock_reason(FA, "gn_g") == expected
    assert settings_lock.locked(FA, "gn_g") is (expected is not None)


def test_the_gate_lock_also_holds_for_a_time_machine_a_conflict_and_a_flow_without_a_run(db_path, monkeypatch):
    from flowgate.gate_decisions import settings_lock

    _seed(db_path)
    monkeypatch.setattr(settings_lock.run_store, "current_run", lambda flow: None)
    state = {"tm": False, "conflict": False}
    monkeypatch.setattr("flowgate.graph_time_machine.operation.busy", lambda flow: state["tm"])
    monkeypatch.setattr(settings_lock, "_merge_conflict_open", lambda flow: state["conflict"])
    assert settings_lock.lock_reason(FA, "gn_g") is None                       # no run: editable as before
    state["tm"] = True
    assert settings_lock.lock_reason(FA, "gn_g") == "time_machine"
    state.update(tm=False, conflict=True)
    assert settings_lock.lock_reason(FA, "gn_g") == "merge_conflict"
    with pytest.raises(Exception) as locked:
        settings_lock.require_unlocked(FA, "gn_g")
    assert (locked.value.status, locked.value.code, locked.value.params["reason"]) == (
        409, "gate_settings_locked", "merge_conflict")


# ── an open gate takes settings changes through the real GET / PATCH ─────────

def _gate_in_a_run(env, monkeypatch):
    """A confirmed Flow (R -> T -> gate) with a started run: (task node, gate node, run id)."""
    from modules.flow_gate.graph_workflow.domain import policies

    monkeypatch.setitem(policies._registered, "gate_node", object())      # the product's slot is a later card's
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN), node("tmp_g", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_t"), edge("tmp_e2", "tmp_t", "tmp_g")])
    t, g = first["key_map"]["tmp_t"], first["key_map"]["tmp_g"]
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    _start(env)
    return t, g, _sql(env["db"], "SELECT run_id FROM graph_runs WHERE group_id = ?", (FA,))[0]["run_id"]


@pytest.mark.parametrize("run_status", ["running", "idle"])
def test_an_open_gate_takes_settings_changes_and_the_other_cards_stay_locked(env, decided, monkeypatch, run_status):
    from flowgate.card_settings import legacy_adapter
    from flowgate.gate_decisions import repository as repo

    t, g, run_id = _gate_in_a_run(env, monkeypatch)
    db = env["db"]
    _sql(db, "UPDATE graph_runs SET status = ? WHERE run_id = ?", (run_status, run_id))
    for key in (g, t):
        _sql(db, "UPDATE graph_node_runs SET status = 'open' WHERE run_id = ? AND node_key = ?", (run_id, key))
    # The Graph's own check says the gate is running (an open node of the active run); the gate's lock must win.
    assert legacy_adapter.node_running(FA, g) is True
    body = get_settings(env, FA, g)
    assert (body["locked"], body["lock_reason"], body["settings_version"]) == (False, None, 0)
    changed = setting(env, FA, g, "archive", True)                         # PATCH succeeds: no node_settings_locked
    assert (changed["locked"], changed["lock_reason"], changed["settings_version"]) == (False, None, 1)
    assert changed["fields"]["archive"]["value"] is True and stored_rows(env, FA, g)[(g, "archive")] == "true"
    guarded = patch(env, FA, g, {"field": "push", "value": True, "expected_settings_version": 1})
    assert guarded["settings_version"] == 2 and guarded["locked"] is False
    # Another card of the same open run keeps the Graph's lock, both in the answer and in the write.
    assert legacy_adapter.node_running(FA, t) is True
    assert get_settings(env, FA, t)["locked"] is True
    refused = setting(env, FA, t, "restart", 1, expect=409)
    assert refused["error"]["code"] != "gate_settings_locked" and (t, "restart") not in stored_rows(env, FA, t)
    assert setting(env, FA, g, "archive", None)["settings_version"] == 3
    assert setting(env, FA, g, "push", None)["settings_version"] == 4
    # A reserved decision still locks the open gate, with the gate's reason.
    held = repo.insert_decision(_row(run=run_id, node_key=g, request="req-open", attempt="att_open"))
    _locked(env, g, "decision_reserved")
    assert repo.move(held["decision_id"], 1, repo.CANCELED) is True
    assert setting(env, FA, g, "archive", True)["settings_version"] == 5     # open again after the cancel


# ── a decision's own run event is not a legacy entry ─────────────────────────

def _run_event(db, run_id, seq, cause, n, payload="{}", node_key="gn_g", actor="u_lee", reason=None):
    _sql(db, "INSERT INTO graph_run_events (run_id, event_seq, event_type, node_key, from_status, to_status, cause, "
             "actor_user_id, reason, payload_json, created_at) "
             "VALUES (?, ?, 'graph_node_state_changed', ?, 'open', 'done', ?, ?, ?, ?, ?)",
         (run_id, seq, node_key, cause, actor, reason, payload, _ts(n)))


def test_a_new_decisions_linked_event_is_not_shown_again_as_a_legacy_entry(gate_db):
    import json

    from flowgate.gate_decisions import history, repository as repo

    _seed(gate_db)
    _sql(gate_db, "INSERT INTO graph_runs (run_id, group_id, revision_no, status, started_at, policy_source, "
                  "policy_set_id, policy_set_version, policy_digest, updated_at) "
                  "VALUES ('run_a', ?, 1, 'idle', ?, 'flow', 'ps', '1', ?, 't')", (FA, _ts(0), "0" * 64))
    # Real earlier events of the same run and node: they stay, with or without a payload that says nothing.
    _run_event(gate_db, "run_a", 3, "gate_rejected", 1, reason="not yet")
    _run_event(gate_db, "run_a", 5, "gate_confirmed", 3, payload='{"note": "old"}')
    _run_event(gate_db, "run_a", 6, "gate_confirmed", 4, payload="not json")
    assert history.decision_link("gdc_1") == {"gate_decision_id": "gdc_1"}
    assert [history.linked_decision_id(p) for p in (None, "", "{}", "[]", "not json", '{"gate_decision_id": ""}',
                                                   '{"gate_decision_id": 7}', json.dumps(history.decision_link("gdc_1")))
            ] == [None, None, None, None, None, None, None, "gdc_1"]
    # A new decision that succeeded, its archive checkpoint, and the event it wrote when the async confirm finished:
    # later than accepted_at, and later than every legacy event.
    done = repo.insert_decision(_row(n=5, attempt="att_1", request="req-ok"))
    assert repo.move(done["decision_id"], 1, repo.SUCCEEDED) is True
    repo.insert_checkpoint({"decision_id": done["decision_id"], "group_id": FA, "run_id": "run_a", "node_key": "gn_g",
                            "attempt_id": "att_1", "source_sha": SHA_A, "ref_name": "refs/flowgate/archive/done"})
    _run_event(gate_db, "run_a", 8, "gate_confirmed", 9, payload=json.dumps(history.decision_link(done["decision_id"])))
    legacy = history.legacy_events(FA, "gn_g")
    assert [e["decision_id"] for e in legacy] == ["legacy:run_a:3", "legacy:run_a:5", "legacy:run_a:6"]
    page = history.history(FA, "gn_g")
    assert [e["decision_id"] for e in page["items"]] == ["legacy:run_a:3", "legacy:run_a:5", "legacy:run_a:6",
                                                         done["decision_id"]]
    assert len({e["decision_id"] for e in page["items"]}) == len(page["items"])          # no duplicate
    latest = page["latest"]                                                               # the real result, not a fake
    assert (latest["decision_id"], latest["source"], latest["archived"], latest["result"]) == (
        done["decision_id"], "gate_decision", True, "confirmed")
    assert latest["effects"] is not None and latest["unknown"] == []
    # The same for a rejected decision: its linked gate_rejected event is its own too.
    bad = repo.insert_decision(_row(n=10, attempt="att_2", request="req-no", decision="reject", status="rejected"))
    _run_event(gate_db, "run_a", 9, "gate_rejected", 12, payload=json.dumps(history.decision_link(bad["decision_id"])))
    again = history.history(FA, "gn_g")
    assert [e["decision_id"] for e in again["items"]][-2:] == [done["decision_id"], bad["decision_id"]]
    assert len(again["items"]) == 5 and again["latest"]["decision_id"] == bad["decision_id"]
    assert again["latest"]["result"] == "rejected" and again["latest"]["archived"] is False
