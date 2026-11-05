"""Remote Agent Job claim races across INDEPENDENT PostgreSQL sessions (opt-in; flowgate.v02.0135 T#1 / T 3-4).

test_agent_jobs_postgres_0135.py runs the real route + service on PostgreSQL, but on one connection with session
TEMP tables, so it cannot show what PostgreSQL's row locks and READ COMMITTED re-checks do when two sessions race
for one Job. This file does: it builds the Agent tables (from the real PostgreSQL migration files 121/124/170/172)
in a throw-away schema ``fg_t0135_race_<random>`` and gives every racer its own psycopg2 connection (its own
``FlowGateStore`` / ``AgentJobService`` / transaction), as separate API requests on the server would have.
The schema is dropped at the end. No user Agent, Job queue or other schema is touched.

It SKIPS unless ``FLOWGATE_PG_TEST_DSN`` is set, and skips again when that account may not ``CREATE SCHEMA``::

    # PowerShell, from server/ (a disposable database: the account needs CREATE on it)
    $env:FLOWGATE_PG_TEST_DSN = "postgresql://<user>:<password>@<host>:<port>/<db>"
    python -m pytest tests/modules/flow_gate/test_agent_jobs_postgres_race_0135.py -v -rs

Checked (each over many rounds, with the racers held at a barrier right after reading the claim candidates so
every one of them really issues its conditional UPDATE against the same NEW row):
- one NEW Job, N Agents claiming at once: exactly one winner; the row's lease owner / token are the winner's;
  every other Agent gets nothing (HTTP 204), none an error.
- M Jobs, N Agents polling at once until the queue is empty: every Job delivered exactly once, none lost.
- an expired legacy lease, N Agents at once: re-queued and handed to exactly one Agent.
- AI-run Job claim vs cancel: either the cancel wins before the claim (CANCELLED, never delivered) or the claim
  wins and the cancel becomes a stop request on that lease — never both, never neither; forced in both orders.
- claims while several AI-run Jobs pass their stage limits at once: no deadlock / serialization error.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import uuid
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

from psycopg2.extras import RealDictCursor  # noqa: E402

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import dialect as _dialect  # noqa: E402
from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services.agent_registry import digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PG_MIG = ROOT / "sql" / "migrations" / "postgres"
T0 = datetime(2026, 11, 5, 5, 0, 0, tzinfo=timezone.utc)
ROUNDS = int(os.environ.get("FLOWGATE_PG_RACE_ROUNDS", "25"))
CANDIDATES_SQL = "FROM agent_jobs j JOIN agents a"
CLAIM_UPDATE_SQL = "SET status='CLAIMED'"


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
    """sqloader-shaped backend over ONE psycopg2 connection (the production store's ``_db``)."""

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


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


class Race:
    """One isolated schema; ``session()`` = an independent PostgreSQL session with its own service."""

    def __init__(self, schema):
        self.schema = schema
        self.conns = []

    def connect(self):
        conn = psycopg2.connect(DSN, connect_timeout=10, options=f"-c search_path={self.schema}")
        self.conns.append(conn)
        return conn

    def session(self):
        store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
        store._db, store._sq = PgDB(self.connect()), None
        return store, jobs.AgentJobService(store)

    def end_round(self):
        """Closes the racers' sessions of a round (the test's own first session stays)."""
        keep, self.conns = self.conns[:1], self.conns[1:]
        self.close()
        self.conns = keep

    def close(self):
        for conn in self.conns:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass


@pytest.fixture
def race(monkeypatch):
    admin = psycopg2.connect(DSN, connect_timeout=10)
    admin.autocommit = True
    schema = "fg_t0135_race_" + uuid.uuid4().hex[:12]
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    except psycopg2.Error as exc:
        admin.close()
        pytest.skip(f"this account may not CREATE SCHEMA ({exc.pgcode}); a cross-session race needs a writable schema")
    holder = Race(schema)
    try:
        with admin.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute("""CREATE TABLE users(user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                           is_active INTEGER NOT NULL DEFAULT 1, is_admin INTEGER NOT NULL DEFAULT 0)""")
            cur.execute("CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY)")
            for name in ("121_agent_registry.sql", "124_agent_jobs.sql",
                         "170_personal_agents.sql", "172_agent_job_ai_run_link.sql"):
                cur.execute((PG_MIG / name).read_text(encoding="utf-8"))
            cur.execute("""SELECT table_schema, data_type FROM information_schema.columns
                           WHERE table_name='agent_jobs' AND column_name='cancel_requested'
                           AND table_schema=%s""", [schema])
            assert cur.fetchone() == (schema, "boolean")  # the production column type, in the race schema only
        clock = Clock()
        monkeypatch.setattr(jobs, "_now", clock)
        holder.clock = clock
        holder.store, holder.svc = holder.session()  # the test's own session (set-up and checks)
        yield holder
    finally:
        holder.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def add_agent(race, aid):
    ts = "2026-11-05T00:00:00Z"
    race.store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,configured_ai_cli,reported_ai_cli,
           reported_ai_api,reported_storage,protocol_version,protocol_compatibility,created_at,updated_at,owner_user_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, aid, True, "remote", "agent_pull", True, True, False, False, 1, "compatible", ts, ts, None],
    )
    credential = "agc_" + aid
    race.store._execute(
        "INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) VALUES(?,?,?,?,?)",
        ["cred_" + aid, aid, digest("agent_credential", credential), "test", ts],
    )
    return credential


def enqueue(race, job_id, **kwargs):
    return race.svc.enqueue_remote_cli(command_line="echo t0135", stdin_text="", cwd="/workspace",
                                       timeout_seconds=kwargs.pop("timeout_seconds", 120),
                                       job_id=job_id, attempt_id=kwargs.pop("attempt_id", "1"), **kwargs)


def ai_enqueue(race, job_id, agent_id):
    return enqueue(race, job_id, target_agent_id=agent_id, ai_run_id="run_" + job_id, graph_exec_id="gx_" + job_id,
                   retrieval_owner=jobs.retrieval_owner_for("run_" + job_id), ai_group_id="flowgate.test.0135",
                   ai_token_id="tok_" + job_id)


def row(race, job_id, attempt_id="1"):
    return race.store._fetch_one("SELECT * FROM agent_jobs WHERE job_id=? AND attempt_id=?", [job_id, attempt_id])


def hold_after(store, marker, before_continue, *, method="_fetch_all"):
    """Runs ``before_continue()`` right after this session executed the statement containing ``marker``."""
    original = getattr(store, method)

    def wrapped(sql, params=None):
        result = original(sql, params)
        if marker in sql:
            before_continue()
        return result

    setattr(store, method, wrapped)


def run_all(targets, timeout=30):
    results, errors = [None] * len(targets), []
    threads = []
    for i, target in enumerate(targets):
        def body(i=i, target=target):
            try:
                results[i] = target()
            except BaseException as exc:  # noqa: BLE001 -- reported below
                errors.append(repr(exc))
        threads.append(threading.Thread(target=body))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert not any(t.is_alive() for t in threads), "a racer did not finish (lock wait / deadlock)"
    assert errors == [], errors
    return results


def test_the_race_runs_in_its_own_schema_on_separate_sessions(race):
    a, b = race.connect(), race.connect()
    with a.cursor() as ca, b.cursor() as cb:
        ca.execute("SELECT pg_backend_pid(), current_schema()")
        cb.execute("SELECT pg_backend_pid(), current_schema()")
        (pa, sa), (pb, sb) = ca.fetchone(), cb.fetchone()
    assert pa != pb and sa == sb == race.schema
    for c in (a, b):
        c.rollback()


def test_one_new_job_many_agents_at_once_one_winner(race):
    agents = [add_agent(race, f"agt_{i}") for i in range(6)]
    for n in range(ROUNDS):
        job_id = f"job_one_{n}"
        enqueue(race, job_id)
        sessions = [race.session() for _ in agents]
        barrier = threading.Barrier(len(agents))
        for store, _ in sessions:  # everyone has read the NEW Job before anyone updates it
            hold_after(store, CANDIDATES_SQL, lambda: barrier.wait(10))
        results = run_all([lambda s=s, c=c: s.claim(c) for (_, s), c in zip(sessions, agents)])
        won = [(i, r["job"]) for i, r in enumerate(results) if r]
        assert len(won) == 1, (n, results)
        winner, job = won[0]
        stored = row(race, job_id)
        assert stored["status"] == "CLAIMED"
        assert stored["lease_owner_agent_id"] == f"agt_{winner}" and stored["lease_token"] == job["lease_token"]
        assert all(r is None for i, r in enumerate(results) if i != winner)  # the others: 204, no error
        race.store._execute("UPDATE agent_jobs SET status='DONE',lease_owner_agent_id=NULL,lease_token=NULL,"
                            "lease_expires_at=NULL WHERE job_id=?", [job_id])
        race.end_round()


def test_many_jobs_many_agents_each_delivered_once(race):
    agents = [add_agent(race, f"agt_{i}") for i in range(5)]
    job_ids = [f"job_many_{n}" for n in range(12)]
    for job_id in job_ids:
        enqueue(race, job_id)
    sessions = [race.session() for _ in agents]
    barrier = threading.Barrier(len(agents))

    def poll(svc, credential):
        barrier.wait(10)
        got = []
        while True:
            result = svc.claim(credential)
            if result is None:
                return got
            got.append((result["job"]["job_id"], result["job"]["lease_token"]))

    results = run_all([lambda s=s, c=c: poll(s, c) for (_, s), c in zip(sessions, agents)])
    delivered = [job for got in results for job, _ in got]
    assert sorted(delivered) == sorted(job_ids)  # all delivered, none twice
    for i, got in enumerate(results):
        for job_id, token in got:
            stored = row(race, job_id)
            assert (stored["status"], stored["lease_owner_agent_id"], stored["lease_token"]) == ("CLAIMED", f"agt_{i}", token)


def test_an_expired_legacy_lease_moves_to_exactly_one_agent(race):
    first = add_agent(race, "agt_first")
    others = [add_agent(race, f"agt_{i}") for i in range(5)]
    for n in range(ROUNDS // 2 or 1):
        job_id = f"job_lease_{n}"
        enqueue(race, job_id)
        old = race.svc.claim(first)["job"]
        assert old["job_id"] == job_id
        race.clock.advance(jobs.LEASE_TTL_SECONDS + 1)
        sessions = [race.session() for _ in others]
        results = run_all([lambda s=s, c=c: s.claim(c) for (_, s), c in zip(sessions, others)])
        won = [(i, r["job"]) for i, r in enumerate(results) if r]
        assert len(won) == 1 and won[0][1]["job_id"] == job_id, results
        stored = row(race, job_id)
        assert stored["lease_owner_agent_id"] == f"agt_{won[0][0]}" and stored["lease_token"] != old["lease_token"]
        race.store._execute("UPDATE agent_jobs SET status='DONE',lease_owner_agent_id=NULL,lease_token=NULL,"
                            "lease_expires_at=NULL WHERE job_id=?", [job_id])
        race.end_round()


def _check_claim_cancel(race, job_id, claim_result, cancel_result):
    stored = row(race, job_id)
    if claim_result is None:  # the cancel won: never delivered
        assert cancel_result == {"status": "CANCELLED", "cancel_requested": True, "cancelled_unclaimed": True}
        assert stored["status"] == "CANCELLED" and stored["lease_owner_agent_id"] is None
        return "cancel"
    # the claim won: the cancel is a stop request on that very lease
    assert claim_result["job"]["job_id"] == job_id
    assert cancel_result == {"status": "CLAIMED", "cancel_requested": True, "cancelled_unclaimed": False}
    assert stored["status"] == "CLAIMED" and stored["cancel_requested"] is True
    assert stored["lease_owner_agent_id"] == "agt_ai" and stored["lease_token"] == claim_result["job"]["lease_token"]
    return "claim"


def test_ai_job_claim_and_cancel_race_has_one_consistent_outcome(race):
    credential = add_agent(race, "agt_ai")
    outcomes = set()
    for n in range(ROUNDS):
        job_id = f"ai_race_{n}"
        ai_enqueue(race, job_id, "agt_ai")
        (_, claimer), (_, canceller) = race.session(), race.session()
        barrier = threading.Barrier(2)
        claim_result, cancel_result = run_all([
            lambda: (barrier.wait(10), claimer.claim(credential))[1],
            lambda: (barrier.wait(10), canceller.cancel_ai_job(job_id, "1"))[1],
        ])
        outcomes.add(_check_claim_cancel(race, job_id, claim_result, cancel_result))
        race.end_round()
    assert outcomes  # free-running rounds; both orders are forced below


def test_cancel_landing_between_the_claim_read_and_its_update_wins(race):
    credential = add_agent(race, "agt_ai")
    ai_enqueue(race, "ai_forced_cancel", "agt_ai")
    claim_store, claimer = race.session()
    _, canceller = race.session()
    read_done, cancel_done = threading.Event(), threading.Event()
    hold_after(claim_store, CANDIDATES_SQL, lambda: (read_done.set(), cancel_done.wait(10)))

    def cancel():
        assert read_done.wait(10)  # the claimer has read the NEW Job as its candidate
        result = canceller.cancel_ai_job("ai_forced_cancel", "1")
        cancel_done.set()
        return result

    claim_result, cancel_result = run_all([lambda: claimer.claim(credential), cancel])
    assert _check_claim_cancel(race, "ai_forced_cancel", claim_result, cancel_result) == "cancel"
    assert race.svc.claim(credential) is None


def test_cancel_arriving_while_the_claim_holds_the_row_waits_and_becomes_a_stop_request(race):
    credential = add_agent(race, "agt_ai")
    ai_enqueue(race, "ai_forced_claim", "agt_ai")
    claim_store, claimer = race.session()
    _, canceller = race.session()
    updated = threading.Event()
    hold_after(claim_store, CLAIM_UPDATE_SQL, lambda: (updated.set(), time.sleep(0.6)), method="_execute_affected")
    waited = {}

    def cancel():
        assert updated.wait(10)  # the claim's UPDATE ran; its transaction is still open (row locked)
        started = time.monotonic()
        result = canceller.cancel_ai_job("ai_forced_claim", "1")
        waited["s"] = time.monotonic() - started
        return result

    claim_result, cancel_result = run_all([lambda: claimer.claim(credential), cancel])
    assert waited["s"] >= 0.3  # PostgreSQL made the cancel wait for the claim's row lock
    assert _check_claim_cancel(race, "ai_forced_claim", claim_result, cancel_result) == "claim"
    renewed = race.svc.renew(credential, "ai_forced_claim", "1", claim_result["job"]["lease_token"])
    assert renewed["cancel_requested"] is True  # the Agent learns to stop on its next renew


def test_legacy_cancel_and_claim_race_never_loses_the_request(race):
    agents = [add_agent(race, f"agt_{i}") for i in range(3)]
    for n in range(ROUNDS):
        job_id = f"legacy_cancel_{n}"
        enqueue(race, job_id)
        sessions = [race.session() for _ in agents]
        _, canceller = race.session()
        barrier = threading.Barrier(len(agents) + 1)
        targets = [lambda s=s, c=c: (barrier.wait(10), s.claim(c))[1] for (_, s), c in zip(sessions, agents)]
        targets.append(lambda: (barrier.wait(10), canceller.request_cancel(job_id, "1"))[1])
        *claims, cancelled = run_all(targets)
        won = [r for r in claims if r]
        assert len(won) == 1  # a legacy Job is still delivered once; the Agent is told to stop
        stored = row(race, job_id)
        assert stored["status"] == "CLAIMED" and stored["cancel_requested"] is True
        assert cancelled["cancel_requested"] is True
        owner = stored["lease_owner_agent_id"]
        assert race.svc.renew("agc_" + owner, job_id, "1", stored["lease_token"])["cancel_requested"] is True
        race.store._execute("UPDATE agent_jobs SET status='DONE',lease_owner_agent_id=NULL,lease_token=NULL,"
                            "lease_expires_at=NULL WHERE job_id=?", [job_id])
        race.end_round()


def test_claims_while_ai_jobs_pass_their_stage_limits_do_not_deadlock(race):
    """Every claim first runs _reclaim/_advance_ai (multi-row UPDATEs); racing them must not fail on PostgreSQL."""
    agents = [add_agent(race, f"agt_{i}") for i in range(6)]
    for n in range(ROUNDS // 2 or 1):
        for k in range(8):
            ai_enqueue(race, f"ai_stage_{n}_{k}", "agt_0")
        for k in range(4):
            enqueue(race, f"legacy_stage_{n}_{k}")
        race.clock.advance(jobs.AI_CLAIM_WAIT_SECONDS + 1)  # every NEW AI Job is now past its claim limit
        sessions = [race.session() for _ in agents]
        barrier = threading.Barrier(len(agents))
        results = run_all([lambda s=s, c=c: (barrier.wait(10), s.claim(c))[1] for (_, s), c in zip(sessions, agents)])
        delivered = [r["job"]["job_id"] for r in results if r]
        assert len(delivered) == len(set(delivered)) == 4  # the legacy Jobs, each once
        assert all(j.startswith("legacy_stage_") for j in delivered)
        states = {r["status"] for r in race.store._fetch_all(
            "SELECT status FROM agent_jobs WHERE job_id LIKE ?", [f"ai_stage_{n}_%"])}
        assert states == {"CANCELLED"}  # past their claim limit: settled once, never delivered
        race.store._execute("UPDATE agent_jobs SET status='DONE',lease_owner_agent_id=NULL,lease_token=NULL,"
                            "lease_expires_at=NULL WHERE job_id LIKE ?", [f"legacy_stage_{n}_%"])
        race.end_round()
