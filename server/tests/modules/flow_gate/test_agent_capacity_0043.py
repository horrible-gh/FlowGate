"""Capacity is enforced at the shared claim boundary, including concurrent polls."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from pydantic import ValidationError
from modules.flow_gate.api.v1.agent_routes import AgentCreate, AgentPatch
from modules.flow_gate.services import agent_jobs as jobs
from modules.flow_gate.services.agent_registry import AgentService
from test_agent_jobs_0636 import Store, add_agent, enqueue, result, NOW, setup

@pytest.mark.parametrize("value", [0,33,-1,True,1.5,"4",None])
def test_capacity_validation(value):
    with pytest.raises(ValidationError): AgentCreate(name="PC",max_concurrent_jobs=value)
    with pytest.raises(ValidationError): AgentPatch(max_concurrent_jobs=value)

def test_registration_defaults_and_removed_controls(setup):
    store,svc,creds=setup
    row=AgentService(store).create(AgentCreate(name="PC").model_dump())
    assert row["max_concurrent_jobs"]==4 and row["enabled"] and row["location"]=="remote"
    for key,value in {"enabled":False,"location":"local","configured_capabilities":{}}.items():
        with pytest.raises(ValidationError): AgentCreate(name="PC",**{key:value})

def test_limit_decrease_keeps_jobs_and_increase_releases_queue(setup):
    store,svc,creds=setup
    reg=AgentService(store)
    reg.patch("agt_a",{"max_concurrent_jobs":2})
    for n in range(5): enqueue(svc,job_id=str(n),attempt_id="a",target_agent_id="agt_a")
    first=svc.claim(creds["agt_a"])["job"]
    second=svc.claim(creds["agt_a"])["job"]
    svc.started(creds["agt_a"],first["job_id"],"a",first["lease_token"])
    assert svc.claim(creds["agt_a"]) is None
    reg.patch("agt_a",{"max_concurrent_jobs":1})
    assert reg.get("agt_a")["running_jobs"]==2
    svc.report(creds["agt_a"],first["job_id"],"a",first["lease_token"],result())
    assert svc.claim(creds["agt_a"]) is None
    reg.patch("agt_a",{"max_concurrent_jobs":5})
    assert svc.claim(creds["agt_a"])["job"]["job_id"]=="2"
    assert reg.get("agt_a")["queued_jobs"]==2

def test_reported_cli_is_authoritative(setup):
    store,svc,creds=setup
    store._execute("UPDATE agents SET configured_ai_cli=0 WHERE agent_id='agt_a'")
    enqueue(svc,target_agent_id="agt_a")
    assert AgentService(store).get("agt_a")["effective_capabilities"]["ai_cli"]
    assert svc.claim(creds["agt_a"]) is not None

def test_same_agent_parallel_claims_cannot_exceed_limit(migrated_sqlite_db,monkeypatch):
    monkeypatch.setattr(jobs,"_now",lambda:NOW)
    path=str(migrated_sqlite_db("capacity-race.sqlite"))
    seed=Store(path); raw=add_agent(seed,"agt_a")
    AgentService(seed).patch("agt_a",{"max_concurrent_jobs":1})
    for n in range(4): enqueue(jobs.AgentJobService(seed),job_id=str(n),attempt_id="a")
    seed.db.close()
    barrier=threading.Barrier(4)
    def claim(_):
        store=Store(path)
        try:
            barrier.wait()
            return jobs.AgentJobService(store).claim(raw)
        finally: store.db.close()
    with ThreadPoolExecutor(max_workers=4) as pool: values=list(pool.map(claim,range(4)))
    assert sum(v is not None for v in values)==1

def test_full_online_agent_does_not_fail_queued_ai_after_two_minutes(setup,monkeypatch):
    store,svc,creds=setup
    AgentService(store).patch("agt_a",{"max_concurrent_jobs":1})
    enqueue(svc,job_id="running",attempt_id="a",target_agent_id="agt_a")
    svc.claim(creds["agt_a"])
    # Link a queued job as an AI run without depending on the provider launch path.
    enqueue(svc,job_id="queued",attempt_id="a",target_agent_id="agt_a")
    store._execute("UPDATE agent_jobs SET ai_run_id='run-test' WHERE job_id='queued'")
    later=NOW+timedelta(seconds=121)
    ts=jobs._stamp(later)
    store._execute("UPDATE agent_credentials SET last_heartbeat_at=? WHERE agent_id='agt_a'",[ts])
    store._execute("UPDATE agent_jobs SET lease_expires_at=? WHERE job_id='running'",[jobs._stamp(later+timedelta(seconds=90))])
    with store.transaction(): svc._advance_ai(ts)
    row=svc._get("queued","a")
    assert row["status"]=="NEW" and row["capacity_wait_at"]==ts

@pytest.mark.parametrize("ai", [False, True])
def test_expired_job_holds_capacity_until_lease_plus_grace(setup, monkeypatch, ai):
    from modules.flow_gate.services import agent_registry as registry
    store, svc, creds = setup
    AgentService(store).patch("agt_a", {"max_concurrent_jobs": 1})
    enqueue(svc, job_id="expired", attempt_id="a", target_agent_id="agt_a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    svc.started(creds["agt_a"], "expired", "a", token)
    if ai:
        store._execute("UPDATE agent_jobs SET ai_run_id='run-test' WHERE job_id='expired'")
    enqueue(svc, job_id="next", attempt_id="a", target_agent_id="agt_a")
    for elapsed in (91, 149):
        at = NOW + timedelta(seconds=elapsed)
        monkeypatch.setattr(jobs, "_now", lambda: at)
        monkeypatch.setattr(registry, "now", lambda: at)
        assert svc.claim(creds["agt_a"]) is None
        assert svc._get("expired", "a")["status"] == "FAILED"
        assert AgentService(store).get("agt_a")["running_jobs"] == 1
        assert svc._claim_facts("agt_a", at)["held"] == 1
    at = NOW + timedelta(seconds=150)
    monkeypatch.setattr(jobs, "_now", lambda: at)
    monkeypatch.setattr(registry, "now", lambda: at)
    assert AgentService(store).get("agt_a")["running_jobs"] == 0
    assert svc.claim(creds["agt_a"])["job"]["job_id"] == "next"


def test_cancel_timeout_keeps_original_future_lease(setup, monkeypatch):
    from modules.flow_gate.services import agent_registry as registry
    store, svc, creds = setup
    AgentService(store).patch("agt_a", {"max_concurrent_jobs": 1})
    enqueue(svc, job_id="cancel", attempt_id="a", target_agent_id="agt_a")
    token = svc.claim(creds["agt_a"])["job"]["lease_token"]
    svc.started(creds["agt_a"], "cancel", "a", token)
    store._execute("UPDATE agent_jobs SET ai_run_id='run-test' WHERE job_id='cancel'")
    svc.request_cancel("cancel", "a")
    at = NOW + timedelta(seconds=80)
    monkeypatch.setattr(jobs, "_now", lambda: at)
    svc.renew(creds["agt_a"], "cancel", "a", token)
    at = NOW + timedelta(seconds=91)
    monkeypatch.setattr(registry, "now", lambda: at)
    svc.advance_ai_jobs()
    # advance uses jobs._now's closure too. Cancellation grace expired at 90s,
    # but the renewed lease lasts until 170s: capacity remains reserved until 230s.
    assert svc._get("cancel", "a")["status"] == "FAILED"
    assert AgentService(store).get("agt_a")["running_jobs"] == 1
    at = NOW + timedelta(seconds=230)
    assert AgentService(store).get("agt_a")["running_jobs"] == 0
