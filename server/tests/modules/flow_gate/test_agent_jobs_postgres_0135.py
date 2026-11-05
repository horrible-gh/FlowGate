"""Remote Agent Job claim / lifecycle against a REAL PostgreSQL server (opt-in).

flowgate.v02.0135 T#1. ``agent_jobs.cancel_requested`` / ``stdout_truncated`` /
``stderr_truncated`` are BOOLEAN on PostgreSQL but INTEGER on SQLite, so the SQLite
suite (test_agent_jobs_0636.py) could never see the ``boolean = integer`` error that made
every ``POST /agent/jobs/claim`` answer HTTP 500 (text/plain, 21 bytes) on PostgreSQL:
``claim() -> _reclaim() -> _advance_ai()`` parsed ``cancel_requested=0`` even on an empty
queue.

This file runs the real route + service + ``FlowGateStore`` (``dialect.translate`` to
PostgreSQL, affected-row counts from the psycopg2 cursor) on PostgreSQL. It SKIPS unless
``FLOWGATE_PG_TEST_DSN`` is set (same opt-in convention as the other ``postgres`` tests)::

    # PowerShell, from server/
    $env:FLOWGATE_PG_TEST_DSN = "postgresql://<user>:<password>@<host>:5432/<db>"
    python -m pytest tests/modules/flow_gate/test_agent_jobs_postgres_0135.py -v -rs

Footprint: every table it touches is a session ``TEMP`` table built from the real
PostgreSQL migration files (121/124/170/172), so it needs only the TEMP privilege and
leaves nothing behind -- it can run against the staging database with a read-only
account. A guard test fails the module if any table it uses resolves outside ``pg_temp``.

Two *concurrent* PostgreSQL sessions racing one claim / a claim and a cancel are not run
here (TEMP tables are private to one session): test_agent_jobs_postgres_race_0135.py runs
them on independent connections in a throw-away schema (same opt-in DSN, needs CREATE).
"""
from __future__ import annotations

import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER", "test-only-pepper")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

DSN_ENV = "FLOWGATE_PG_TEST_DSN"
DSN = os.environ.get(DSN_ENV)

psycopg2 = pytest.importorskip(
    "psycopg2", reason="psycopg2 is not installed; the live PostgreSQL check cannot run here")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason=f"{DSN_ENV} is not set -- opt-in live PostgreSQL check"),
]

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from modules.flow_gate.api.v1 import agent_job_routes as routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import dialect as _dialect  # noqa: E402
from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services.agent_registry import AgentError, digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PG_MIG = ROOT / "sql" / "migrations" / "postgres"
T0 = datetime(2026, 11, 5, 5, 0, 0, tzinfo=timezone.utc)
TABLES = ("users", "ai_invoke_runs", "agents", "agent_credentials", "agent_enrollments",
          "agent_jobs", "ai_remote_recovery_holds")
URL = "/flowgate/api/v1/agent/jobs"


class _Txn:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=None):
        self.cursor.execute(sql, list(params or []))
        return self.cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self.cursor.fetchall()]


class PgDB:
    """sqloader-shaped backend over one psycopg2 connection (the production store's ``_db``)."""

    db_type = _dialect.POSTGRESQL

    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin_transaction(self):
        cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield _Txn(cursor)
        except BaseException:
            self.conn.rollback()
            cursor.close()
            raise
        self.conn.commit()
        cursor.close()

    def execute(self, sql, params=None):
        cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql, list(params or []))
        return cursor

    def commit(self):
        self.conn.commit()

    def fetch_one(self, sql, params=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, list(params or []))
            row = cursor.fetchone()
        self.conn.commit()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, list(params or []))
            rows = [dict(r) for r in cursor.fetchall()]
        self.conn.commit()
        return rows


def _temp_ddl(name):
    sql = (PG_MIG / name).read_text(encoding="utf-8")
    return re.sub(r"\bCREATE TABLE\b", "CREATE TEMP TABLE", sql)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def pg(monkeypatch):
    conn = psycopg2.connect(DSN, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            # Minimal parents with the production column types (001_flowgate_schema.sql).
            cur.execute("""CREATE TEMP TABLE users(user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                           is_active INTEGER NOT NULL DEFAULT 1, is_admin INTEGER NOT NULL DEFAULT 0)""")
            cur.execute("CREATE TEMP TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY)")
            for name in ("121_agent_registry.sql", "124_agent_jobs.sql",
                         "170_personal_agents.sql", "172_agent_job_ai_run_link.sql"):
                cur.execute(_temp_ddl(name))
        conn.commit()
        store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
        store._db, store._sq = PgDB(conn), None
        clock = Clock()
        monkeypatch.setattr(jobs, "_now", clock)
        service = jobs.AgentJobService(store)
        monkeypatch.setattr(routes, "svc", lambda: service)
        app = FastAPI()
        app.include_router(routes.router, prefix="/flowgate/api/v1")
        with TestClient(app, raise_server_exceptions=False) as client:
            yield {"conn": conn, "store": store, "svc": service, "clock": clock, "client": client}
    finally:
        conn.rollback()
        conn.close()  # TEMP tables vanish with the session


def add_agent(pg, aid, *, enabled=True, capable=True, compatible=True, owner=None):
    ts = "2026-11-05T00:00:00Z"
    store = pg["store"]
    store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,configured_ai_cli,
           reported_ai_cli,reported_ai_api,reported_storage,protocol_version,protocol_compatibility,
           last_rejected_protocol_version,last_protocol_rejection_at,created_at,updated_at,owner_user_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, aid, enabled, "remote", "agent_pull", capable, capable, False, False, 1,
         "compatible" if compatible else "incompatible", None if compatible else 2,
         None if compatible else ts, ts, ts, owner],
    )
    credential = "agc_" + aid
    store._execute(
        "INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) VALUES(?,?,?,?,?)",
        ["cred_" + aid, aid, digest("agent_credential", credential), "test", ts],
    )
    return {"Authorization": "Bearer " + credential}


def enqueue(pg, **kwargs):
    return pg["svc"].enqueue_remote_cli(command_line="codex exec --json", stdin_text="안녕",
                                        cwd="/workspace", timeout_seconds=kwargs.pop("timeout_seconds", 120),
                                        **kwargs)


def ai_enqueue(pg, job_id, agent_id, **kwargs):
    return enqueue(pg, job_id=job_id, attempt_id="1", target_agent_id=agent_id, ai_run_id="run_" + job_id,
                   graph_exec_id="gx_" + job_id, retrieval_owner=jobs.retrieval_owner_for("run_" + job_id),
                   ai_group_id="flowgate.test.0135", ai_token_id="tok_" + job_id, **kwargs)


def report_body(lease_token, status="DONE", **kwargs):
    body = {"lease_token": lease_token, "status": status, "exit_code": 0 if status == "DONE" else None,
            "stdout": "ok", "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
            "diagnostics": None, "end_reason": None, "duration_seconds": 0.5,
            "reported_at": "2026-11-05T05:00:01Z"}
    body.update(kwargs)
    return body


def row(pg, job_id, attempt_id="1"):
    return pg["store"]._fetch_one("SELECT * FROM agent_jobs WHERE job_id=? AND attempt_id=?", [job_id, attempt_id])


def claim(pg, headers):
    return pg["client"].post(URL + "/claim", headers=headers, json={})


def test_every_table_used_is_session_temporary(pg):
    with pg["conn"].cursor() as cur:
        for table in TABLES:
            cur.execute("""SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE c.oid=to_regclass(%s)""", [table])
            (nsp,) = cur.fetchone()
            assert nsp.startswith("pg_temp"), (table, nsp)
        cur.execute("""SELECT data_type FROM information_schema.columns
                       WHERE table_name='agent_jobs' AND column_name='cancel_requested'
                       AND table_schema LIKE 'pg_temp%%'""")
        assert cur.fetchone()[0] == "boolean"  # the production PostgreSQL column type


def test_integer_literal_is_what_postgres_rejects(pg):
    """The pre-fix SQL shape fails on this very table; the shipped TRUE/FALSE form does not."""
    with pg["conn"].cursor() as cur:
        with pytest.raises(psycopg2.Error) as exc:
            cur.execute("SELECT count(*) FROM agent_jobs WHERE cancel_requested=0")
        assert "boolean = integer" in str(exc.value)
        pg["conn"].rollback()
        cur.execute("SELECT count(*) FROM agent_jobs WHERE cancel_requested=FALSE")
        assert cur.fetchone()[0] == 0


def test_idle_claim_is_204_not_500(pg):
    headers = add_agent(pg, "agt_idle")
    for _ in range(3):  # the Agent polls again and again on an empty queue
        response = claim(pg, headers)
        assert response.status_code == 204, (response.status_code, response.text[:200])
        assert response.content == b""
        assert response.headers["cache-control"] == "no-store"


def test_rejections_keep_their_intended_json_errors(pg):
    cases = {
        "unauthorized": {"Authorization": "Bearer agc_nobody"},
        "agent_disabled": add_agent(pg, "agt_off", enabled=False),
        "capability_required": add_agent(pg, "agt_nocap", capable=False),
        "agent_incompatible": add_agent(pg, "agt_old", compatible=False),
    }
    expected = {"unauthorized": 401, "agent_disabled": 403, "capability_required": 403, "agent_incompatible": 403}
    enqueue(pg, job_id="bait", attempt_id="1")  # a claimable Job must not leak to any of them
    for code, headers in cases.items():
        response = claim(pg, headers)
        assert response.status_code == expected[code], (code, response.status_code, response.text[:200])
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == code
    assert row(pg, "bait")["status"] == "NEW"


def test_enqueue_claim_started_report_end_to_end(pg):
    a, b = add_agent(pg, "agt_a"), add_agent(pg, "agt_b")
    created = enqueue(pg, job_id="job_e2e", attempt_id="1")
    assert created["created"] is True
    stored = row(pg, "job_e2e")
    assert stored["cancel_requested"] is False and stored["stdout_truncated"] is False

    response = claim(pg, a)
    assert response.status_code == 200, response.text[:300]
    job = response.json()["job"]
    assert (job["job_id"], job["attempt_id"], job["cancel_requested"]) == ("job_e2e", "1", False)
    lease = job["lease_token"]
    assert claim(pg, b).status_code == 204  # no duplicate delivery to another Agent
    assert claim(pg, a).status_code == 204  # nor a second one to the same Agent

    base = f"{URL}/job_e2e/1"
    renewed = pg["client"].post(base + "/lease", headers=a, json={"lease_token": lease})
    assert renewed.status_code == 200 and renewed.json()["cancel_requested"] is False
    started = pg["client"].post(base + "/started", headers=a, json={"lease_token": lease})
    assert started.status_code == 200 and started.json()["idempotent"] is False
    again = pg["client"].post(base + "/started", headers=a, json={"lease_token": lease})
    assert again.status_code == 200 and again.json()["idempotent"] is True
    wrong = pg["client"].post(base + "/started", headers=b, json={"lease_token": lease})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "lease_owner_mismatch"

    body = report_body(lease, stdout_truncated=True)
    reported = pg["client"].post(base + "/report", headers=a, json=body)
    assert reported.status_code == 200, reported.text[:300]
    assert reported.json()["status"] == "DONE"
    final = row(pg, "job_e2e")
    assert final["status"] == "DONE" and final["child_exit_code"] == 0
    assert final["stdout_truncated"] is True and final["stderr_truncated"] is False
    assert final["lease_expires_at"] is None
    retry = pg["client"].post(base + "/report", headers=a, json=body)
    assert retry.status_code == 200 and retry.json()["idempotent"] is True
    assert claim(pg, a).status_code == 204


def test_personal_agent_claims_only_its_owners_job(pg):
    pg["store"]._execute("INSERT INTO users(user_id,username,is_active,is_admin) VALUES(?,?,?,?)",
                         ["usr_1", "owner", 1, 0])
    mine = add_agent(pg, "agt_mine", owner="usr_1")
    system = add_agent(pg, "agt_sys")
    enqueue(pg, job_id="job_mine", attempt_id="1", requested_by_user_id="usr_1")
    assert claim(pg, system).status_code == 204
    response = claim(pg, mine)
    assert response.status_code == 200 and response.json()["job"]["job_id"] == "job_mine"


def test_cancel_retry_and_lease_expiry_without_duplicates(pg):
    a, b = add_agent(pg, "agt_a"), add_agent(pg, "agt_b")
    svc, clock = pg["svc"], pg["clock"]
    # cancel of a claimed legacy Job
    enqueue(pg, job_id="job_c", attempt_id="1")
    lease = claim(pg, a).json()["job"]["lease_token"]
    assert svc.request_cancel("job_c", "1") == {"status": "CLAIMED", "cancel_requested": True}
    renewed = pg["client"].post(f"{URL}/job_c/1/lease", headers=a, json={"lease_token": lease})
    assert renewed.json()["cancel_requested"] is True
    cancelled = pg["client"].post(f"{URL}/job_c/1/report", headers=a, json=report_body(lease, "CANCELLED"))
    assert cancelled.status_code == 200 and row(pg, "job_c")["status"] == "CANCELLED"

    # retry = a new attempt of the same Job; the payload of an attempt cannot change
    assert enqueue(pg, job_id="job_c", attempt_id="1")["created"] is False
    with pytest.raises(AgentError) as exc:
        svc.enqueue_remote_cli(command_line="other", stdin_text="", cwd="/w", timeout_seconds=5,
                               job_id="job_c", attempt_id="1")
    assert exc.value.code == "attempt_payload_conflict"
    assert enqueue(pg, job_id="job_c", attempt_id="2")["created"] is True
    with pytest.raises(AgentError) as exc:
        enqueue(pg, job_id="job_c", attempt_id="3")
    assert exc.value.code == "job_attempt_active"
    second = claim(pg, a).json()["job"]
    assert (second["job_id"], second["attempt_id"]) == ("job_c", "2")

    # an expired CLAIMED legacy lease goes back to NEW once and moves to the next Agent
    clock.advance(jobs.LEASE_TTL_SECONDS + 1)
    moved = claim(pg, b)
    assert moved.status_code == 200 and moved.json()["job"]["attempt_id"] == "2"
    stale = pg["client"].post(f"{URL}/job_c/2/started", headers=a, json={"lease_token": second["lease_token"]})
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "lease_owner_mismatch"
    assert claim(pg, a).status_code == 204
    assert row(pg, "job_c", "2")["lease_owner_agent_id"] == "agt_b"


def test_ai_run_job_cancel_race_stage_limits_and_retrieval(pg):
    a = add_agent(pg, "agt_ai")
    svc, clock = pg["svc"], pg["clock"]
    # cancel before claim wins: the Job is never delivered
    ai_enqueue(pg, "ai_pre", "agt_ai")
    cancelled = svc.cancel_ai_job("ai_pre", "1")
    assert cancelled == {"status": "CANCELLED", "cancel_requested": True, "cancelled_unclaimed": True}
    assert claim(pg, a).status_code == 204

    # claim wins, then the cancel becomes a stop request seen on the next renew
    ai_enqueue(pg, "ai_post", "agt_ai")
    job = claim(pg, a).json()["job"]
    assert job["job_id"] == "ai_post" and job["cancel_requested"] is False
    assert row(pg, "ai_post")["stdin_text"] == ""  # delivered once, input not kept
    late = svc.cancel_ai_job("ai_post", "1")
    assert late == {"status": "CLAIMED", "cancel_requested": True, "cancelled_unclaimed": False}
    renewed = pg["client"].post(f"{URL}/ai_post/1/lease", headers=a, json={"lease_token": job["lease_token"]})
    assert renewed.status_code == 200 and renewed.json()["cancel_requested"] is True
    done = pg["client"].post(f"{URL}/ai_post/1/report", headers=a,
                             json=report_body(job["lease_token"], "CANCELLED"))
    assert done.status_code == 200
    collected = svc.collect_ai_result("ai_post", "1", jobs.retrieval_owner_for("run_ai_post"))
    assert collected["collected"] is True and collected["status"] == "CANCELLED"

    # server stage limits (_advance_ai) on PostgreSQL: unclaimed, unstarted, overrunning
    ai_enqueue(pg, "ai_unclaimed", "agt_ai")
    clock.advance(jobs.AI_CLAIM_WAIT_SECONDS + 1)
    svc.advance_ai_jobs()
    assert row(pg, "ai_unclaimed")["status"] == "CANCELLED"
    assert row(pg, "ai_unclaimed")["end_reason"] == "not_claimed"

    ai_enqueue(pg, "ai_run", "agt_ai", timeout_seconds=1)
    run_job = claim(pg, a).json()["job"]
    assert run_job["job_id"] == "ai_run"
    pg["client"].post(f"{URL}/ai_run/1/started", headers=a, json={"lease_token": run_job["lease_token"]})
    clock.advance(1 + jobs.AI_RUN_GRACE_SECONDS + 1)
    svc.advance_ai_jobs()
    over = row(pg, "ai_run")
    assert over["status"] == "STARTED" and over["cancel_requested"] is True
    clock.advance(jobs.AI_CANCEL_GRACE_SECONDS + 1)
    svc.advance_ai_jobs()
    expired = row(pg, "ai_run")
    assert expired["status"] == "FAILED" and expired["terminal_cause"] == jobs.CAUSE_SERVER_EXPIRED
    assert expired["end_reason"] == "remote_termination_unconfirmed"

    # supervisor / recovery reads run on PostgreSQL too
    assert svc.sweep_orphaned_ai_jobs(is_live_owner=lambda owner: True) >= 0
    assert {r["ai_run_id"] for r in svc.unsettled_ai_run_ids()} == {"run_ai_run"}
    assert svc.mark_fenced(ai_run_id="run_ai_run") == 1
    assert svc.unsettled_ai_run_ids() == []
