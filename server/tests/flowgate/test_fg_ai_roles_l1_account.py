"""0113 T#2 A: account API, availability, preservation, feature off; no AI calls."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from test_fg_card_settings import db_path, env, _sql, FA


@pytest.fixture()
def role_api(env):
    from flowgate.ai_roles.routes import router
    from modules.flow_gate.auth.middleware import get_current_user
    app = FastAPI()
    app.include_router(router)
    def current(request: Request):
        return {"user_id": request.headers.get("X-User", "u_kim")}
    app.dependency_overrides[get_current_user] = current
    env["roles_client"] = TestClient(app)
    return env


def body(pid=None):
    return {"roles": {r: {"kind": "assigned", "provider_id": pid} if pid else {"kind": "not_used"}
                      for r in ("instruction", "execution", "review")}}


def test_unset_save_all_values_first_timestamp_and_account_isolation(role_api, monkeypatch):
    from flowgate.ai_roles import role_store
    c=role_api["roles_client"]
    first=c.get("/api/v1/me/ai-roles").json()
    assert not first["setup_completed"] and first["selectable_count"] == 2
    assert all(r == {"state":"unset", "value":None,"display_name":None,"cause":None} for r in first["roles"].values())
    assert all(set(o)=={"id","name","exec_type","kind","owner_scope"} for o in first["options"])
    monkeypatch.setattr(role_store, "_now", lambda: "2026-10-30T00:00:00+00:00")
    saved=c.put("/api/v1/me/ai-roles",json=body("aip_card"))
    assert saved.status_code==200, saved.text
    first=saved.json()
    assert first["setup_completed"] and all(v["state"]=="assigned_ok" for v in first["roles"].values())
    monkeypatch.setattr(role_store, "_now", lambda: "2026-10-30T01:00:00+00:00")
    again=c.put("/api/v1/me/ai-roles",json=body()).json()
    assert again["setup_completed_at"] == first["setup_completed_at"]
    assert again["updated_at"] != first["updated_at"]
    assert all(v["value"]=={"kind":"not_used","provider_id":None} for v in again["roles"].values())
    assert not c.get("/api/v1/me/ai-roles",headers={"X-User":"u_lee"}).json()["setup_completed"]
    assert c.put("/api/v1/me/ai-roles",json=body("aip_card"),headers={"X-User":"u_lee"}).status_code==200
    assert c.get("/api/v1/me/ai-roles").json()==again


def test_collect_all_validation_errors_and_no_partial_write(role_api):
    from flowgate.ai_roles import role_store
    c=role_api["roles_client"]
    response=c.put("/api/v1/me/ai-roles",json={"roles":{"instruction":{"kind":"assigned","provider_id":"  "},
                                                              "execution":{"kind":"not_used","provider_id":"aip_card"}}})
    assert response.status_code==422
    assert response.json()["detail"]=={"code":"validation_failed","errors":[
        {"field":"roles.instruction","reason":"malformed","detail":"provider_id_required"},
        {"field":"roles.execution","reason":"malformed","detail":"provider_id_not_allowed"},
        {"field":"roles.review","reason":"required"}]}
    assert role_store.account_roles("u_kim") is None
    response=c.put("/api/v1/me/ai-roles",json={"extra":1,"roles":{"other":1,"instruction":None,"execution":{},"review":{"kind":"UNKNOWN"}}})
    assert len(response.json()["detail"]["errors"])==5
    assert role_store.account_roles("u_kim") is None
    value=body(); value["roles"]["execution"]["provider_id"]=None
    assert c.put("/api/v1/me/ai-roles",json=value).status_code==200


@pytest.mark.parametrize("value,detail", [(1,"not_an_object"), ({},"kind_missing"),
    ({"kind":"bad"},"unknown_kind"), ({"kind":"assigned","provider_id":False},"provider_id_required"),
    ({"kind":"assigned","provider_id":" aip_card"},"provider_id_required"),
    ({"kind":"not_used","provider_id":"x"},"provider_id_not_allowed")])
def test_account_value_grammar_without_writes(monkeypatch,value,detail):
    from flowgate.ai_roles import account, role_store
    from fastapi import HTTPException
    monkeypatch.setattr(role_store,"available",lambda:True)
    monkeypatch.setattr(role_store,"account_roles",lambda u:None)
    sent=body(); sent["roles"]["execution"]=value
    with pytest.raises(HTTPException) as exc: account.parse_and_validate_save("u",sent)
    assert exc.value.detail["errors"]==[{"field":"roles.execution","reason":"malformed","detail":detail}]


def test_deleted_value_preserved_resave_and_zero_choices(role_api):
    from flowgate.ai_roles import role_store
    c=role_api["roles_client"]
    assert c.put("/api/v1/me/ai-roles",json=body("aip_card")).status_code==200
    snapshot=role_store.account_roles("u_kim").values["review"].provider_name
    _sql(role_api["db"],"DELETE FROM ai_providers")
    view=c.get("/api/v1/me/ai-roles").json()
    assert view["setup_completed"] and view["selectable_count"]==0 and view["options"]==[]
    assert view["roles"]["review"]["cause"]=="deleted"
    sent=body("aip_card"); sent["roles"]["execution"]={"kind":"not_used"}
    assert c.put("/api/v1/me/ai-roles",json=sent).status_code==200
    assert role_store.account_roles("u_kim").values["review"].provider_name==snapshot
    assert c.put("/api/v1/me/ai-roles",json=sent,headers={"X-User":"u_lee"}).status_code==422


def test_user_options_order_none_name_and_other_owner_rejection(role_api):
    c=role_api["roles_client"]
    for pid, owner in [("mine","u_kim"),("other","u_lee")]:
        _sql(role_api["db"],"INSERT INTO ai_providers (provider_id,name,exec_type,kind,enabled,owner_scope,owner_user_id,created_at,updated_at) VALUES (?, 'None','api','openai',1,'user',?,'t','t')",(pid,owner))
    options=c.get("/api/v1/me/ai-roles").json()["options"]
    assert [o["id"] for o in options]==["aip_assignee","aip_card","mine"]
    assert options[-1]["name"]=="None"
    assert c.put("/api/v1/me/ai-roles",json=body("mine")).json()["roles"]["review"]["state"]=="assigned_ok"
    assert c.put("/api/v1/me/ai-roles",json=body("other")).json()["detail"]["errors"][0]["cause"]=="not_selectable"
    _sql(role_api["db"],"UPDATE ai_providers SET enabled=0 WHERE provider_id='aip_card'")
    assert c.put("/api/v1/me/ai-roles",json=body("aip_card")).json()["detail"]["errors"][0]["cause"]=="inactive"


def test_feature_off_read_write_errors_and_registry_errors(role_api, monkeypatch):
    from flowgate.ai_roles import availability, role_store
    c=role_api["roles_client"]
    with monkeypatch.context() as m:
        m.setattr(role_store,"available",lambda:False)
        for method in (c.get,c.put):
            r=method("/api/v1/me/ai-roles")
            assert r.status_code==409 and r.json()=={"detail":{"code":"ai_roles_unavailable"}}
    def fail(*a,**k): raise RuntimeError("private diagnostic")
    for name in ("account_roles","save_account_roles"):
        with monkeypatch.context() as m:
            m.setattr(role_store,name,fail)
            r=c.put("/api/v1/me/ai-roles",json=body())
            assert r.status_code==503 and "private" not in r.text
    with monkeypatch.context() as m:
        m.setattr(availability,"lookup",fail)
        r=c.put("/api/v1/me/ai-roles",json=body("new"))
        assert r.status_code==422 and r.json()["detail"]["errors"][0]["cause"]=="registry_unreadable"
    monkeypatch.setattr(availability,"selectable_ais",fail)
    assert c.get("/api/v1/me/ai-roles").status_code==503


def test_actual_missing_163_tables_and_public_lookup_has_no_secrets(role_api):
    from modules.flow_gate.db import ai_providers
    row=ai_providers.get_public_row("aip_card")
    # flowgate.v02.0129 CH0012: where it runs (exec_location, target Agent id) is not a secret.
    assert set(row)=={"provider_id","name","exec_type","kind","enabled","owner_scope","owner_user_id",
                      "exec_location","target_agent_id"}
    _sql(role_api["db"],"DROP TABLE user_ai_role_defaults")
    assert role_api["roles_client"].get("/api/v1/me/ai-roles").status_code==409
    assert role_api["roles_client"].put("/api/v1/me/ai-roles",json=body()).status_code==409


def test_malformed_account_role_is_unset_without_resetting_completion(monkeypatch):
    from flowgate.ai_roles import account,availability,role_store as rs
    rec=rs.AccountRoles({"instruction":rs.RoleValue("assigned",""),"execution":rs.RoleValue("not_used"),
                         "review":rs.RoleValue("not_used")},"first","last")
    monkeypatch.setattr(rs,"available",lambda:True)
    monkeypatch.setattr(rs,"account_roles",lambda uid:rec)
    monkeypatch.setattr(availability,"selectable_ais",lambda uid:[])
    view=account.account_view("u")
    assert view["setup_completed"] and view["setup_completed_at"]=="first"
    assert view["roles"]["instruction"]=={"state":"unset","value":None,"display_name":None,"cause":None}

def test_ai_settings_deletion_keeps_role_defaults_and_slots(role_api,monkeypatch):
    from flowgate.ai_roles import role_store as rs
    from flowgate.card_settings import repository
    from modules.flow_gate.settings import ai_settings_service as svc,ai_execution_policy_service as policy
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from test_fg_card_settings import placed
    assert role_api["roles_client"].put("/api/v1/me/ai-roles",json=body("aip_card")).status_code==200
    key=placed(role_api,FA)
    account=rs.account_roles("u_kim"); slots=rs.slots(FA,key)
    monkeypatch.setattr(policy,"execution_policy_payload",lambda:{})
    saved=svc.save_system_settings([],None,None)
    assert saved["providers"]==[]
    assert rs.account_roles("u_kim")==account and rs.slots(FA,key)==slots
    assert role_api["roles_client"].get("/api/v1/me/ai-roles").json()["roles"]["execution"]["cause"]=="deleted"
