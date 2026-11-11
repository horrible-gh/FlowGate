"""flowgate.v02test.0022 CH0002 -- an unclaimed AI-run Job says why it was never claimed.

Before this, slot saturation, an offline Agent, a refused claim (403) and an Agent whose
claim loop had stopped all ended as the same ``remote_agent_no_response: not_claimed``.
The server now records each Agent's last authenticated claim poll and last refused claim,
and at claim_timeout stores a verdict (``agent_jobs.claim_wait_cause`` + ``diagnostics``)
that the remote provider passes on as ``cause`` / ``message``. The code and reason
(``remote_agent_no_response: not_claimed``) are unchanged.

Real SQLite schema (full migration chain) and the real AgentJobService.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER", "test-only-pepper")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services.agent_registry import AgentError, digest  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_remote  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
MIG = ROOT / "sql" / "migrations"
MIGRATION = "183_agent_claim_diagnostics.sql"
T0 = datetime(2026, 11, 11, 1, 0, 0, tzinfo=timezone.utc)
WAIT = jobs.AI_CLAIM_WAIT_SECONDS


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


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def at(self, seconds):
        self.t = T0 + timedelta(seconds=seconds)


def _iso(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_agent(store, aid, *, capable=True):
    ts = "2026-01-01T00:00:00Z"
    store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,
           configured_ai_cli,reported_ai_cli,reported_ai_api,reported_storage,
           protocol_version,protocol_compatibility,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, aid, True, "remote", "agent_pull", capable, capable, 0, 0, 1, "compatible", ts, ts],
    )
    credential = "agc_" + aid
    store._execute(
        """INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at)
           VALUES(?,?,?,?,?)""",
        ["cred_" + aid, aid, digest("agent_credential", credential), "test", ts],
    )
    return credential


def heartbeat(store, aid, seconds):
    """The Agent's heartbeat as the registry stores it (online for ONLINE_TTL after it)."""
    store._execute("UPDATE agent_credentials SET last_heartbeat_at=? WHERE agent_id=?", [_iso(seconds), aid])


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(jobs, "_now", clock)
    store = Store(str(migrated_sqlite_db("agent_claim_wait_cause_0022.sqlite")))
    cred = add_agent(store, "agt_a")
    yield store, jobs.AgentJobService(store), cred, clock
    store.db.close()


def ai_job(svc, job_id, target="agt_a"):
    return svc.enqueue_remote_cli(command_line="claude -p", stdin_text="prompt", cwd="/tmp/" + job_id,
                                  timeout_seconds=600, job_id=job_id, attempt_id="1",
                                  target_agent_id=target, ai_run_id="run_" + job_id,
                                  retrieval_owner=jobs.retrieval_owner_for("run_" + job_id))


def plain_job(svc, job_id):
    return svc.enqueue_remote_cli(command_line="echo hi", stdin_text="", cwd="/tmp/" + job_id,
                                  timeout_seconds=600, job_id=job_id, attempt_id="1", target_agent_id="agt_a")


def agent(store, aid="agt_a"):
    return store._fetch_one("SELECT * FROM agents WHERE agent_id=?", [aid])


def job(store, job_id):
    return store._fetch_one("SELECT * FROM agent_jobs WHERE job_id=?", [job_id])


def expire(svc, clock, seconds):
    clock.at(seconds)
    svc.advance_ai_jobs()


# -- migration ----------------------------------------------------------------

@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_migration_183_is_additive_and_nullable(dialect):
    sql = re.sub(r"--[^\n]*", "", (MIG / dialect / MIGRATION).read_text(encoding="utf-8")).lower()
    alters = [line.strip() for line in sql.splitlines() if line.strip()]
    expected = {("agents", "last_claim_poll_at"), ("agents", "last_claim_refusal_code"),
                ("agents", "last_claim_refusal_at"), ("agent_jobs", "claim_wait_cause")}
    assert {tuple(re.match(r"alter table (\w+) add column (\w+) ", line).groups()) for line in alters} == expected
    for line in alters:
        assert "not null" not in line and "default" not in line


# -- what claim() records -------------------------------------------------------

def test_authenticated_poll_is_recorded_even_on_an_empty_queue(env):
    store, svc, cred, clock = env
    clock.at(5)
    assert svc.claim(cred) is None
    assert agent(store)["last_claim_poll_at"] == jobs._stamp(clock.t)
    assert agent(store)["last_claim_refusal_code"] is None


@pytest.mark.parametrize("change, code", [
    ("UPDATE agents SET reported_ai_cli=FALSE WHERE agent_id='agt_a'", "capability_required"),
    ("UPDATE agents SET enabled=FALSE WHERE agent_id='agt_a'", "agent_disabled"),
])
def test_refused_claim_is_recorded_and_still_refused(env, change, code):
    store, svc, cred, clock = env
    store._execute(change)
    clock.at(7)
    with pytest.raises(AgentError) as exc:
        svc.claim(cred)
    assert exc.value.code == code
    row = agent(store)
    assert (row["last_claim_refusal_code"], row["last_claim_refusal_at"]) == (code, jobs._stamp(clock.t))
    assert row["last_claim_poll_at"] is None          # the refused claim's own writes rolled back


def test_unknown_credential_records_nothing(env):
    store, svc, _, _ = env
    with pytest.raises(AgentError) as exc:
        svc.claim("agc_not_registered")
    assert exc.value.code == "unauthorized"
    assert agent(store)["last_claim_refusal_code"] is None


# -- the verdict at claim_timeout -------------------------------------------------

def test_refused_claim_explains_the_timeout_even_while_online(env):
    store, svc, cred, clock = env
    assert svc.claim(cred) is None                    # polled fine at T0
    ai_job(svc, "job_r")
    store._execute("UPDATE agents SET reported_ai_cli=FALSE WHERE agent_id='agt_a'")
    clock.at(3)
    with pytest.raises(AgentError):
        svc.claim(cred)
    store._execute("UPDATE agents SET reported_ai_cli=TRUE WHERE agent_id='agt_a'")  # root back, loop stays dead
    heartbeat(store, "agt_a", WAIT - 10)
    expire(svc, clock, WAIT + 1)
    row = job(store, "job_r")
    assert (row["status"], row["end_reason"], row["terminal_cause"]) == ("CANCELLED", "not_claimed", "claim_timeout")
    assert row["claim_wait_cause"] == jobs.CLAIM_WAIT_REFUSED
    assert "capability_required" in row["diagnostics"] and "Agent online" in row["diagnostics"]


def test_a_later_good_poll_supersedes_an_old_refusal(env):
    store, svc, cred, clock = env
    store._execute("UPDATE agents SET reported_ai_cli=FALSE WHERE agent_id='agt_a'")
    with pytest.raises(AgentError):
        svc.claim(cred)
    store._execute("UPDATE agents SET reported_ai_cli=TRUE WHERE agent_id='agt_a'")
    clock.at(2)
    assert svc.claim(cred) is None                    # restarted Agent polls again
    clock.at(4)
    ai_job(svc, "job_s")
    heartbeat(store, "agt_a", WAIT - 10)
    expire(svc, clock, WAIT + 5)
    assert job(store, "job_s")["claim_wait_cause"] == jobs.CLAIM_WAIT_NO_POLL


def test_offline_agent(env):
    store, svc, cred, clock = env
    heartbeat(store, "agt_a", 0)
    assert svc.claim(cred) is None
    clock.at(1)
    ai_job(svc, "job_o")
    expire(svc, clock, WAIT + 2)                      # heartbeat older than ONLINE_TTL
    row = job(store, "job_o")
    assert row["claim_wait_cause"] == jobs.CLAIM_WAIT_OFFLINE
    assert _iso(0) in row["diagnostics"]


def test_online_agent_holding_jobs_without_polling_is_busy(env):
    store, svc, cred, clock = env
    plain_job(svc, "job_held")
    held = svc.claim(cred)["job"]                     # poll at T0, the only slot is now busy
    svc.started(cred, held["job_id"], held["attempt_id"], held["lease_token"])
    clock.at(1)
    ai_job(svc, "job_b")
    heartbeat(store, "agt_a", WAIT - 10)
    expire(svc, clock, WAIT + 2)
    row = job(store, "job_b")
    assert row["claim_wait_cause"] == jobs.CLAIM_WAIT_BUSY
    assert "1 Job(s)" in row["diagnostics"]


def test_online_agent_holding_nothing_and_not_polling_has_stopped(env):
    store, svc, cred, clock = env
    assert svc.claim(cred) is None
    clock.at(1)
    ai_job(svc, "job_n")
    heartbeat(store, "agt_a", WAIT - 10)
    expire(svc, clock, WAIT + 2)
    row = job(store, "job_n")
    assert row["claim_wait_cause"] == jobs.CLAIM_WAIT_NO_POLL
    assert jobs._stamp(T0) in row["diagnostics"]


def test_agent_that_polled_after_queueing_but_took_another_job(env):
    store, svc, cred, clock = env
    plain_job(svc, "job_older")
    clock.at(1)
    ai_job(svc, "job_p")
    clock.at(2)
    assert svc.claim(cred)["job"]["job_id"] == "job_older"   # oldest first
    heartbeat(store, "agt_a", WAIT - 10)
    expire(svc, clock, WAIT + 2)
    assert job(store, "job_p")["claim_wait_cause"] == jobs.CLAIM_WAIT_POLLED


def test_missing_agent(env):
    _, svc, _, _ = env
    cause, note = svc._claim_wait_cause({"target_agent_id": "agt_gone", "created_at": jobs._stamp(T0)}, T0, {})
    assert (cause, note) == (jobs.CLAIM_WAIT_AGENT_MISSING, "Target Agent not found")


def test_cause_is_returned_to_the_executor(env):
    store, svc, cred, clock = env
    ai_job(svc, "job_c")
    expire(svc, clock, WAIT + 1)
    assert svc.get_ai_job("job_c", "1")["claim_wait_cause"] == jobs.CLAIM_WAIT_OFFLINE
    collected = svc.collect_ai_result("job_c", "1", jobs.retrieval_owner_for("run_job_c"))
    assert collected["claim_wait_cause"] == jobs.CLAIM_WAIT_OFFLINE
    assert collected["diagnostics"].startswith("Agent offline")


# -- provider boundary: same code/reason, the verdict in cause/message ---------

def test_settle_keeps_the_reason_and_adds_cause_and_message():
    run = {"run_id": "run_x", "cancel_event": threading.Event(), "timed_out": False,
           "remote_job": {"target_agent_id": "agt_a", "job_id": "job_x", "attempt_id": "1"}}
    result = {"status": "CANCELLED", "lease_owner_agent_id": None, "terminal_cause": "claim_timeout",
              "claim_wait_cause": "agent_busy", "diagnostics": "Agent online but made no claim poll"}
    status, detail = provider_remote._settle(run, {"id": "aip_r"}, "claude", result, 0.0, None)
    assert (status, detail) == ("spawn_failed", "remote_agent_no_response: not_claimed")
    final = run["remote_final_failure"]
    assert (final["code"], final["reason"], final["cause"]) == ("remote_agent_no_response", "not_claimed", "agent_busy")
    assert final["message"] == "Agent online but made no claim poll"


def test_settle_without_a_verdict_is_unchanged():
    run = {"run_id": "run_y", "cancel_event": threading.Event(), "timed_out": False,
           "remote_job": {"target_agent_id": "agt_a"}}
    result = {"status": "CANCELLED", "lease_owner_agent_id": None}
    assert provider_remote._settle(run, {"id": "aip_r"}, "claude", result, 0.0, None) == (
        "spawn_failed", "remote_agent_no_response: not_claimed")
    assert run["remote_final_failure"]["cause"] is None and run["remote_final_failure"]["message"] is None
