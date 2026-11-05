"""PostgreSQL BOOLEAN columns of the Agent tables are never written or compared as integers.

flowgate.v02.0135 T#1. On PostgreSQL ``agent_jobs.cancel_requested`` / ``stdout_truncated`` /
``stderr_truncated`` (and the ``agents`` capability flags) are BOOLEAN; on SQLite they are
INTEGER. ``cancel_requested=0`` is fine on SQLite and a parse error on PostgreSQL
(``operator does not exist: boolean = integer``) -- and because ``claim()`` always runs
``_reclaim() -> _advance_ai()`` first, every claim answered HTTP 500 even on an empty queue.

The live PostgreSQL proof is the opt-in test_agent_jobs_postgres_0135.py. This file runs
everywhere (SQLite only) and pins the contract that makes it hold:

1. the BOOLEAN column list is read from the PostgreSQL migrations, not hard-coded;
2. no SQL text in the Agent services compares / assigns / inserts an integer literal into
   one of those columns;
3. every statement the service actually executes during a full lifecycle (claim on an
   empty queue, enqueue, claim, renew, started, report, cancel, AI-run stage limits,
   sweep) -- before and after ``dialect.translate(..., POSTGRESQL)`` -- obeys the same
   rule, and the report binds the truncation flags as Python ``bool``.
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

from modules.flow_gate.db import dialect as _dialect  # noqa: E402
from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services.agent_registry import digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PG_MIG = ROOT / "sql" / "migrations" / "postgres"
SERVICES = ROOT / "modules" / "flow_gate" / "services"
T0 = datetime(2026, 11, 5, 5, 0, 0, tzinfo=timezone.utc)


def _pg_boolean_columns():
    cols = set()
    for name in ("121_agent_registry.sql", "124_agent_jobs.sql", "170_personal_agents.sql",
                 "172_agent_job_ai_run_link.sql"):
        cols |= set(re.findall(r"(\w+)\s+BOOLEAN\b", (PG_MIG / name).read_text(encoding="utf-8")))
    return cols


BOOL_COLS = _pg_boolean_columns()
_COMPARE = re.compile(r"\b(%s)\s*(=|<>|!=)\s*-?\d+\b" % "|".join(sorted(BOOL_COLS)))
_INSERT = re.compile(r"INSERT\s+INTO\s+(agent_jobs|agents)\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.I | re.S)


def boolean_violations(sql):
    found = [m.group(0) for m in _COMPARE.finditer(sql)]
    for match in _INSERT.finditer(sql):
        columns = [c.strip() for c in match.group(2).split(",")]
        values = [v.strip() for v in match.group(3).split(",")]
        assert len(columns) == len(values), match.group(0)
        found += [f"{c} <- {v}" for c, v in zip(columns, values)
                  if c in BOOL_COLS and v.upper() not in ("TRUE", "FALSE", "?")]
    return found


def test_boolean_column_list_comes_from_the_postgres_schema():
    assert {"cancel_requested", "stdout_truncated", "stderr_truncated", "enabled",
            "configured_ai_cli", "reported_ai_cli"} <= BOOL_COLS


def test_the_checker_catches_the_shipped_defect_shapes():
    assert boolean_violations("UPDATE agent_jobs SET cancel_requested=1 WHERE cancel_requested=0")
    assert boolean_violations("INSERT INTO agent_jobs(job_id,stdout_truncated) VALUES(?,0)")
    assert not boolean_violations("UPDATE agent_jobs SET cancel_requested=TRUE WHERE cancel_requested=FALSE")
    assert not boolean_violations("INSERT INTO agent_jobs(job_id,stdout_truncated) VALUES(?,FALSE)")


@pytest.mark.parametrize("module", ["agent_jobs.py", "agent_registry.py"])
def test_service_source_has_no_integer_boolean_sql(module):
    source = (SERVICES / module).read_text(encoding="utf-8")
    assert boolean_violations(source) == []


class RecordingStore:
    """SQLite store (same shape as test_agent_jobs_0636.Store) that records every statement."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self.seen = []

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

    def _run(self, sql, params):
        self.seen.append((sql, list(params or [])))
        return self.db.execute(sql, params or [])

    def _execute(self, sql, params=None):
        return self._run(sql, params)

    def _execute_affected(self, sql, params=None):
        return self._run(sql, params).rowcount

    def _fetch_one(self, sql, params=None):
        cur = self._run(sql, params)
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    def _fetch_all(self, sql, params=None):
        cur = self._run(sql, params)
        return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]


@pytest.fixture
def recorded(migrated_sqlite_db, monkeypatch):
    clock = {"now": T0}
    monkeypatch.setattr(jobs, "_now", lambda: clock["now"])
    store = RecordingStore(str(migrated_sqlite_db("agent_jobs_bool_0135.sqlite")))
    ts = "2026-11-05T00:00:00Z"
    store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,configured_ai_cli,
           reported_ai_cli,reported_ai_api,reported_storage,protocol_version,protocol_compatibility,
           created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ["agt_a", "agt_a", True, "remote", "agent_pull", True, True, False, False, 1, "compatible", ts, ts])
    store._execute("INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) "
                   "VALUES(?,?,?,?,?)", ["cred_a", "agt_a", digest("agent_credential", "agc_a"), "test", ts])
    store.seen.clear()
    yield store, jobs.AgentJobService(store), clock
    store.db.close()


def _result(status="DONE", **kw):
    value = {"status": status, "exit_code": 0 if status == "DONE" else None, "stdout": "o", "stderr": "",
             "stdout_truncated": True, "stderr_truncated": False, "diagnostics": None, "end_reason": None,
             "duration_seconds": 0.1, "reported_at": "2026-11-05T05:00:01Z"}
    value.update(kw)
    return value


def _ai(svc, job_id, timeout=120):
    return svc.enqueue_remote_cli(command_line="codex exec", stdin_text="x", cwd="/w", timeout_seconds=timeout,
                                  job_id=job_id, attempt_id="1", target_agent_id="agt_a",
                                  ai_run_id="run_" + job_id, graph_exec_id="gx", ai_group_id="g",
                                  ai_token_id="t", retrieval_owner=jobs.retrieval_owner_for("run_" + job_id))


def test_every_executed_agent_job_statement_is_postgres_boolean_safe(recorded):
    store, svc, clock = recorded
    cred = "agc_a"
    assert svc.claim(cred) is None  # idle claim (the 500 path on PostgreSQL)
    svc.enqueue_remote_cli(command_line="codex exec", stdin_text="x", cwd="/w", timeout_seconds=30,
                           job_id="legacy", attempt_id="1")
    job = svc.claim(cred)["job"]
    assert job["cancel_requested"] is False
    svc.renew(cred, "legacy", "1", job["lease_token"])
    svc.started(cred, "legacy", "1", job["lease_token"])
    svc.report(cred, "legacy", "1", job["lease_token"], _result())
    svc.enqueue_remote_cli(command_line="codex exec", stdin_text="x", cwd="/w", timeout_seconds=30,
                           job_id="legacy", attempt_id="2")
    assert svc.request_cancel("legacy", "2")["cancel_requested"] is True

    _ai(svc, "ai_pre")
    assert svc.cancel_ai_job("ai_pre", "1")["cancelled_unclaimed"] is True
    _ai(svc, "ai_run", timeout=1)
    ai_job = svc.claim(cred)["job"]
    assert ai_job["job_id"] == "legacy" or ai_job["job_id"] == "ai_run"
    while ai_job["job_id"] != "ai_run":  # the cancelled legacy attempt is still NEW (legacy rules)
        ai_job = svc.claim(cred)["job"]
    svc.started(cred, "ai_run", "1", ai_job["lease_token"])
    clock["now"] = T0 + timedelta(seconds=63)
    svc.advance_ai_jobs()
    assert svc.get_ai_job("ai_run", "1")["cancel_requested"] in (1, True)
    assert svc.cancel_ai_job("ai_run", "1")["cancel_requested"] is True
    svc.sweep_orphaned_ai_jobs()
    clock["now"] = T0 + timedelta(seconds=400)
    svc.advance_ai_jobs()
    assert svc.get_ai_job("ai_run", "1")["status"] == "FAILED"

    statements = [(sql, params) for sql, params in store.seen if "agent_jobs" in sql]
    assert len(statements) > 20
    for sql, _ in statements:
        assert boolean_violations(sql) == [], sql
        assert boolean_violations(_dialect.translate(sql, _dialect.POSTGRESQL)) == [], sql
    report = [p for sql, p in statements if "stdout_truncated=?" in sql]
    assert report and all(isinstance(p[4], bool) and isinstance(p[5], bool) for p in report)
