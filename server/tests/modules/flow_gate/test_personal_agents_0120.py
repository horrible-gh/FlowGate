"""Personal Agent ownership, real authentication, management and job isolation."""
import pytest
from test_system_agents_0076 import env, _create, REMOTE_BODY, API  # noqa: F401
from modules.flow_gate.services.agent_registry import AgentError
from modules.flow_gate.services.agent_jobs import AgentJobService

PERSONAL = {**REMOTE_BODY, "configured_capabilities": {"ai_cli": True, "ai_api": True, "storage": False}}
ME = API + "/me/agents"


def create_personal(env, who="M", **changes):
    response = env.client.post(ME, json={**PERSONAL, **changes}, headers=env.h(who))
    assert response.status_code == 201, response.text
    return response.json()


def test_personal_crud_isolated_from_other_people_and_system_registry(env):
    system = _create(env, REMOTE_BODY)
    mine = create_personal(env)
    other = create_personal(env, "N")
    aid = mine["agent_id"]
    assert mine["owner_user_id"] == "usr_ag_m0076" and mine["scope"] == "personal"
    assert system["owner_user_id"] is None and system["scope"] == "system"
    for who, expected in [("M", [aid]), ("N", [other["agent_id"]]), ("A", [])]:
        rows = env.client.get(ME, headers=env.h(who)).json()["agents"]
        assert [r["agent_id"] for r in rows] == expected
    assert [a["agent_id"] for a in env.client.get(API+"/system/agents", headers=env.h("A")).json()["agents"]] == [system["agent_id"]]
    actions = [("GET", "", None), ("PATCH", "", {"name": "stolen"}),
               ("POST", "/enrollments", {}), ("POST", "/credential/revoke", {}), ("DELETE", "", None)]
    for target in [aid, system["agent_id"]]:
        for method, suffix, body in actions:
            r = env.client.request(method, ME+"/"+target+suffix, json=body, headers=env.h("N"))
            assert r.status_code == 404, (method, target, r.text)
    for method, suffix, body in actions:
        r = env.client.request(method, API+"/system/agents/"+aid+suffix, json=body, headers=env.h("A"))
        assert r.status_code == 404, r.text
    assert env.client.patch(ME+"/"+aid, json={"name": "My renamed runner", "configured_capabilities": {"ai_cli": False, "ai_api": True, "storage": False}}, headers=env.h("M")).status_code == 200
    reread = env.client.get(ME+"/"+aid, headers=env.h("M")).json()
    assert reread["name"] == "My renamed runner" and reread["configured_capabilities"]["ai_cli"] is False
    assert env.client.delete(ME+"/"+aid, headers=env.h("M")).status_code == 204
    assert env.client.get(ME+"/"+aid, headers=env.h("M")).status_code == 404
    assert env.agent(system["agent_id"]).json() == system


def test_personal_enrollment_authentication_and_invalid_scope_fields(env):
    for who in ["anon", "garbage", "revoked"]:
        assert env.client.get(ME, headers=env.h(who)).status_code == 401
        assert env.client.post(ME, json=PERSONAL, headers=env.h(who)).status_code == 401
    for fields in [{"owner_user_id": "usr_ag_n0076"}, {"scope": "system"},
                   {"configured_capabilities": {"ai_cli": True, "ai_api": False, "storage": True}},
                   {"location": "local", "connection_mode": None}]:
        assert env.client.post(ME, json={**PERSONAL, **fields}, headers=env.h("M")).status_code == 422
    aid = create_personal(env)["agent_id"]
    for fields in [{"owner_user_id": "usr_ag_n0076"}, {"name": None}, {"name": " "}, {"enabled": None}, {"configured_capabilities": None},
                   {"configured_capabilities": {"ai_cli": True, "ai_api": False, "storage": True}}]:
        assert env.client.patch(ME+"/"+aid, json=fields, headers=env.h("M")).status_code == 422
    issued = env.client.post(ME+"/"+aid+"/enrollments", json={}, headers=env.h("M"))
    assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
    enrolled = env.enroll(issued.json()["enrollment_token"])
    assert enrolled.status_code == 201
    credential = enrolled.json()["agent_credential"]
    assert env.heartbeat(aid, credential).status_code == 200
    current = env.client.get(ME+"/"+aid, headers=env.h("M")).json()
    assert current["online_status"] == "online" and current["owner_user_id"] == "usr_ag_m0076"
    # flowgate.v02test.0019: a connected Agent with no Job in progress is deleted (was 409); its
    # credential is revoked in the same transaction, so it can no longer connect.
    assert env.client.delete(ME+"/"+aid, headers=env.h("M")).status_code == 204
    assert env.heartbeat(aid, credential).status_code == 401
    assert env.client.get(ME+"/"+aid, headers=env.h("M")).status_code == 404


def test_deleted_personal_agent_never_authenticates_or_enrolls(env):
    # flowgate.v02gui.0008: an enroll committing between delete()'s credential and enrollment
    # revocations can leave an active credential (and a consumed token) on a deleted Agent. Simulate
    # the survivors directly: neither may authenticate, heartbeat, claim nor enroll. History stays.
    from modules.flow_gate.api.v1.agent_job_routes import router
    env.client.app.include_router(router, prefix=API)
    aid = create_personal(env)["agent_id"]
    token = env.client.post(ME+"/"+aid+"/enrollments", json={}, headers=env.h("M")).json()["enrollment_token"]
    credential = env.enroll(token).json()["agent_credential"]
    assert env.heartbeat(aid, credential).status_code == 200
    pending = env.client.post(ME+"/"+aid+"/enrollments", json={}, headers=env.h("M")).json()["enrollment_token"]
    # The new token revoked nothing of the credential; delete revokes both.
    assert env.client.delete(ME+"/"+aid, headers=env.h("M")).status_code == 204
    env.store._execute("UPDATE agent_credentials SET revoked_at=NULL WHERE agent_id=?", [aid])
    env.store._execute("UPDATE agent_enrollments SET revoked_at=NULL WHERE agent_id=? AND consumed_at IS NULL", [aid])
    assert env.heartbeat(aid, credential).status_code == 401
    claim = env.client.post(API+"/agent/jobs/claim", json={}, headers={"Authorization": "Bearer "+credential})
    assert claim.status_code == 401
    assert env.enroll(pending).status_code == 409
    assert env.store._fetch_one("SELECT COUNT(*) n FROM agent_credentials WHERE agent_id=?", [aid])["n"] == 1
    assert env.store._fetch_one("SELECT deleted_at FROM agents WHERE agent_id=?", [aid])["deleted_at"]


def test_personal_jobs_require_requester_project_and_live_permissions(env):
    from modules.flow_gate.rbac import role_service
    from modules.flow_gate.api.v1.agent_job_routes import router
    env.client.app.include_router(router, prefix=API)
    # A system viewer role grants document.read globally in the deployed RBAC schema.
    # Remove that grant so these two accounts exercise project membership boundaries.
    for uid in ["usr_ag_m0076", "usr_ag_n0076"]:
        role_service.revoke_role(uid, role_service.SYSTEM_PROJECT)
    def enrolled_agent(who):
        agent = _create(env, REMOTE_BODY) if who == "A" else create_personal(env, who)
        base = API+"/system/agents" if who == "A" else ME
        token = env.client.post(base+"/"+agent["agent_id"]+"/enrollments", json={}, headers=env.h(who)).json()["enrollment_token"]
        cred = env.enroll(token).json()["agent_credential"]
        assert env.heartbeat(agent["agent_id"], cred).status_code == 200
        return agent["agent_id"], cred
    system, sys_cred = enrolled_agent("A")
    mine, my_cred = enrolled_agent("M")
    theirs, their_cred = enrolled_agent("N")
    svc = AgentJobService(env.store)
    def enqueue(**kw):
        return svc.enqueue_remote_cli(command_line="echo isolated", stdin_text="", cwd="/isolated", timeout_seconds=30, **kw)
    def claim(cred):
        return env.client.post(API+"/agent/jobs/claim", json={}, headers={"Authorization": "Bearer "+cred})
    system_job = enqueue()
    assert claim(my_cred).status_code == 204 and claim(their_cred).status_code == 204
    assert claim(sys_cred).json()["job"]["job_id"] == system_job["job_id"]
    for kw in [
        {"target_agent_id": mine},
        {"target_agent_id": mine, "requested_by_user_id": "usr_ag_n0076"},
        {"target_agent_id": system, "requested_by_user_id": "usr_ag_m0076"},
        {"requested_by_user_id": "usr_ag_n0076", "project_id": "p1_ag0076"},
    ]:
        with pytest.raises(AgentError) as exc:
            enqueue(**kw)
        assert exc.value.status == 403
    queued = enqueue(requested_by_user_id="usr_ag_m0076", project_id="p1_ag0076", target_agent_id=mine)
    assert claim(sys_cred).status_code == 204 and claim(their_cred).status_code == 204
    job = claim(my_cred).json()["job"]
    assert job["job_id"] == queued["job_id"]
    # Removing project access prevents starting an already claimed job.
    role_service.revoke_role("usr_ag_m0076", "p1_ag0076")
    response = env.client.post(API+f"/agent/jobs/{job['job_id']}/{job['attempt_id']}/started",
        json={"lease_token": job["lease_token"]}, headers={"Authorization": "Bearer "+my_cred})
    assert response.status_code == 403
    # Personal non-project work still belongs only to its requester.
    own = enqueue(requested_by_user_id="usr_ag_m0076")
    assert claim(my_cred).json()["job"]["job_id"] == own["job_id"]
    assert claim(their_cred).status_code == 204 and claim(sys_cred).status_code == 204
    env.store._execute("UPDATE users SET is_active=0 WHERE user_id=?", ["usr_ag_m0076"])
    assert claim(my_cred).status_code == 403
