"""Server Remote CLI Job contract and Agent registry regression tests."""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER", "test-only-pepper")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.api.v1 import agent_job_routes as routes  # noqa: E402
from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services.agent_registry import AgentError, AgentService, digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "sql" / "migrations"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _execute(self, sql, params=None):
        return self.db.execute(sql, params or [])

    def _execute_affected(self, sql, params=None):
        return self.db.execute(sql, params or []).rowcount

    def _fetch_one(self, sql, params=None):
        cur = self.db.execute(sql, params or [])
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    def _fetch_all(self, sql, params=None):
        cur = self.db.execute(sql, params or [])
        return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]


def setup_schema(store):
    store.db.executescript((MIG / "sqlite" / "121_agent_registry.sql").read_text())
    store.db.executescript((MIG / "sqlite" / "124_agent_jobs.sql").read_text())


def add_agent(store, aid, *, capable=True, enabled=True):
    ts = "2026-01-01T00:00:00Z"
    store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,
           configured_ai_cli,reported_ai_cli,reported_ai_api,reported_storage,
           protocol_version,protocol_compatibility,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, aid, enabled, "remote", "agent_pull", capable, capable, 0, 0,
         1, "compatible", ts, ts],
    )
    credential = "agc_" + aid
    store._execute(
        """INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at)
           VALUES(?,?,?,?,?)""",
        ["cred_" + aid, aid, digest("agent_credential", credential), "test", ts],
    )
    return credential


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_now", lambda: NOW)
    store = Store(str(tmp_path / "jobs.sqlite"))
    setup_schema(store)
    credentials = {aid: add_agent(store, aid) for aid in ("agt_a", "agt_b")}
    yield store, jobs.AgentJobService(store), credentials
    store.db.close()


def enqueue(svc, **kwargs):
    return svc.enqueue_remote_cli(command_line="codex exec --json", stdin_text="안녕\nこんにちは",
                                  cwd="/workspace", timeout_seconds=120, **kwargs)


def result(status="DONE", **kwargs):
    value = {"status": status, "exit_code": 0 if status == "DONE" else None,
             "stdout": "안녕", "stderr": "", "stdout_truncated": False,
             "stderr_truncated": False, "diagnostics": None, "end_reason": None,
             "duration_seconds": 1.25, "reported_at": "2026-01-01T00:00:01Z"}
    value.update(kwargs)
    return value


def assert_error(code, call):
    with pytest.raises(AgentError) as exc:
        call()
    assert exc.value.code == code


def test_migration_parity_and_sqlite_composite_key():
    for dialect in ("sqlite", "mysql", "postgres"):
        sql = (MIG / dialect / "124_agent_jobs.sql").read_text()
        assert "CREATE TABLE agent_jobs" in sql
        assert "PRIMARY KEY(job_id,attempt_id)" in sql
        for field in ("payload_digest", "result_digest", "cancel_requested", "lease_expires_at",
                      "stdout_truncated", "stderr_truncated", "reported_at", "duration_seconds"):
            assert field in sql
    db = sqlite3.connect(":memory:")
    db.executescript((MIG / "sqlite" / "124_agent_jobs.sql").read_text())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO agent_jobs(job_id,attempt_id) VALUES('j','a')")
    db.close()


def test_enqueue_duplicate_payload_conflict_and_new_attempt(setup):
    store, svc, _ = setup
    first = enqueue(svc, job_id="j", attempt_id="a")
    assert first == {"job_id": "j", "attempt_id": "a", "status": "NEW", "created": True}
    assert enqueue(svc, job_id="j", attempt_id="a")["created"] is False
    assert_error("attempt_payload_conflict", lambda: svc.enqueue_remote_cli(
        job_id="j", attempt_id="a", command_line="other", stdin_text="", cwd="/workspace", timeout_seconds=2))
    assert_error("job_attempt_active", lambda: enqueue(svc, job_id="j", attempt_id="b"))
    assert store._fetch_one("SELECT command_line FROM agent_jobs WHERE job_id='j'")["command_line"] == "codex exec --json"


def test_claim_target_no_job_capability_disabled_and_revoked(setup):
    store, svc, creds = setup
    assert svc.claim(creds["agt_a"]) is None
    enqueue(svc, job_id="target", attempt_id="one", target_agent_id="agt_b")
    assert svc.claim(creds["agt_a"]) is None
    claim = svc.claim(creds["agt_b"])["job"]
    assert claim["job_id"] == "target" and claim["execution_type"] == "remote_cli"
    assert claim["stdin_text"] == "안녕\nこんにちは" and claim["lease_token"].startswith("lsh_")
    store._execute("UPDATE agents SET reported_ai_cli=0 WHERE agent_id='agt_a'")
    assert_error("capability_required", lambda: svc.claim(creds["agt_a"]))
    store._execute("UPDATE agents SET enabled=0 WHERE agent_id='agt_a'")
    assert_error("agent_disabled", lambda: svc.claim(creds["agt_a"]))
    store._execute("UPDATE agents SET enabled=1 WHERE agent_id='agt_a'")
    store._execute("UPDATE agent_credentials SET revoked_at=? WHERE agent_id='agt_a'", ["2026-01-01T01:00:00Z"])
    assert_error("unauthorized", lambda: svc.claim(creds["agt_a"]))


def test_concurrent_claim_has_one_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_now", lambda: NOW)
    path = str(tmp_path / "concurrent.sqlite")
    seed = Store(path)
    setup_schema(seed)
    a = add_agent(seed, "agt_a")
    b = add_agent(seed, "agt_b")
    enqueue(jobs.AgentJobService(seed), job_id="one", attempt_id="one")
    seed.db.close()
    barrier = threading.Barrier(2)

    def claim(raw):
        store = Store(path)
        try:
            barrier.wait()
            return jobs.AgentJobService(store).claim(raw)
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(claim, (a, b)))
    assert sum(value is not None for value in values) == 1


def test_lease_started_retry_wrong_owner_and_wrong_token(setup):
    store, svc, creds = setup
    enqueue(svc, job_id="j", attempt_id="a")
    claim = svc.claim(creds["agt_a"])["job"]
    token = claim["lease_token"]
    assert_error("lease_owner_mismatch", lambda: svc.renew(creds["agt_b"], "j", "a", token))
    assert_error("lease_owner_mismatch", lambda: svc.started(creds["agt_a"], "j", "a", "bad"))
    renewed = svc.renew(creds["agt_a"], "j", "a", token)
    assert renewed["renew_after_seconds"] == 30 and renewed["cancel_requested"] is False
    assert svc.started(creds["agt_a"], "j", "a", token)["idempotent"] is False
    assert svc.started(creds["agt_a"], "j", "a", token)["idempotent"] is True
    assert store._fetch_one("SELECT status FROM agent_jobs WHERE job_id='j'")["status"] == "STARTED"


def test_claimed_expiry_requeues_with_new_owner(setup, monkeypatch):
    store, svc, creds = setup
    enqueue(svc, job_id="j", attempt_id="a")
    old = svc.claim(creds["agt_a"])["job"]["lease_token"]
    monkeypatch.setattr(jobs, "_now", lambda: NOW + timedelta(seconds=91))
    new = svc.claim(creds["agt_b"])["job"]["lease_token"]
    assert new != old
    assert_error("lease_owner_mismatch", lambda: svc.started(creds["agt_a"], "j", "a", old))
    assert store._fetch_one("SELECT lease_owner_agent_id FROM agent_jobs WHERE job_id='j'")["lease_owner_agent_id"] == "agt_b"


def test_started_expiry_fails_without_reexecution(setup, monkeypatch):
    store, svc, creds = setup
    enqueue(svc, job_id="j", attempt_id="a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    svc.started(creds["agt_a"], "j", "a", token)
    monkeypatch.setattr(jobs, "_now", lambda: NOW + timedelta(seconds=91))
    assert svc.claim(creds["agt_b"]) is None
    row = store._fetch_one("SELECT status,end_reason FROM agent_jobs WHERE job_id='j'")
    assert row == {"status": "FAILED", "end_reason": "lease_expired"}
    assert_error("terminal_conflict", lambda: svc.report(creds["agt_a"], "j", "a", token, result()))
    assert enqueue(svc, job_id="j", attempt_id="b")["created"] is True


def test_terminal_retry_conflict_and_owner_checks(setup):
    store, svc, creds = setup
    enqueue(svc, job_id="j", attempt_id="a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    svc.started(creds["agt_a"], "j", "a", token)
    payload = result()
    assert_error("lease_owner_mismatch", lambda: svc.report(creds["agt_b"], "j", "a", token, payload))
    assert_error("lease_owner_mismatch", lambda: svc.report(creds["agt_a"], "j", "a", "bad", payload))
    assert svc.report(creds["agt_a"], "j", "a", token, payload)["idempotent"] is False
    assert svc.report(creds["agt_a"], "j", "a", token, payload)["idempotent"] is True
    assert_error("terminal_conflict", lambda: svc.report(creds["agt_a"], "j", "a", token, result("FAILED", stdout="other")))
    row = store._fetch_one("SELECT status,stdout,lease_expires_at FROM agent_jobs WHERE job_id='j'")
    assert row == {"status": "DONE", "stdout": "안녕", "lease_expires_at": None}


def test_prestart_rejection_failure_and_cancel_race(setup):
    store, svc, creds = setup
    enqueue(svc, job_id="rejected", attempt_id="a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    assert_error("job_state_conflict", lambda: svc.report(creds["agt_a"], "rejected", "a", token, result()))
    svc.report(creds["agt_a"], "rejected", "a", token, result("REJECTED", end_reason="workspace_guard"))
    enqueue(svc, job_id="cancel", attempt_id="a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    assert svc.request_cancel("cancel", "a")["cancel_requested"] is True
    assert svc.renew(creds["agt_a"], "cancel", "a", token)["cancel_requested"] is True
    svc.report(creds["agt_a"], "cancel", "a", token, result("CANCELLED", end_reason="cancel_requested"))
    assert store._fetch_one("SELECT status FROM agent_jobs WHERE job_id='cancel'")["status"] == "CANCELLED"


def test_active_lease_rechecks_credential_and_protocol(setup):
    store, svc, creds = setup
    enqueue(svc, job_id="j", attempt_id="a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    store._execute("UPDATE agents SET protocol_compatibility='incompatible',last_rejected_protocol_version=2,last_protocol_rejection_at=? WHERE agent_id='agt_a'",
                   ["2026-01-01T00:00:00Z"])
    assert_error("agent_incompatible", lambda: svc.renew(creds["agt_a"], "j", "a", token))
    store._execute("UPDATE agents SET protocol_compatibility='compatible',last_rejected_protocol_version=NULL,last_protocol_rejection_at=NULL WHERE agent_id='agt_a'")
    AgentService(store).revoke("agt_a")
    assert_error("unauthorized", lambda: svc.started(creds["agt_a"], "j", "a", token))


def test_enrollment_credential_rotation_remains_usable(setup):
    store, svc, creds = setup
    registry = AgentService(store)
    issued = registry.issue("agt_a")
    enrolled = registry.enroll({"enrollment_token": issued["enrollment_token"],
                                "protocol_version": 1, "agent_version": "1.0",
                                "os": "linux", "architecture": "amd64"})
    assert_error("unauthorized", lambda: svc.claim(creds["agt_a"]))
    enqueue(svc, job_id="j", attempt_id="a")
    assert svc.claim(enrolled["agent_credential"])["job"]["job_id"] == "j"


def test_api_contract_and_heartbeat_regression(setup, monkeypatch):
    store, svc, creds = setup
    monkeypatch.setattr(routes, "svc", lambda: svc)
    app = FastAPI()
    app.include_router(routes.router, prefix="/flowgate/api/v1")
    with TestClient(app) as client:
        url = "/flowgate/api/v1/agent/jobs/claim"
        assert client.post(url, json={}).status_code == 401
        auth = {"Authorization": "Bearer " + creds["agt_a"]}
        assert client.post(url, headers=auth, json={}).status_code == 204
        enqueue(svc, job_id="j", attempt_id="a")
        claim = client.post(url, headers=auth, json={})
        assert claim.status_code == 200 and claim.headers["cache-control"] == "no-store"
        wire = claim.json()["job"]
        path = "/flowgate/api/v1/agent/jobs/j/a"
        assert client.post(path + "/started", headers=auth,
                           json={"lease_token": wire["lease_token"]}).json()["status"] == "STARTED"
        body = {"lease_token": wire["lease_token"], **result()}
        assert client.post(path + "/report", headers=auth, json=body).json()["status"] == "DONE"
        assert client.post(path + "/report", headers=auth, json=body).json()["idempotent"] is True
    heartbeat = AgentService(store).heartbeat(creds["agt_a"], {
        "agent_id": "agt_a", "protocol_version": 1, "agent_version": "1.0",
        "os": "linux", "architecture": "amd64",
        "reported_capabilities": {"ai_cli": True, "ai_api": False, "storage": False},
    })
    assert heartbeat["accepted"] is True
