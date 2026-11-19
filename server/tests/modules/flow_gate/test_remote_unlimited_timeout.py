"""Unlimited remote chat jobs keep cancellation and lease fencing intact.

Use the real provider boundary, HTTP claim route and fully migrated SQLite queue.
Only the pull Agent is simulated with the same public lifecycle calls it makes.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import agent_job_routes
from modules.flow_gate.services import agent_jobs as jobs
from modules.flow_gate.services.agent_registry import AgentError
from modules.flow_gate.services.ai_invoke import provider_cli
from test_agent_jobs_0636 import CLAIM_KEYS
from test_remote_cli_routing_0129 import (
    MIG, FakeAgent, completes, env, jobs_rows, make_run, remote, report,
)


MIGRATION = "199_agent_job_unlimited_timeout.sql"


def enqueue(env, timeout_seconds=0, *, ai_run=True, job_id="unlimited"):
    extra = {}
    if ai_run:
        run_id = "run_" + job_id
        extra = {"ai_run_id": run_id, "retrieval_owner": jobs.retrieval_owner_for(run_id)}
    return env.svc.enqueue_remote_cli(
        command_line="claude -p", stdin_text="hello", cwd="/tmp/flowgate/" + job_id,
        timeout_seconds=timeout_seconds, job_id=job_id, attempt_id="a",
        target_agent_id="agt_a", **extra,
    )


def claim_and_start(env):
    job = env.svc.claim(env.creds["agt_a"])["job"]
    env.svc.started(env.creds["agt_a"], job["job_id"], job["attempt_id"], job["lease_token"])
    return job


def test_unlimited_chat_reaches_agent_and_returns_its_answer(env):
    agent = FakeAgent(env, "agt_a", completes("unlimited answer"))
    agent.start()
    run = make_run(chat_timeout_policy=True, chat_timeout_sec=None,
                   started_mono=time.monotonic() - 86400)

    assert provider_cli._cli_execute(remote(), "hello", run) == ("started_ok", None)
    agent.join(5)

    assert not agent.is_alive()
    assert len(agent.received) == 1
    assert agent.received[0]["timeout_seconds"] == 0
    assert run["last_message"] == "unlimited answer"
    assert run.get("remote_final_failure") is None
    assert not run["timed_out"] and not run["remote_job_active"]
    assert env.local_calls == []
    [row] = jobs_rows(env)
    assert row["timeout_unlimited"] == 1
    assert row["status"] == "DONE" and row["retrieval_state"] == "collected"
    assert row["stdout"] is None and row["scrubbed_at"]


def test_finite_chat_still_sends_its_remaining_timeout(env):
    agent = FakeAgent(env, "agt_a", completes())
    agent.start()
    run = make_run(chat_timeout_policy=True, chat_timeout_sec=120)

    assert provider_cli._cli_execute(remote(), "hello", run) == ("started_ok", None)
    agent.join(5)

    assert len(agent.received) == 1
    assert 0 < agent.received[0]["timeout_seconds"] <= 120
    [row] = jobs_rows(env)
    assert row["timeout_unlimited"] == 0
    assert row["timeout_seconds"] == agent.received[0]["timeout_seconds"]


def test_unlimited_chat_user_cancel_reaches_agent_and_settles(env):
    run = make_run(chat_timeout_policy=True, chat_timeout_sec=None)
    cancellations = []

    def cancel_then_report(svc, credential, job):
        key = (credential, job["job_id"], job["attempt_id"], job["lease_token"])
        svc.started(*key)
        run["cancel_event"].set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if svc.renew(*key)["cancel_requested"]:
                cancellations.append(job["job_id"])
                break
            time.sleep(0.01)
        svc.report(*key, report("CANCELLED", stdout="", end_reason="cancel_requested"))

    agent = FakeAgent(env, "agt_a", cancel_then_report)
    agent.start()
    assert provider_cli._cli_execute(remote(), "hello", run) == ("started_ok", None)
    agent.join(5)

    assert cancellations == [agent.received[0]["job_id"]]
    assert agent.received[0]["timeout_seconds"] == 0
    [row] = jobs_rows(env)
    assert row["status"] == "CANCELLED" and row["terminal_cause"] == "agent_report"
    assert not run["remote_job_active"] and not run["timed_out"]


@pytest.mark.parametrize("ai_run", [False, True])
def test_zero_timeout_round_trips_without_changing_claim_contract(env, monkeypatch, ai_run):
    enqueue(env, ai_run=ai_run)
    monkeypatch.setattr(agent_job_routes, "svc", lambda: env.svc)
    app = FastAPI()
    app.include_router(agent_job_routes.router, prefix="/flowgate/api/v1")
    with TestClient(app) as client:
        response = client.post(
            "/flowgate/api/v1/agent/jobs/claim", json={},
            headers={"Authorization": "Bearer " + env.creds["agt_a"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"job"}
    assert set(payload["job"]) == CLAIM_KEYS
    assert payload["job"]["timeout_seconds"] == 0
    row = env.svc._get("unlimited", "a")
    assert row["timeout_unlimited"] == 1
    assert row["timeout_seconds"] > 0  # Existing storage CHECK remains satisfied.


@pytest.mark.parametrize("ai_run", [False, True])
def test_unlimited_payload_is_idempotent_and_distinct_from_finite_one(env, ai_run):
    assert enqueue(env, ai_run=ai_run)["created"] is True
    assert enqueue(env, ai_run=ai_run)["created"] is False
    original = env.svc._get("unlimited", "a")

    with pytest.raises(AgentError) as exc:
        enqueue(env, 1, ai_run=ai_run)
    assert exc.value.code == "attempt_payload_conflict"
    current = env.svc._get("unlimited", "a")
    assert current["payload_digest"] == original["payload_digest"]
    assert current["timeout_unlimited"] == 1


@pytest.mark.parametrize("value", [None, False, True, -1, 0.0, 1.5, "0"])
def test_invalid_timeout_is_not_silently_unlimited(env, value):
    with pytest.raises(AgentError) as exc:
        enqueue(env, value)
    assert exc.value.code == "validation_failed"
    assert jobs_rows(env) == []


@pytest.mark.parametrize("timeout_seconds,should_cancel", [(0, False), (60, True)])
def test_only_finite_jobs_expire_by_elapsed_execution_time(env, timeout_seconds, should_cancel):
    enqueue(env, timeout_seconds)
    job = claim_and_start(env)
    # Keep a live lease while making the execution older than any finite timeout.
    env.store._execute("UPDATE agent_jobs SET started_at=? WHERE job_id=?", [
        jobs._stamp(env.clock() - timedelta(days=365)), job["job_id"],
    ])

    env.svc.advance_ai_jobs()
    row = env.svc.get_ai_job(job["job_id"], "a")
    assert row["status"] == "STARTED"
    assert bool(row["cancel_requested"]) is should_cancel
    renewed = env.svc.renew(env.creds["agt_a"], job["job_id"], "a", job["lease_token"])
    assert renewed["cancel_requested"] is should_cancel


def test_unlimited_job_still_expires_when_lease_is_lost(env):
    enqueue(env)
    job = claim_and_start(env)
    env.clock.jump(jobs.LEASE_TTL_SECONDS + 1)

    env.svc.advance_ai_jobs()
    row = env.svc.get_ai_job(job["job_id"], "a")
    assert (row["status"], row["terminal_cause"], row["end_reason"]) == (
        "FAILED", "server_expired", "remote_termination_unconfirmed",
    )
    assert env.svc.claim(env.creds["agt_a"]) is None
    with pytest.raises(AgentError) as exc:
        env.svc.report(env.creds["agt_a"], job["job_id"], "a", job["lease_token"], report())
    assert exc.value.code == "terminal_conflict"


def test_unlimited_unclaimed_job_keeps_claim_wait_limit(env):
    enqueue(env)
    env.clock.jump(jobs.AI_CLAIM_WAIT_SECONDS + 1)

    env.svc.advance_ai_jobs()
    row = env.svc.get_ai_job("unlimited", "a")
    assert (row["status"], row["terminal_cause"]) == ("CANCELLED", "claim_timeout")
    assert env.svc.claim(env.creds["agt_a"]) is None


def test_unlimited_claimed_job_keeps_start_wait_limit(env):
    enqueue(env)
    job = env.svc.claim(env.creds["agt_a"])["job"]
    # Isolate the start deadline from lease expiry; a live claim must still start.
    env.store._execute("UPDATE agent_jobs SET lease_expires_at=? WHERE job_id=?", [
        jobs._stamp(env.clock() + timedelta(seconds=3 * jobs.LEASE_TTL_SECONDS)), job["job_id"],
    ])
    env.clock.jump(jobs.AI_START_WAIT_SECONDS + 1)

    env.svc.advance_ai_jobs()
    row = env.svc.get_ai_job(job["job_id"], "a")
    assert row["status"] == "CLAIMED" and row["cancel_requested"]
    assert env.svc.renew(env.creds["agt_a"], job["job_id"], "a", job["lease_token"])["cancel_requested"]


def test_unlimited_job_cancel_before_claim_is_terminal(env):
    enqueue(env)
    env.svc.cancel_ai_job("unlimited", "a")

    row = env.svc.get_ai_job("unlimited", "a")
    assert row["status"] == "CANCELLED" and row["cancel_requested"]
    assert env.svc.claim(env.creds["agt_a"]) is None


def test_migration_preserves_existing_finite_jobs_and_payload_digests():
    with sqlite3.connect(":memory:") as db:
        db.executescript((MIG / "sqlite" / "124_agent_jobs.sql").read_text(encoding="utf-8"))
        db.execute(
            """INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,
               timeout_seconds,status,payload_digest,created_at,updated_at)
               VALUES('existing','a','remote_cli','c','s','/w',120,'NEW','stable-digest','t','t')"""
        )
        db.executescript((MIG / "sqlite" / MIGRATION).read_text(encoding="utf-8"))
        assert db.execute(
            "SELECT timeout_seconds,timeout_unlimited,payload_digest,status FROM agent_jobs"
        ).fetchone() == (120, 0, "stable-digest", "NEW")
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
