"""0618: owner identity, bounded wait, and point-in-time provider evidence."""
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from modules.flow_gate.services import snapshot_request_service as service


SQL = Path(__file__).parents[1] / "sql" / "migrations" / "sqlite"


def _insert(conn, sid, chain, status="requested"):
    conn.execute(
        "INSERT INTO snapshot_requests "
        "(snapshot_id,project_id,group_id,run_id,chain_id,token_id,provider_id,"
        "reason,scope,requested_paths,purpose,source_kind,status,requested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "p", "g", sid, chain, "tok", "provider", "reason", "whole_source",
         "[]", "tests", "current_worktree", status, sid),
    )


def test_sqlite_migration_closes_legacy_duplicates_and_guards_owner(tmp_path):
    conn = sqlite3.connect(tmp_path / "snap.db")
    for filename in (
        "115_snapshot_requests.sql",
        "118_snapshot_lineage.sql",
        "120_snapshot_rejection_reason.sql",
    ):
        conn.executescript((SQL / filename).read_text(encoding="utf-8"))
    _insert(conn, "a", "chain")
    _insert(conn, "b", "chain")
    conn.executescript((SQL / "121_snapshot_pending_owner_provenance.sql").read_text(encoding="utf-8"))
    assert conn.execute("SELECT snapshot_id FROM snapshot_requests WHERE status='requested'").fetchall() == [("a",)]
    assert conn.execute("SELECT status FROM snapshot_requests WHERE snapshot_id='b'").fetchone() == ("rejected",)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "c", "chain")
    _insert(conn, "d", "another-chain")
    conn.execute("UPDATE snapshot_requests SET status='rejected' WHERE snapshot_id='a'")
    _insert(conn, "e", "chain")
    assert conn.execute("SELECT COUNT(*) FROM snapshot_requests WHERE status='requested'").fetchone() == (2,)
    conn.close()


@pytest.mark.parametrize("status,field", [
    ("created", None), ("rejected", "rejection_reason"), ("failed", "failure_reason"),
])
def test_wait_returns_decision_and_details(monkeypatch, status, field):
    row = {"snapshot_id": "snap", "status": "requested"}
    result = row | {"status": status}
    if field:
        result[field] = "public reason"
    monkeypatch.setattr(service.db, "get", lambda _sid: result)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SECONDS", .05)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_POLL_SECONDS", .001)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SAFETY_SECONDS", 0)
    settled = service.wait_for_decision(row, 1)
    assert settled["status"] == status
    assert settled["wait_timed_out"] is False
    if field:
        assert settled[field] == "public reason"


def test_wait_timeout_preserves_identity_and_budget_cap(monkeypatch):
    row = {"snapshot_id": "snap", "status": "requested"}
    monkeypatch.setattr(service.db, "get", lambda _sid: row)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SECONDS", 10)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SAFETY_SECONDS", .02)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_POLL_SECONDS", .001)
    started = time.monotonic()
    settled = service.wait_for_decision(row, .03)
    assert settled["snapshot_id"] == "snap"
    assert settled["wait_timed_out"] is True
    assert time.monotonic() - started < .1


def test_request_data_uses_canonical_fallback_provenance():
    run = {
        "run_id": "run", "chain_id": "chain", "project_id": "p", "group_id": "g",
        "requested_provider_id": "selected", "provider_id": "actual",
        "provider": {"name": "Actual at request time"}, "attempt_no": 2,
    }
    data = service.request_data_for_run(run, {"token_id": "tok"}, {})
    assert data["provider_id"] == "actual"
    assert data["requested_provider_id"] == "selected"
    assert data["actual_provider_name"] == "Actual at request time"
    assert data["fallback_used"] is True
    assert data["provider_source"] == "fallback"


def test_sqlite_concurrent_owner_claim_has_one_winner(tmp_path):
    db_path = tmp_path / "race.db"
    with sqlite3.connect(db_path) as conn:
        for number in (115, 118, 120, 121):
            name = {
                115: "snapshot_requests", 118: "snapshot_lineage",
                120: "snapshot_rejection_reason", 121: "snapshot_pending_owner_provenance",
            }[number]
            conn.executescript((SQL / f"{number}_{name}.sql").read_text(encoding="utf-8"))
    barrier = threading.Barrier(10)
    outcomes = []
    lock = threading.Lock()

    def claim(index):
        conn = sqlite3.connect(db_path, timeout=10)
        barrier.wait()
        try:
            _insert(conn, f"s{index}", "same-chain")
            conn.commit()
            outcome = "created"
        except sqlite3.IntegrityError:
            conn.rollback()
            outcome = "reused"
        finally:
            conn.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert outcomes.count("created") == 1
    assert outcomes.count("reused") == 9
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshot_requests WHERE status='requested'").fetchone() == (1,)


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_each_dialect_has_owner_unique_guard_and_provenance_columns(dialect):
    path = SQL.parent / dialect / "121_snapshot_pending_owner_provenance.sql"
    sql = path.read_text(encoding="utf-8")
    assert "uq_snapshot_pending_owner" in sql
    assert "COALESCE(NULLIF(chain_id,''),run_id)" in sql
    for column in ("requested_provider_id", "actual_provider_name", "provider_source",
                   "attempt_no", "fallback_used"):
        assert column in sql


def test_wait_does_not_return_on_intermediate_approved(monkeypatch):
    states = iter(["approved", "created"])
    monkeypatch.setattr(service.db, "get", lambda _sid: {"snapshot_id": "snap", "status": next(states)})
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SECONDS", .1)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_SAFETY_SECONDS", 0)
    monkeypatch.setattr(service, "SNAPSHOT_WAIT_POLL_SECONDS", .001)
    result = service.wait_for_decision({"snapshot_id": "snap", "status": "requested"}, 1)
    assert result["status"] == "created"
    assert result["wait_timed_out"] is False


def test_cli_failed_status_exposes_public_failure_detail():
    from modules.flow_gate.services import snapshot_access_service
    result = snapshot_access_service._metadata({
        "snapshot_id": "snap_test", "status": "failed",
        "failure_code": "snapshot_source_changed",
        "failure_reason": "source changed while snapshot was being built",
    })
    assert result["status"] == "failed"
    assert result["failure_code"] == "snapshot_source_changed"
    assert result["failure_reason"] == "source changed while snapshot was being built"
