"""flowgate.v02.0129 T#2 -- remote CLI connections run only as a Job on their target Agent.

Real SQLite schema (full migration chain), the real AgentJobService/AgentService and the
real provider boundary (`provider_cli._cli_execute`, `worker._execute_provider_chain`).
A pull Agent is simulated in a thread with the public queue calls (claim/started/report).
Only the server-local process boundary (`_execute_local`), the Graph launch hook and the
invocation journal are replaced, so nothing here starts a real CLI or touches a real run.

Job creation is gated by ``provider_remote.AGENT_WORKDIR_CONTRACT_VERIFIED``. The product
default is open since flowgate.v02.0131 CH0004 (operator instruction); the fixture opens
it explicitly so the lifecycle (D0005 (가)~(라)) is exercised regardless, one test pins
the open default and one test keeps the closed-gate behaviour covered.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER", "test-only-pepper")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.services import agent_jobs as jobs  # noqa: E402
from modules.flow_gate.services import graph_execution_hooks  # noqa: E402
from modules.flow_gate.services.agent_registry import AgentError, digest  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_cli, provider_remote, terminal, worker  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission, remote_supervisor  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from flowgate.ai_roles import invocations as role_invocations  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
MIG = ROOT / "sql" / "migrations"
MIGRATION = "172_agent_job_ai_run_link.sql"
TOKEN = "fgt_secret_token_0129_value"
COMMAND = "claude --secret-flag-0129 -p"
SCRATCH = "/srv/flowgate/scratch/run-0129"
TOKEN_SCRATCH = "/srv/flowgate/scratch/token-0129"
BASE = "https://flowgate.example.test/flowgate/api/v1"
# flowgate.v02.0135 CH0006: the Agent's remote working directory setting.
WORKDIR = "/tmp/flowgate/${AGENT_JOB_ID}"


class Store:
    """Single SQLite connection; every call is serialized so threads can share it."""

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
        with self.lock:
            return self.db.execute(sql, params or [])

    def _execute_affected(self, sql, params=None):
        with self.lock:
            return self.db.execute(sql, params or []).rowcount

    def _fetch_one(self, sql, params=None):
        with self.lock:
            cur = self.db.execute(sql, params or [])
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None

    def _fetch_all(self, sql, params=None):
        with self.lock:
            cur = self.db.execute(sql, params or [])
            return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]

    def table_exists(self, table):
        with self.lock:
            return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                   [table]).fetchone() is not None

    def column_exists(self, table, column):
        with self.lock:
            return any(row[1] == column for row in self.db.execute(f"PRAGMA table_info({table})"))


class Clock:
    """Queue time (agent_jobs._now) that a test can move forward."""

    def __init__(self):
        self.offset = timedelta()

    def __call__(self):
        return datetime.now(timezone.utc) + self.offset

    def jump(self, seconds):
        self.offset += timedelta(seconds=seconds)


def _real_stamp(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_agent(store, aid, *, capable=True, enabled=True, online=True, owner=None, workdir=WORKDIR):
    ts = "2026-01-01T00:00:00Z"
    store._execute(
        """INSERT INTO agents(agent_id,name,enabled,location,connection_mode,
           configured_ai_cli,reported_ai_cli,reported_ai_api,reported_storage,
           protocol_version,protocol_compatibility,created_at,updated_at,owner_user_id,remote_workdir)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [aid, aid, enabled, "remote", "agent_pull", capable, capable, 0, 0, 1, "compatible", ts, ts, owner, workdir],
    )
    credential = "agc_" + aid
    store._execute(
        """INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at,last_heartbeat_at)
           VALUES(?,?,?,?,?,?)""",
        ["cred_" + aid, aid, digest("agent_credential", credential), "test", ts,
         _real_stamp() if online else "2026-01-01T00:00:00Z"],
    )
    return credential


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch):
    store = Store(str(migrated_sqlite_db("remote_cli_routing_0129.sqlite")))
    clock = Clock()
    monkeypatch.setattr(jobs, "_now", clock)
    monkeypatch.setattr(jobs, "get_store", lambda: store)
    monkeypatch.setattr(db_connection, "get_store", lambda: store)
    monkeypatch.setattr(provider_remote, "REMOTE_POLL_SEC", 0.02)
    # Stand-in for the verified Agent working-directory contract (see module docstring).
    monkeypatch.setattr(provider_remote, "AGENT_WORKDIR_CONTRACT_VERIFIED", True)
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "_runs", {})
    launches = []

    def hook(action, *args, **kwargs):
        if action == "launch":
            launches.append(store._fetch_one("SELECT COUNT(*) AS n FROM agent_jobs")["n"])
            return env.launch_result
        return None

    monkeypatch.setattr(graph_execution_hooks, "call", hook)
    local_calls = []

    def local(*args, **kwargs):
        local_calls.append(kwargs.get("command"))
        return NS(proc=NS(returncode=0), launched_mono=time.monotonic()), (b"local answer", b"", False, None)

    monkeypatch.setattr(provider_cli, "_execute_local", local)
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch",
                        lambda *a: ({"effective_command": "x", "shell_kind": "posix_sh", "spawn_cwd": "/tmp"}, None))
    for name in ("begin", "finish", "cli_prepared", "cli_outcome"):
        monkeypatch.setattr(role_invocations, name, lambda *a, **k: None)
    monkeypatch.setattr(worker, "_provider_prompt", lambda run, provider, prompt: prompt)
    monkeypatch.setattr(ai_invoke_service, "_broadcast", lambda *a, **k: None)
    from modules.flow_gate.services.ai_invoke import oracle
    monkeypatch.setattr(oracle, "_work_landed", lambda run: False)
    revoked = []
    from modules.flow_gate.services import token_service
    monkeypatch.setattr(token_service, "revoke", lambda token_id, reason="": revoked.append((token_id, reason)))
    env = NS(store=store, clock=clock, svc=jobs.AgentJobService(store), launches=launches,
             local_calls=local_calls, launch_result=None, revoked=revoked, agents=[],
             creds={aid: add_agent(store, aid) for aid in ("agt_a", "agt_b")})
    yield env
    for agent in env.agents:
        agent.stop_event.set()
        agent.join(5)
    store.db.close()


def remote(target="agt_a", **kw):
    return {"id": "aip_remote", "name": "Remote", "exec_type": "cli", "kind": "claude",
            "cli_command": COMMAND, "exec_location": "remote", "target_agent_id": target, **kw}


def local_provider(**kw):
    return {"id": "aip_local", "name": "Local", "exec_type": "cli", "kind": "claude",
            "cli_command": "claude -p", "exec_location": "local", "target_agent_id": None, **kw}


def make_run(**kw):
    run = {"run_id": "run_0129_" + os.urandom(4).hex(), "project_id": None, "graph_exec_id": None,
           "group_id": None, "api_base_url": BASE, "cancel_event": threading.Event(),
           "started_mono": time.monotonic(), "timed_out": False, "token_id": None,
           "scratch_dir": SCRATCH, "token_scratch_dir": TOKEN_SCRATCH, "raw_token": TOKEN,
           "fallback_history": [], "attempt_no": 1}
    run.update(kw)
    return run


def prompt_with_secrets():
    return f"Do the work. Authorization: Bearer {TOKEN}\nScratch: {TOKEN_SCRATCH}/note.json"


def jobs_rows(env):
    return env.store._fetch_all("SELECT * FROM agent_jobs ORDER BY created_at")


class FakeAgent(threading.Thread):
    """A pull Agent using only the public queue calls."""

    def __init__(self, env, aid, behaviour=None, *, deadline=20.0):
        super().__init__(daemon=True)
        self.env, self.aid, self.behaviour = env, aid, behaviour
        self.received, self.polls, self.stop_event = [], 0, threading.Event()
        self.claimed = threading.Event()
        self.deadline = time.monotonic() + deadline
        env.agents.append(self)

    def run(self):
        svc, cred = self.env.svc, self.env.creds[self.aid]
        while not self.stop_event.is_set() and time.monotonic() < self.deadline:
            got = svc.claim(cred)
            self.polls += 1
            if got:
                job = got["job"]
                self.received.append(job)
                self.claimed.set()
                if self.behaviour:
                    self.behaviour(svc, cred, job)
                return
            time.sleep(0.01)


def report(status="DONE", **kw):
    value = {"status": status, "exit_code": 0 if status == "DONE" else None, "stdout": "final answer",
             "stderr": "", "stdout_truncated": False, "stderr_truncated": False, "diagnostics": None,
             "end_reason": None, "duration_seconds": 42.0, "reported_at": "2026-01-01T00:00:01Z"}
    value.update(kw)
    return value


def completes(stdout="final answer"):
    def behaviour(svc, cred, job):
        svc.started(cred, job["job_id"], job["attempt_id"], job["lease_token"])
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"], report(stdout=stdout))
    return behaviour


# ── migration ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_migration_172_is_additive_and_nullable(dialect):
    sql = re.sub(r"--[^\n]*", "", (MIG / dialect / MIGRATION).read_text(encoding="utf-8")).lower()
    alters = [line for line in sql.splitlines() if line.startswith("alter table")]
    for column in ("ai_run_id", "graph_exec_id", "ai_group_id", "ai_token_id", "cancel_requested_at",
                   "terminal_cause", "retrieval_state", "retrieval_owner", "retrieval_deadline_at",
                   "fenced_at", "scrubbed_at"):
        assert any(f"alter table agent_jobs add column {column} " in line for line in alters), column
    assert any("alter table ai_invoke_runs add column remote_job_json " in line for line in alters)
    for line in alters:                       # every added column is nullable, no backfill default
        assert "not null" not in line and "default" not in line
    assert not re.search(r"\b(update|insert|delete from|drop)\b", sql)
    assert "create index idx_agent_jobs_ai_run on agent_jobs(ai_run_id)" in sql
    assert "create table ai_remote_recovery_holds" in sql


def test_sqlite_migration_keeps_existing_jobs_unlinked():
    db = sqlite3.connect(":memory:")
    db.executescript((MIG / "sqlite" / "124_agent_jobs.sql").read_text(encoding="utf-8"))
    db.execute("CREATE TABLE ai_invoke_runs (run_id TEXT PRIMARY KEY)")
    db.execute("""INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,
                  timeout_seconds,status,payload_digest,created_at,updated_at)
                  VALUES('j','a','remote_cli','c','s','/w',1,'NEW','d','t','t')""")
    db.execute("INSERT INTO ai_invoke_runs(run_id) VALUES('r')")
    db.executescript((MIG / "sqlite" / MIGRATION).read_text(encoding="utf-8"))
    row = db.execute("SELECT ai_run_id,retrieval_state,scrubbed_at,fenced_at,ai_token_id,stdin_text "
                     "FROM agent_jobs").fetchone()
    assert row == (None, None, None, None, None, "s")
    assert db.execute("SELECT remote_job_json FROM ai_invoke_runs").fetchone() == (None,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE agent_jobs SET retrieval_state='bogus'")
    db.close()


# ── local CLI is unchanged ───────────────────────────────────────────────────

def test_local_cli_still_uses_the_local_process_boundary(env):
    run = make_run(api_base_url="")
    status, detail = provider_cli._cli_execute(local_provider(), "hello", run)
    assert (status, detail) == ("started_ok", None)
    assert env.local_calls == ["x"] and jobs_rows(env) == []
    assert run["last_message"] == "local answer" and "remote_job" not in run


def test_local_prompt_unchanged_remote_prompt_gets_the_remote_contract(monkeypatch):
    from modules.flow_gate.services import node_title_hooks
    monkeypatch.setattr(node_title_hooks, "provider_prompt", lambda prompt, *a: prompt)
    run = {"locale": "en", "action_scope": "new"}
    assert worker._provider_prompt(run, local_provider(), "P") == "P"
    text = worker._provider_prompt(run, remote(), "P")
    assert text.startswith("P\n\n## Remote CLI execution contract")
    assert "current working directory" in text and "do not use doc_path" in text
    for locale in ("ko", "ja"):
        assert worker._provider_prompt({"locale": locale}, remote(), "P") == "P\n\n" + worker._REMOTE_CLI_TRANSPORT[locale]


# ── remote runs only on the target Agent and its result becomes the AI result ──

def test_remote_runs_only_on_target_agent_and_result_is_reflected(env, caplog):
    caplog.set_level(logging.DEBUG)
    other = FakeAgent(env, "agt_b")
    target = FakeAgent(env, "agt_a", completes("line one\n\nfinal answer"))
    other.start()
    target.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), prompt_with_secrets(), run)
    other.stop_event.set()
    target.join(5)
    other.join(5)
    assert (status, detail) == ("started_ok", None)
    assert env.local_calls == [] and env.launches == [0]          # launch approved before any Job
    assert other.received == [] and other.polls > 0                # the other Agent never got it
    assert len(target.received) == 1
    job = target.received[0]
    # flowgate.v02.0135 CH0006: the Agent's setting with ${AGENT_JOB_ID} replaced, no fixed path.
    assert job["cwd"] == f"/tmp/flowgate/{job['job_id']}"
    assert run["remote_job"]["workdir"] == job["cwd"]
    assert TOKEN in job["stdin_text"] and TOKEN_SCRATCH not in job["stdin_text"]
    assert job["command_line"] == COMMAND
    assert run["last_message"] == "line one\n\nfinal answer" and run["exit_code"] == 0
    assert run["stdout_tail"].endswith("final answer") and not run.get("timed_out")
    info = run["remote_job"]
    assert info["target_agent_id"] == info["claimed_agent_id"] == "agt_a" and info["job_id"] == job["job_id"]
    [row] = jobs_rows(env)
    assert (row["status"], row["lease_owner_agent_id"], row["target_agent_id"]) == ("DONE", "agt_a", "agt_a")
    assert row["ai_run_id"] == run["run_id"] and row["retrieval_state"] == "collected"
    assert (row["stdin_text"], row["command_line"], row["stdout"], row["stderr"]) == ("", "", None, None)
    assert row["scrubbed_at"] and row["terminal_cause"] == "agent_report"
    assert run.get("remote_job_active") is False and terminal.provider_stopped(run)
    assert TOKEN not in caplog.text and COMMAND not in caplog.text


@pytest.mark.parametrize("setup,code,reason", [
    (lambda s: None, "remote_agent_unassigned", "no_target_agent"),
    (lambda s: s._execute("UPDATE agent_credentials SET last_heartbeat_at='2026-01-01T00:00:00Z' WHERE agent_id='agt_a'"),
     "remote_agent_offline", "agent_offline"),
    (lambda s: s._execute("UPDATE agents SET enabled=0 WHERE agent_id='agt_a'"), "remote_agent_unavailable", "agent_disabled"),
    (lambda s: s._execute("UPDATE agent_credentials SET revoked_at='2026-01-01T00:00:00Z' WHERE agent_id='agt_a'"),
     "remote_agent_unavailable", "agent_revoked"),
    (lambda s: s._execute("UPDATE agents SET reported_ai_cli=0 WHERE agent_id='agt_a'"),
     "remote_agent_unavailable", "ai_cli_capability_missing"),
])
def test_unusable_target_fails_clearly_without_job_launch_or_local_run(env, setup, code, reason):
    setup(env.store)
    provider = remote(target=None) if code == "remote_agent_unassigned" else remote()
    other = FakeAgent(env, "agt_b", deadline=0.5)
    other.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(provider, prompt_with_secrets(), run)
    other.join(2)
    assert status == "spawn_failed" and detail == f"{code}: {reason}"
    assert run["remote_final_failure"]["code"] == code
    assert jobs_rows(env) == [] and env.launches == [] and env.local_calls == [] and other.received == []
    assert TOKEN not in detail and COMMAND not in detail


def test_deleted_or_foreign_agent_is_one_reason(env):
    for target in ("agt_missing", "agt_personal"):
        if target == "agt_personal":
            # A personal Agent (seeded reserved user) is not usable by a shared connection.
            add_agent(env.store, target, owner="u-system")
        run = make_run()
        status, detail = provider_cli._cli_execute(remote(target=target), "p", run)
        assert (status, detail) == ("spawn_failed", "remote_agent_unavailable: agent_not_found")
    assert jobs_rows(env) == [] and env.local_calls == []


def test_loopback_api_base_is_refused_before_any_job(env):
    for base in ("http://127.0.0.1:8092/flowgate/api/v1", "http://localhost/x", "", "http://[::1]/x"):
        run = make_run(api_base_url=base)
        status, detail = provider_cli._cli_execute(remote(), "p", run)
        assert status == "spawn_failed" and detail.startswith("remote_api_base_unreachable")
    assert jobs_rows(env) == [] and env.launches == []


def test_launch_refusal_creates_no_job(env):
    env.launch_result = False
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    assert (status, detail) == ("spawn_failed", "Graph execution lease refused")
    assert env.launches == [0] and jobs_rows(env) == []


# ── stage limits, cancel, rejection, unconfirmed end ─────────────────────────

def test_unclaimed_job_times_out_and_can_never_be_claimed_later(env):
    def advance():
        while not done.is_set():
            env.clock.jump(30)
            time.sleep(0.03)
    done = threading.Event()
    mover = threading.Thread(target=advance, daemon=True)
    mover.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), prompt_with_secrets(), run)
    done.set()
    mover.join(2)
    assert (status, detail) == ("spawn_failed", "remote_agent_no_response: not_claimed")
    [row] = jobs_rows(env)
    assert (row["status"], row["terminal_cause"], row["stdin_text"]) == ("CANCELLED", "claim_timeout", "")
    assert env.svc.claim(env.creds["agt_a"]) is None


def test_user_cancel_before_claim_wins_and_job_is_never_delivered(env):
    run = make_run()
    timer = threading.Timer(0.2, run["cancel_event"].set)
    timer.start()
    status, detail = provider_cli._cli_execute(remote(), prompt_with_secrets(), run)
    timer.join()
    assert (status, detail) == ("spawn_failed", "remote_cancelled_before_start: cancelled_before_claim")
    [row] = jobs_rows(env)
    assert row["status"] == "CANCELLED" and row["stdin_text"] == "" and row["cancel_requested"] == 1
    assert env.svc.claim(env.creds["agt_a"]) is None


def test_cancel_and_claim_race_has_exactly_one_winner(env):
    for n in range(12):
        job_id = f"job_race_{n}"
        env.svc.enqueue_remote_cli(command_line="c", stdin_text="s", cwd=f"flowgate-runs/{job_id}",
                                   timeout_seconds=30, job_id=job_id, attempt_id="a", target_agent_id="agt_a",
                                   ai_run_id="run_race", retrieval_owner=jobs.retrieval_owner_for("run_race"))
        results = {}
        barrier = threading.Barrier(2)

        def claimer():
            barrier.wait()
            results["claim"] = env.svc.claim(env.creds["agt_a"])

        def canceller():
            barrier.wait()
            results["cancel"] = env.svc.cancel_ai_job(job_id, "a")

        threads = [threading.Thread(target=claimer), threading.Thread(target=canceller)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        row = env.svc.get_ai_job(job_id, "a")
        if results["cancel"]["cancelled_unclaimed"]:
            assert results["claim"] is None and row["status"] == "CANCELLED"
        else:
            assert results["claim"]["job"]["job_id"] == job_id
            assert row["status"] == "CLAIMED" and row["cancel_requested"] == 1
            env.svc.report(env.creds["agt_a"], job_id, "a", results["claim"]["job"]["lease_token"],
                           report("CANCELLED", stdout=""))


def test_agent_rejection_is_a_final_failure(env):
    def reject(svc, cred, job):
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"],
                   report("REJECTED", stdout="", end_reason="cwd_outside_workspace"))
    agent = FakeAgent(env, "agt_a", reject)
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    job_id = run["remote_job"]["job_id"]
    assert (status, detail) == ("spawn_failed", "remote_agent_rejected: path_invalid: cwd_outside_workspace"
                                f" (workdir: /tmp/flowgate/{job_id})")
    failure = run["remote_final_failure"]
    assert failure["claimed_agent_id"] == "agt_a"
    assert (failure["cause"], failure["message"], failure["workdir"]) == (
        "path_invalid", "cwd_outside_workspace", f"/tmp/flowgate/{job_id}")


# ── flowgate.v02.0135 CH0006: remote working directory and the Agent's own failure cause ──

def _rejecting(**fields):
    def reject(svc, cred, job):
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"],
                   report("REJECTED", stdout="", **fields))
    return reject


def test_rejection_reason_in_diagnostics_reaches_the_failure_with_its_workdir(env):
    # The incident: the Agent put its cause in diagnostics and left end_reason empty.
    def reject(svc, cred, job):
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"], report(
            "REJECTED", stdout="", end_reason=None,
            diagnostics=f'workspace: "{job["cwd"]}" is invalid: does not exist'))
    agent = FakeAgent(env, "agt_a", reject)
    agent.start()
    started, run = _chain(env, [remote(), local_provider()])
    agent.join(5)
    workdir = run["remote_job"]["workdir"]
    assert started is False and env.local_calls == []
    final = run["fallback_history"][0]["remote_final"]
    assert final["code"] == "remote_agent_rejected" and final["cause"] == "workdir_missing"
    assert final["workdir"] == workdir and final["message"] == f'workspace: "{workdir}" is invalid: does not exist'
    assert run["last_message"] == f'remote_agent_rejected: workdir_missing: workspace: "{workdir}" is invalid: does not exist'
    assert "rejected" != run["last_message"].split(": ", 1)[1]


@pytest.mark.parametrize("fields, cause", [
    ({"end_reason": "workdir_create_failed", "diagnostics": "mkdir /tmp/flowgate/x: permission denied"},
     "permission_denied"),
    ({"diagnostics": "could not create working directory: disk full"}, "workdir_create_failed"),
    ({"diagnostics": "cwd escapes the workspace through a symlink"}, "path_invalid"),
    ({"end_reason": "rejected", "diagnostics": "unsupported protocol option"}, "agent_rejected"),
    ({"end_reason": "rejected"}, "agent_rejected"),
])
def test_rejection_cause_is_classified_from_the_agent_words(env, fields, cause):
    agent = FakeAgent(env, "agt_a", _rejecting(**fields))
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert status == "spawn_failed" and run["remote_final_failure"]["cause"] == cause
    assert detail.startswith(f"remote_agent_rejected: {cause}: ")


def test_a_command_not_found_is_not_reported_as_a_missing_directory():
    assert provider_remote.classify_agent_failure("claude: command not found", "agent_failed") == "agent_failed"
    assert provider_remote.classify_agent_failure("permission denied", "agent_failed") == "permission_denied"


def test_failure_text_keeps_the_path_and_drops_secrets(env):
    agent = FakeAgent(env, "agt_a", _rejecting(diagnostics=(
        f"cannot create directory /tmp/flowgate/run: Authorization: Bearer abcdef123456 token={TOKEN} key agc_secretvalue1234")))
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    message = run["remote_final_failure"]["message"]
    assert "/tmp/flowgate/run" in message and run["remote_final_failure"]["cause"] == "workdir_create_failed"
    for secret in (TOKEN, "abcdef123456", "agc_secretvalue1234"):
        assert secret not in message and secret not in detail


def test_failed_before_start_reports_the_agent_cause(env):
    def fail(svc, cred, job):
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"], report(
            "FAILED", stdout="", exit_code=None, end_reason="failed_before_start",
            diagnostics="mkdir: cannot create directory '/opt/x': Permission denied"))
    agent = FakeAgent(env, "agt_a", fail)
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert status == "spawn_failed" and detail.startswith("remote_agent_failed_before_start: permission_denied: ")
    assert "/opt/x" in run["remote_final_failure"]["message"]


def test_an_agent_without_a_remote_workdir_fails_before_any_job(env):
    env.store._execute("UPDATE agents SET remote_workdir=NULL WHERE agent_id='agt_a'")
    agent = FakeAgent(env, "agt_a", completes(), deadline=0.5)
    agent.start()
    started, run = _chain(env, [remote(), local_provider()])
    agent.join(2)
    assert started is False and jobs_rows(env) == [] and env.launches == [] and env.local_calls == []
    final = run["fallback_history"][0]["remote_final"]
    assert (final["code"], final["cause"]) == ("remote_agent_workdir_unset", "workdir_unset")
    assert run["last_message"] == "remote_agent_workdir_unset: remote_workdir_not_set"


def test_workdir_without_the_variable_is_sent_as_set():
    assert provider_remote.job_workdir("/srv/agent/work", "job_x") == "/srv/agent/work"
    assert provider_remote.job_workdir("C:\\fg\\${AGENT_JOB_ID}\\w", "job_x") == "C:\\fg\\job_x\\w"


@pytest.mark.parametrize("revoke_ok", [True, False])
def test_lost_agent_ends_unconfirmed_token_revoked_before_release(env, monkeypatch, revoke_ok):
    agent = FakeAgent(env, "agt_a", lambda svc, cred, job: svc.started(cred, job["job_id"], job["attempt_id"], job["lease_token"]))
    agent.start()

    def lose_agent():
        agent.claimed.wait(5)
        time.sleep(0.1)
        env.clock.jump(jobs.LEASE_TTL_SECONDS + 5)
    threading.Thread(target=lose_agent, daemon=True).start()
    if not revoke_ok:
        from modules.flow_gate.services import token_service
        monkeypatch.setattr(token_service, "revoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    run = make_run(token_id="tok_0129")
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert (status, detail) == ("spawn_failed", "remote_termination_unconfirmed: no_terminal_report")
    [row] = jobs_rows(env)
    assert (row["status"], row["terminal_cause"], row["scrubbed_at"] is not None) == ("FAILED", "server_expired", True)
    job = agent.received[0]
    with pytest.raises(AgentError) as late:
        env.svc.report(env.creds["agt_a"], job["job_id"], job["attempt_id"], job["lease_token"], report())
    assert late.value.code == "terminal_conflict"
    assert jobs_rows(env)[0]["stdout"] is None
    if revoke_ok:
        assert env.revoked == [("tok_0129", "remote_termination_unconfirmed")]
        assert terminal.provider_stopped(run)
    else:
        # Unfenced remote process: the run stays "alive" so cleanup parks the lease.
        assert run["remote_job_active"] is True and not terminal.provider_stopped(run)


def test_ai_job_is_never_requeued_after_lease_expiry_but_legacy_job_is(env):
    env.svc.enqueue_remote_cli(command_line="c", stdin_text="s", cwd="/w", timeout_seconds=30,
                               job_id="legacy", attempt_id="a", target_agent_id="agt_a")
    env.svc.enqueue_remote_cli(command_line="c", stdin_text="s", cwd="flowgate-runs/ai", timeout_seconds=30,
                               job_id="ai", attempt_id="a", target_agent_id="agt_a",
                               ai_run_id="run_x", retrieval_owner=jobs.retrieval_owner_for("run_x"))
    first = env.svc.claim(env.creds["agt_a"])["job"]
    second = env.svc.claim(env.creds["agt_a"])["job"]
    assert {first["job_id"], second["job_id"]} == {"legacy", "ai"}
    env.clock.jump(jobs.LEASE_TTL_SECONDS + 1)
    again = env.svc.claim(env.creds["agt_a"])
    assert again["job"]["job_id"] == "legacy"                    # unchanged legacy behaviour
    ai = env.svc.get_ai_job("ai", "a")
    assert (ai["status"], ai["terminal_cause"]) == ("FAILED", "server_expired")


def test_enqueue_of_ai_job_requires_a_target_agent(env):
    with pytest.raises(AgentError) as exc:
        env.svc.enqueue_remote_cli(command_line="c", stdin_text="s", cwd="flowgate-runs/x", timeout_seconds=3,
                                   ai_run_id="run_x", retrieval_owner="o")
    assert exc.value.code == "validation_failed"


def test_retrieval_ownership_and_orphan_sweep(env):
    owner = jobs.retrieval_owner_for("run_r")
    env.svc.enqueue_remote_cli(command_line="c", stdin_text=TOKEN, cwd="flowgate-runs/r", timeout_seconds=30,
                               job_id="r", attempt_id="a", target_agent_id="agt_a",
                               ai_run_id="run_r", retrieval_owner=owner)
    job = env.svc.claim(env.creds["agt_a"])["job"]
    assert job["stdin_text"] == TOKEN
    assert env.store._fetch_one("SELECT stdin_text FROM agent_jobs WHERE job_id='r'")["stdin_text"] == ""
    env.svc.started(env.creds["agt_a"], "r", "a", job["lease_token"])
    env.svc.report(env.creds["agt_a"], "r", "a", job["lease_token"], report(stdout="secret output"))
    stolen = env.svc.collect_ai_result("r", "a", "proc_other:run_r")
    assert stolen["collected"] is False and stolen["stdout"] is None
    assert env.svc.sweep_orphaned_ai_jobs(lambda o: o == owner) == 0      # live owner: untouched
    assert env.store._fetch_one("SELECT stdout FROM agent_jobs WHERE job_id='r'")["stdout"] == "secret output"
    assert env.svc.sweep_orphaned_ai_jobs(lambda o: False) >= 1            # restart: handed over + scrubbed
    row = env.store._fetch_one("SELECT stdout,retrieval_state,scrubbed_at FROM agent_jobs WHERE job_id='r'")
    assert row["stdout"] is None and row["retrieval_state"] == "handed_over" and row["scrubbed_at"]
    late = env.svc.collect_ai_result("r", "a", owner)
    assert late["collected"] is False and late["stdout"] is None


def test_startup_sweep_cancels_unclaimed_job_of_a_previous_process(env):
    env.svc.enqueue_remote_cli(command_line="c", stdin_text=TOKEN, cwd="flowgate-runs/o", timeout_seconds=30,
                               job_id="o", attempt_id="a", target_agent_id="agt_a",
                               ai_run_id="run_o", retrieval_owner="proc_previous:run_o")
    assert env.svc.sweep_orphaned_ai_jobs() == 1
    row = env.svc.get_ai_job("o", "a")
    assert row["status"] == "CANCELLED" and env.svc.claim(env.creds["agt_a"]) is None
    assert env.store._fetch_one("SELECT stdin_text FROM agent_jobs WHERE job_id='o'")["stdin_text"] == ""


# ── connection chain: a remote final failure stops the walk ──────────────────

def _chain(env, chain, run=None):
    run = run or make_run()
    started = worker._execute_provider_chain(run, chain, prompt_with_secrets())
    return started, run


def test_remote_failure_never_falls_through_to_local_api_or_other_agent(env):
    api = {"id": "aip_api", "name": "API", "exec_type": "api", "kind": "openai"}
    for nxt in (local_provider(), remote(target="agt_b", id="aip_remote_b"), api):
        run = make_run()
        started, run = _chain(env, [remote(target=None), nxt], run)
        assert started is False
        assert [h["provider_id"] for h in run["fallback_history"]] == ["aip_remote"]
        assert run["fallback_history"][0]["remote_final"]["code"] == "remote_agent_unassigned"
        assert run["last_message"] == "remote_agent_unassigned: no_target_agent"
        assert worker._retry_eligible(dict(run, mode="continuous", end_reason="exited")) is False
    assert env.local_calls == [] and jobs_rows(env) == [] and env.launches == []


def test_remote_fast_fail_on_target_is_not_handed_to_the_next_connection(env):
    def fail(svc, cred, job):
        svc.started(cred, job["job_id"], job["attempt_id"], job["lease_token"])
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"],
                   report("FAILED", exit_code=2, stdout="", stderr="boom", duration_seconds=1.0))
    agent = FakeAgent(env, "agt_a", fail)
    agent.start()
    started, run = _chain(env, [remote(), local_provider()])
    agent.join(5)
    assert started is False and env.local_calls == []
    assert [h["reason"] for h in run["fallback_history"]] == ["fast_fail"]
    assert run["fallback_history"][0]["remote_final"]["claimed_agent_id"] == "agt_a"


def test_local_start_failure_still_falls_back_and_then_runs_only_on_the_remote_target(env, monkeypatch):
    monkeypatch.setattr(provider_cli, "_execute_local", lambda *a, **k: (NS(proc=None), None))
    # The local half only needs to fail at start; its API-base rewrite reads server config.
    monkeypatch.setattr(provider_cli, "_canonicalize_cli_prompt", lambda prompt, base: (prompt, base))
    agent = FakeAgent(env, "agt_a", completes())
    other = FakeAgent(env, "agt_b")
    agent.start()
    other.start()
    started, run = _chain(env, [local_provider(), remote()])
    other.stop_event.set()
    agent.join(5)
    other.join(5)
    assert started is True
    assert [h["reason"] for h in run["fallback_history"]] == ["spawn_failed"]   # existing local fallback
    assert "remote_final" not in run["fallback_history"][0]
    assert run["remote_job"]["claimed_agent_id"] == "agt_a" and other.received == []
    assert run["last_message"] == "final answer"


# ── run-less CLI paths refuse remote connections ─────────────────────────────

def test_connection_test_evaluation_and_commit_suggest_refuse_remote_cli(monkeypatch):
    from modules.flow_gate.services import ai_connection_test_service as connection
    providers = [dict(remote(), id="p_remote"), dict(local_provider(), id="p_local")]
    monkeypatch.setattr(connection, "_effective", lambda project_id: {"source": "system", "providers": providers})
    with pytest.raises(connection.ConnectionTestError) as exc:
        connection.resolve_subject("flowgate", "p_remote", fingerprint=False)
    assert (exc.value.code, exc.value.status) == ("remote_cli_unsupported", 409)
    subject = connection.resolve_subject("flowgate", "p_local", fingerprint=False)
    assert subject.exec_type == "cli" and subject.cli_command


def test_settings_launch_probe_refuses_remote_cli_without_spawning(monkeypatch):
    from modules.flow_gate.services import ai_provider_probe_service as probe
    monkeypatch.setattr(probe, "_run_probe", lambda *a, **k: pytest.fail("must not launch"))
    result = probe.probe_provider({"exec_type": "cli", "kind": "claude", "cli_command": COMMAND,
                                   "exec_location": "remote"})
    assert (result["status"], result["reason"], result["launched"]) == ("skipped", "remote_cli_unsupported", False)
    assert COMMAND not in str(result)
    from modules.flow_gate.settings.routers.ai_settings import ProviderProbeIn
    assert ProviderProbeIn(exec_location="remote").model_dump()["exec_location"] == "remote"


# ══ rework (rej_01M6JN9QBKQ9VABZ) ════════════════════════════════════════════

def _register(run, **kw):
    """Put a run into this process's live registry (the fixture gives each test its own)."""
    from modules.flow_gate.services import ai_invoke_service
    run.setdefault("status", "running")
    run.update(kw)
    with ai_invoke_service._runs_lock:
        ai_invoke_service._runs[run["run_id"]] = run
    return run


def _enqueue(env, job_id, run_id, *, owner=None, stdin=TOKEN, token=None, group=None, graph=None,
             target="agt_a"):
    env.svc.enqueue_remote_cli(command_line="c", stdin_text=stdin, cwd=f"flowgate-runs/{job_id}",
                               timeout_seconds=600, job_id=job_id, attempt_id="a", target_agent_id=target,
                               ai_run_id=run_id, graph_exec_id=graph,
                               retrieval_owner=owner or jobs.retrieval_owner_for(run_id),
                               ai_group_id=group, ai_token_id=token)


def _claim(env, aid="agt_a", start=True):
    job = env.svc.claim(env.creds[aid])["job"]
    if start:
        env.svc.started(env.creds[aid], job["job_id"], job["attempt_id"], job["lease_token"])
    return job


def _report(env, job, status="DONE", aid="agt_a", **kw):
    return env.svc.report(env.creds[aid], job["job_id"], job["attempt_id"], job["lease_token"],
                          report(status, **kw))


def _row(env, job_id):
    return env.store._fetch_one("SELECT * FROM agent_jobs WHERE job_id=?", [job_id])


def waits_for_cancel(release=None, status="CANCELLED", stdout="late secret"):
    """Starts, renews like a live Agent until a cancel is requested, then reports."""
    def behaviour(svc, cred, job):
        svc.started(cred, job["job_id"], job["attempt_id"], job["lease_token"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            renewed = svc.renew(cred, job["job_id"], job["attempt_id"], job["lease_token"])
            if renewed["cancel_requested"]:
                break
            time.sleep(0.02)
        behaviour.saw_cancel = True
        if release is not None:
            release.wait(10)
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"],
                   report(status, stdout=stdout))
    behaviour.saw_cancel = False
    return behaviour


# ── P1-1: Job creation stays closed until the Agent workdir contract is verified ──

def test_job_creation_is_closed_until_the_agent_workdir_contract_is_verified(env, monkeypatch):
    monkeypatch.setattr(provider_remote, "AGENT_WORKDIR_CONTRACT_VERIFIED", False)
    assert provider_remote.AGENT_WORKDIR_CONTRACT_VERIFIED is False
    agent = FakeAgent(env, "agt_a", completes(), deadline=0.5)
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), prompt_with_secrets(), run)
    assert (status, detail) == ("spawn_failed",
                                "remote_agent_workdir_unverified: agent_workdir_contract_unverified")
    started, chained = _chain(env, [remote(), local_provider()])
    agent.join(2)
    assert started is False and chained["fallback_history"][0]["remote_final"]["code"] == \
        "remote_agent_workdir_unverified"
    assert jobs_rows(env) == [] and env.launches == [] and env.local_calls == [] and agent.received == []


def test_the_product_default_opens_job_creation():
    # flowgate.v02.0131 CH0004: the operator opened remote CLI Job creation.
    import importlib
    source = Path(provider_remote.__file__).read_text(encoding="utf-8")
    assert "\nAGENT_WORKDIR_CONTRACT_VERIFIED = True\n" in source
    assert importlib.import_module("modules.flow_gate.services.ai_invoke.provider_remote") is provider_remote


# ── P1-3: a watch / lookup / retrieval failure never clears the run's liveness ──

@pytest.mark.parametrize("broken", ["advance_ai_jobs", "get_ai_job"])
def test_watch_failure_keeps_the_run_alive_until_the_supervisor_sees_the_job_settled(env, monkeypatch, broken):
    fail = {"on": False}
    original = getattr(jobs.AgentJobService, broken)

    def flaky(self, *a, **k):
        if fail["on"]:
            raise RuntimeError("queue unreadable")
        return original(self, *a, **k)
    monkeypatch.setattr(jobs.AgentJobService, broken, flaky)
    release = threading.Event()
    behaviour = waits_for_cancel(release)

    def agent_behaviour(svc, cred, job):
        fail["on"] = True                      # the executor's polling breaks after STARTED
        behaviour(svc, cred, job)
    agent = FakeAgent(env, "agt_a", agent_behaviour)
    agent.start()
    cleanups = []
    monkeypatch.setattr(terminal, "cleanup", lambda run, **kw: cleanups.append(kw.get("reason")) or True)
    run = _register(make_run())
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    assert (status, detail) == ("spawn_failed", "remote_job_watch_failed: watch_failed")
    assert run["remote_final_failure"]["code"] == "remote_job_watch_failed"
    # The Job still runs remotely: the run must stay alive (leases parked, not released).
    assert run["remote_job_active"] is True and not terminal.provider_stopped(run)
    fail["on"] = False
    for _ in range(50):
        if behaviour.saw_cancel:
            break
        time.sleep(0.02)
    assert behaviour.saw_cancel                # the abandoned Job was asked to stop
    run["lease_cleanup_pending"] = True        # the worker reached its terminal boundary
    remote_supervisor.tick()
    assert run["remote_job_active"] is True and cleanups == []
    release.set()
    agent.join(5)
    remote_supervisor.tick()
    assert run["remote_job_active"] is False and terminal.provider_stopped(run)
    assert cleanups == ["remote_job_settled"]
    [row] = jobs_rows(env)
    assert row["status"] == "CANCELLED" and row["stdout"] is None and row["scrubbed_at"]
    assert env.local_calls == [] and env.launches == [0]


def test_retrieval_failure_is_final_and_the_settled_job_releases_the_run(env, monkeypatch):
    def broken(self, *a, **k):
        raise RuntimeError("retrieval failed")
    monkeypatch.setattr(jobs.AgentJobService, "collect_ai_result", broken)
    agent = FakeAgent(env, "agt_a", completes("secret output"))
    agent.start()
    run = make_run()
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert (status, detail) == ("spawn_failed", "remote_result_unavailable: retrieval_failed")
    [row] = jobs_rows(env)
    assert row["status"] == "DONE" and row["retrieval_state"] == "handed_over" and row["stdout"] is None
    assert run["remote_job_active"] is False and terminal.provider_stopped(run)


def test_unreadable_job_state_after_the_watch_keeps_the_run_alive(env, monkeypatch):
    agent = FakeAgent(env, "agt_a", completes())
    agent.start()
    original = jobs.AgentJobService.get_ai_job
    calls = {"n": 0}

    def flaky(self, *a, **k):
        row = original(self, *a, **k)
        if row["status"] in jobs.TERMINAL:
            calls["n"] += 1
            if calls["n"] > 1:                 # the watch saw it; the final settle read fails
                raise RuntimeError("db down")
        return row
    monkeypatch.setattr(jobs.AgentJobService, "get_ai_job", flaky)
    run = _register(make_run())
    status, _ = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert status == "started_ok"
    assert run["remote_job_active"] is True    # unknown is never "stopped"
    monkeypatch.setattr(jobs.AgentJobService, "get_ai_job", original)
    remote_supervisor.tick()
    assert run["remote_job_active"] is False


# ── P1-4: periodic supervisor -- owner loss, retrieval deadline, late output ────

def test_supervisor_hands_over_the_output_of_a_run_lost_inside_this_process(env):
    _enqueue(env, "gone", "run_gone")
    job = _claim(env)
    _report(env, job, stdout="secret output")
    # The startup rule ("this process wrote it") alone would keep it forever.
    env.svc.sweep_orphaned_ai_jobs()
    assert _row(env, "gone")["stdout"] == "secret output"
    remote_supervisor.tick()                   # run_gone is not live in this process
    row = _row(env, "gone")
    assert row["stdout"] is None and row["retrieval_state"] == "handed_over" and row["scrubbed_at"]


def test_live_owner_keeps_its_result_until_the_retrieval_deadline(env):
    owner_run = _register(make_run())
    _enqueue(env, "live", owner_run["run_id"])
    _report(env, _claim(env), stdout="kept for the owner")
    remote_supervisor.tick()
    assert _row(env, "live")["stdout"] == "kept for the owner"
    got = env.svc.collect_ai_result("live", "a", jobs.retrieval_owner_for(owner_run["run_id"]))
    assert got["collected"] is True and got["stdout"] == "kept for the owner"
    late_run = _register(make_run())
    _enqueue(env, "late", late_run["run_id"])
    _report(env, _claim(env), stdout="overdue")
    env.clock.jump(jobs.AI_RETRIEVAL_GRACE_SECONDS + 1)
    remote_supervisor.tick()
    assert _row(env, "late")["stdout"] is None
    got = env.svc.collect_ai_result("late", "a", jobs.retrieval_owner_for(late_run["run_id"]))
    assert got["collected"] is False and got["stdout"] is None


def test_live_owner_collect_racing_the_supervisor_never_loses_the_result(env):
    for n in range(10):
        owner_run = _register(make_run())
        job_id = f"race_{n}"
        _enqueue(env, job_id, owner_run["run_id"])
        _report(env, _claim(env), stdout=f"result {n}")
        got = {}
        barrier = threading.Barrier(2)

        def collect():
            barrier.wait()
            got["r"] = env.svc.collect_ai_result(job_id, "a", jobs.retrieval_owner_for(owner_run["run_id"]))

        def sweep():
            barrier.wait()
            remote_supervisor.tick()
        threads = [threading.Thread(target=collect), threading.Thread(target=sweep)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        assert got["r"]["collected"] is True and got["r"]["stdout"] == f"result {n}"
        assert _row(env, job_id)["stdout"] is None


def test_late_report_after_a_startup_hand_over_is_not_kept(env):
    _enqueue(env, "prev", "run_prev", owner="proc_previous:run_prev")
    job = _claim(env)
    env.svc.sweep_orphaned_ai_jobs()           # startup: handed over + cancel requested
    row = _row(env, "prev")
    assert row["retrieval_state"] == "handed_over" and row["cancel_requested"] == 1
    _report(env, job, "CANCELLED", stdout="late secret")
    for _ in range(3):
        env.svc.advance_ai_jobs()
    row = _row(env, "prev")
    assert row["status"] == "CANCELLED" and row["stdout"] is None and row["scrubbed_at"]


def test_supervisor_retries_the_revocation_of_an_unconfirmed_end(env, monkeypatch):
    from modules.flow_gate.services import token_service
    _enqueue(env, "exp", "run_exp", token="tok_exp")
    _claim(env)
    env.clock.jump(jobs.LEASE_TTL_SECONDS + 5)
    monkeypatch.setattr(token_service, "revoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    remote_supervisor.tick()
    row = _row(env, "exp")
    assert row["terminal_cause"] == "server_expired" and row["fenced_at"] is None
    assert remote_supervisor.run_has_unsettled_job("run_exp") is True
    monkeypatch.setattr(token_service, "revoke", lambda token_id, reason="": env.revoked.append((token_id, reason)))
    remote_supervisor.tick()
    assert _row(env, "exp")["fenced_at"] and env.revoked == [("tok_exp", "remote_termination_unconfirmed")]
    assert remote_supervisor.run_has_unsettled_job("run_exp") is False


# ── P1-2: restart holds the leases of a run whose remote Job is not settled ─────

@pytest.fixture
def restart(env, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service, q_service
    recorded, token_reclaims = [], []
    monkeypatch.setattr(ai_invoke_service, "_record_orphaned_lease_run",
                        lambda row, reason: recorded.append((row.get("run_id"), reason)))
    monkeypatch.setattr(admission, "_reclaim_orphan_lease_token",
                        lambda row, reason: token_reclaims.append(row.get("run_id")))
    monkeypatch.setattr(ai_invoke_service, "startup_recover_handoffs", lambda: None)
    monkeypatch.setattr(q_service, "startup_recover_question_responders", lambda: None)

    def lease(group, run_id):
        db_leases.acquire(group_id=group, project_id="prj", run_id=run_id, chain_id=run_id,
                          action_scope="new", worker_identity="usr_ai")
        db_leases._memory[group]["acquired_at"] = "2000-01-01T00:00:00+00:00"
        return db_leases._memory[group]
    return NS(recorded=recorded, token_reclaims=token_reclaims, lease=lease)


def test_restart_holds_a_live_remote_run_and_resumes_after_the_agent_report(env, restart):
    _enqueue(env, "held", "run_h", owner="proc_previous:run_h", token="tok_h", group="grp_h")
    job = _claim(env)
    restart.lease("grp_h", "run_h")
    restart.lease("grp_n", "run_n")            # a run without a remote Job: recovered as before
    assert remote_supervisor.startup_hold_remote_runs() == {"run_h"}
    assert ("tok_h", "remote_termination_unconfirmed") in env.revoked      # fenced at once
    hold = env.store._fetch_one("SELECT * FROM ai_remote_recovery_holds WHERE run_id='run_h'")
    assert hold["token_revoked_at"] and hold["resumed_at"] is None
    row = _row(env, "held")
    assert row["status"] == "STARTED" and row["cancel_requested"] == 1 and row["retrieval_state"] == "handed_over"
    assert admission.startup_recover_leases() == 1
    assert db_leases.get("grp_h")["run_id"] == "run_h" and db_leases.get("grp_n") is None
    assert restart.recorded == [("run_n", "orphaned_by_restart")]
    # A second restart while the Job is still alive keeps the hold and the lease.
    assert remote_supervisor.startup_hold_remote_runs() == {"run_h"}
    assert admission.startup_recover_leases() == 0 and db_leases.get("grp_h")["run_id"] == "run_h"
    assert remote_supervisor.remote_alive("run_h") is True
    db_leases._memory["grp_h"]["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
    remote_supervisor.tick()                   # still alive: heartbeat, no resume
    assert db_leases.get("grp_h")["heartbeat_at"] != "2000-01-01T00:00:00+00:00"
    assert restart.recorded == [("run_n", "orphaned_by_restart")]
    _report(env, job, "CANCELLED", stdout="late secret")
    remote_supervisor.tick()
    assert db_leases.get("grp_h") is None
    assert ("run_h", "orphaned_by_restart") in restart.recorded and restart.token_reclaims[-1] == "run_h"
    hold = env.store._fetch_one("SELECT * FROM ai_remote_recovery_holds WHERE run_id='run_h'")
    assert hold["resumed_at"] and remote_supervisor.remote_alive("run_h") is False
    assert _row(env, "held")["stdout"] is None


def test_restart_hold_survives_revocation_failure_until_the_token_is_fenced(env, restart, monkeypatch):
    from modules.flow_gate.services import token_service
    monkeypatch.setattr(token_service, "revoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    _enqueue(env, "held", "run_f", owner="proc_previous:run_f", token="tok_f", group="grp_f")
    _claim(env, start=False)
    restart.lease("grp_f", "run_f")
    assert remote_supervisor.startup_hold_remote_runs() == {"run_f"}
    hold = env.store._fetch_one("SELECT * FROM ai_remote_recovery_holds WHERE run_id='run_f'")
    assert hold["token_revoked_at"] is None and hold["last_error"] == "token_revoke_failed"
    assert admission.startup_recover_leases() == 0
    env.clock.jump(jobs.LEASE_TTL_SECONDS + 5)  # the Agent is gone: server-expired, still unfenced
    remote_supervisor.tick()
    assert _row(env, "held")["terminal_cause"] == "server_expired"
    assert db_leases.get("grp_f")["run_id"] == "run_f" and restart.recorded == []
    monkeypatch.setattr(token_service, "revoke", lambda token_id, reason="": env.revoked.append((token_id, reason)))
    remote_supervisor.tick()
    assert db_leases.get("grp_f") is None and restart.recorded == [("run_f", "orphaned_by_restart")]


def test_restart_sweep_and_claim_race_ends_with_one_rule(env):
    for n in range(8):
        job_id = f"r{n}"
        _enqueue(env, job_id, f"run_r{n}", owner=f"proc_previous:run_r{n}")
        got = {}
        barrier = threading.Barrier(2)

        def claimer():
            barrier.wait()
            got["claim"] = env.svc.claim(env.creds["agt_a"])

        def sweeper():
            barrier.wait()
            env.svc.sweep_orphaned_ai_jobs()
        threads = [threading.Thread(target=claimer), threading.Thread(target=sweeper)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        row = _row(env, job_id)
        if got["claim"] is None:
            assert row["status"] == "CANCELLED"
        else:
            assert row["status"] == "CLAIMED" and row["cancel_requested"] == 1
            env.svc.report(env.creds["agt_a"], job_id, "a", got["claim"]["job"]["lease_token"],
                           report("CANCELLED", stdout=""))


def test_graph_restart_recovery_skips_held_runs(env, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service
    rows = [{"exec_id": "exe_h", "ai_run_id": "run_h", "group_id": "g", "status": "running",
             "created_at": "t", "lease_token": "lt"},
            {"exec_id": "exe_n", "ai_run_id": "run_n", "group_id": "g", "status": "running",
             "created_at": "t", "lease_token": "lt"}]
    executed = []
    fake = NS(table_exists=lambda name: True, _fetch_all=lambda sql, params: rows,
              _execute=lambda sql, params: executed.append(params[1]))
    monkeypatch.setattr(admission.db_connection, "get_store", lambda: fake)
    recorded = []
    monkeypatch.setattr(ai_invoke_service, "_record_orphaned_lease_run",
                        lambda row, reason: recorded.append(row["run_id"]))
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "p"})
    admission._startup_recover_graph_leases(frozenset({"run_h"}))
    assert recorded == ["run_n"] and executed == ["exe_n"]   # held: no end record, lease not expired


def test_graph_recovery_never_marks_a_held_or_unsettled_execution_lost(env, monkeypatch):
    from flowgate.graph_run import execution
    from modules.flow_gate.services import ai_invoke_service
    _enqueue(env, "gx", "run_gx", graph="exe_gx")
    _claim(env)
    marked = []
    monkeypatch.setattr(execution.ledger, "get", lambda exec_id: {
        "exec_id": exec_id, "status": "running", "ai_run_id": "run_gx", "lease_expires_at": "2000-01-01",
        "lease_token": "lt", "attempt_id": "att"})
    monkeypatch.setattr(execution.ledger, "mark_lost", lambda *a: marked.append(a))
    monkeypatch.setattr(execution.ai_invoke_runs, "get", lambda run_id: {"run_id": run_id})
    monkeypatch.setattr(execution, "with_flow_guard", lambda group, body: body())

    @contextmanager
    def exclusive(*a, **k):
        yield
    monkeypatch.setattr(execution.fencing, "exclusive", exclusive)
    execution._recover_snapshot({"exec_id": "exe_gx", "group_id": "g"}, ai_invoke_service, NS())
    assert marked == []                        # the remote Job is alive: no lost, no node reset
    from flowgate.graph_run import stop
    assert stop._process_ended({"exec_id": "exe_gx", "ai_run_id": "run_gx"}) is False


# ── P1-5: launch refuses while a previous remote Job is not settled ─────────────

@pytest.fixture
def graph_launch(env, monkeypatch):
    from flowgate.graph_run import execution
    refused, renewed = [], []
    monkeypatch.setattr(execution.ledger, "cancel_ineligible", lambda exec_id: refused.append(exec_id))
    monkeypatch.setattr(execution.ledger, "renew", lambda exec_id, token: renewed.append(exec_id))
    monkeypatch.setattr(execution.ledger, "get", lambda exec_id: {"exec_id": exec_id, "attempt_id": "att_1"})
    monkeypatch.setattr(execution.workspace, "verify_execution", lambda exec_id: None)
    node_execs = ["exe_1"]
    monkeypatch.setattr(execution.run_store, "_one", lambda sql, params: {"run_id": "gr", "node_key": "n1"})
    monkeypatch.setattr(execution.run_store, "_all", lambda sql, params: [{"exec_id": e} for e in node_execs])

    def launch(**kw):
        run = {"run_id": "run_new", "graph_exec_id": "exe_1", "cancel_event": threading.Event(),
               "graph_lease_token": "lt", "proc": None}
        run.update(kw)
        return execution.launch(run), run
    return NS(launch=launch, refused=refused, renewed=renewed, node_execs=node_execs)


def test_graph_launch_refuses_while_the_run_marks_a_remote_job_alive(graph_launch):
    approved, run = graph_launch.launch(remote_job_active=True)
    assert approved is False and run["graph_lease_lost"] and graph_launch.refused == ["exe_1"]
    assert graph_launch.renewed == []


def test_graph_launch_refuses_relaunch_and_replacement_while_a_remote_job_is_unsettled(env, graph_launch):
    _enqueue(env, "same_run", "run_new")                          # an earlier attempt of the same run
    assert graph_launch.launch()[0] is False
    env.svc.cancel_ai_job("same_run", "a")
    _enqueue(env, "same_exec", "run_old", graph="exe_1")          # the run this replay replaces
    assert graph_launch.launch()[0] is False
    env.svc.cancel_ai_job("same_exec", "a")
    graph_launch.node_execs.append("exe_0")
    _enqueue(env, "same_node", "run_older", graph="exe_0")        # an earlier execution of the node
    assert graph_launch.launch()[0] is False
    env.svc.cancel_ai_job("same_node", "a")
    approved, _ = graph_launch.launch()
    assert approved is True and graph_launch.renewed == ["exe_1"]


def test_remote_precheck_refuses_to_replace_a_run_whose_remote_job_is_alive(env):
    _enqueue(env, "prev", "run_prev", group="grp_x")
    run = make_run(group_id="grp_x")
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    assert (status, detail) == ("spawn_failed", "remote_previous_job_unsettled: previous_remote_job_not_settled")
    _enqueue(env, "prev_g", "run_prev_g", graph="exe_7")
    run = make_run(graph_exec_id="exe_7")
    status, detail = provider_cli._cli_execute(remote(), "p", run)
    assert detail.startswith("remote_previous_job_unsettled")
    assert len(jobs_rows(env)) == 2 and env.launches == []


# ── P2-6: the progress watchdog and the ceilings apply to remote runs ───────────

@pytest.fixture
def watchdog(env, monkeypatch):
    from modules.flow_gate.services.ai_invoke import oracle
    seq = {"value": 5, "step": 0}

    def observe(run):
        seq["value"] += seq["step"]
        return seq["value"]
    monkeypatch.setattr(oracle, "_observe_group_max_seq", observe)
    monkeypatch.setattr(provider_remote, "REMOTE_PROGRESS_INTERVAL_SEC", 0.05)
    return seq


def test_no_progress_cancels_the_job_and_holds_the_run_until_it_ends(env, watchdog):
    behaviour = waits_for_cancel(status="CANCELLED", stdout="")
    agent = FakeAgent(env, "agt_a", behaviour)
    agent.start()
    run = make_run(timeout_sec=0.3, group_id="grp_w")
    status, _ = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert behaviour.saw_cancel and run["timed_out"] is True
    assert run["watchdog_kill"]["kind"] == "no_progress" and run["remote_job"]["cancel_reason"] == "no_progress"
    [row] = jobs_rows(env)
    assert row["status"] == "CANCELLED" and row["cancel_requested"] == 1 and row["terminal_cause"] == "agent_report"
    assert status == "started_ok" and run["exit_code"] is None   # a timeout, never a startup failure
    assert run["remote_job_active"] is False


def test_progress_renews_the_no_progress_window(env, watchdog):
    watchdog["step"] = 1                       # a new document every sample

    def slow(svc, cred, job):
        svc.started(cred, job["job_id"], job["attempt_id"], job["lease_token"])
        time.sleep(0.8)                        # longer than timeout_sec, but busy
        svc.report(cred, job["job_id"], job["attempt_id"], job["lease_token"], report(stdout="done"))
    agent = FakeAgent(env, "agt_a", slow)
    agent.start()
    run = make_run(timeout_sec=0.3, group_id="grp_w")
    status, _ = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert status == "started_ok" and not run.get("timed_out") and run.get("watchdog_kill") is None
    assert run["progress_observations"] >= 1 and jobs_rows(env)[0]["cancel_requested"] == 0


def test_absolute_ceiling_cancels_the_job(env, monkeypatch):
    behaviour = waits_for_cancel(status="CANCELLED", stdout="")
    agent = FakeAgent(env, "agt_a", behaviour)
    # The Job's limit is what is left; the ceiling is reached once the Agent holds the Job.
    monkeypatch.setattr(provider_remote, "_absolute_remaining_sec",
                        lambda run: 0 if agent.claimed.is_set() else 600)
    agent.start()
    run = make_run()
    provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert behaviour.saw_cancel and run["watchdog_kill"]["kind"] == "absolute_cap" and run["timed_out"]
    assert jobs_rows(env)[0]["timeout_seconds"] == 600


# ── P2-7: the run record keeps the remote Job identity on success ───────────────

def test_successful_run_record_links_the_job_and_the_agents(env, monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    from modules.flow_gate.services.ai_invoke import finalize
    env.store.db.execute("PRAGMA foreign_keys=OFF")   # the record's group/project rows are not under test
    monkeypatch.setattr(db_runs, "get_store", lambda: env.store)
    agent = FakeAgent(env, "agt_a", completes())
    agent.start()
    run = make_run(group_id="grp_r", project_id="prj_r", doc_ref="flowgate.v02.0129.0009-TR", mode="single",
                   started_at="2026-11-04T00:00:00+00:00", finished_at="2026-11-04T00:01:00+00:00",
                   provider_id=None)
    status, _ = provider_cli._cli_execute(remote(), "p", run)
    agent.join(5)
    assert status == "started_ok" and "fallback_history" in run and run["fallback_history"] == []
    assert finalize._persist_run_record(run) is True
    path = env.store.db.execute("PRAGMA database_list").fetchone()[2]
    fresh = sqlite3.connect(path)                      # another connection = another process' view
    raw = fresh.execute("SELECT remote_job_json FROM ai_invoke_runs WHERE run_id=?", [run["run_id"]]).fetchone()[0]
    fresh.close()
    record = db_runs._row_to_payload({"remote_job_json": raw})["remote_job"]
    [row] = jobs_rows(env)
    assert (record["job_id"], record["attempt_id"]) == (row["job_id"], row["attempt_id"])
    assert record["target_agent_id"] == record["claimed_agent_id"] == row["lease_owner_agent_id"] == "agt_a"
    assert row["ai_run_id"] == run["run_id"] and record["status"] == "DONE"
    assert TOKEN not in raw and COMMAND not in raw


# ── P2-8: the Job keeps the run's project for shared and personal connections ───

def test_job_keeps_the_run_project_for_shared_and_personal_connections(env, monkeypatch):
    agent = FakeAgent(env, "agt_a", completes())
    agent.start()
    run = make_run(project_id="prj_shared")
    assert provider_cli._cli_execute(remote(), "p", run)[0] == "started_ok"
    agent.join(5)
    shared = env.store._fetch_one("SELECT * FROM agent_jobs WHERE ai_run_id=?", [run["run_id"]])
    assert (shared["project_id"], shared["requested_by_user_id"]) == ("prj_shared", None)
    # A personal Agent of an active user (the seeded reserved user, activated in this copy).
    env.store._execute("UPDATE users SET is_active=1 WHERE user_id='u-system'")
    env.creds["agt_p"] = add_agent(env.store, "agt_p", owner="u-system")
    monkeypatch.setattr(provider_remote, "_binding", lambda provider: {"target_agent_id": "agt_p",
                                                                       "requester": "u-system"})
    monkeypatch.setattr(jobs.AgentJobService, "_requester_allowed", lambda self, user, project: True)
    personal_agent = FakeAgent(env, "agt_p", completes())
    personal_agent.start()
    run = make_run(project_id="prj_personal", issued_to="u-system")
    assert provider_cli._cli_execute(remote(target="agt_p"), "p", run)[0] == "started_ok"
    personal_agent.join(5)
    personal = env.store._fetch_one("SELECT * FROM agent_jobs WHERE ai_run_id=?", [run["run_id"]])
    assert (personal["project_id"], personal["requested_by_user_id"]) == ("prj_personal", "u-system")
    assert personal["lease_owner_agent_id"] == "agt_p" and personal["ai_run_id"] == run["run_id"]


# ── CH0012 (approved (B)): a personal connection runs only for its owner ──────────

def test_a_personal_connection_refuses_a_run_requested_by_another_user(env, monkeypatch):
    env.store._execute("UPDATE users SET is_active=1 WHERE user_id='u-system'")
    env.creds["agt_p"] = add_agent(env.store, "agt_p", owner="u-system")
    monkeypatch.setattr(provider_remote, "_binding", lambda provider: {"target_agent_id": "agt_p",
                                                                       "requester": "u-system"})
    run = make_run(project_id="prj_personal", issued_to="someone-else")
    assert provider_cli._cli_execute(remote(target="agt_p"), "p", run) == (
        "spawn_failed", "remote_agent_unavailable: requester_not_owner")
    assert run["remote_final_failure"]["code"] == "remote_agent_unavailable"
    # Final: no launch approval, no Job, no local fallback.
    assert jobs_rows(env) == [] and env.launches == [] and env.local_calls == []
