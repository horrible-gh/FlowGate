"""0113 T#2 D: WP role priority, read isolation, no duplicate hook, redeploy and off."""
import json
import pytest
from test_work_plan_deploy_0074 import db_path,env,dep,_flow,_write_plan,_execute,_preview,_doc_row,_sql,FA
from test_fg_card_settings import _h


def defaults(uid="u_kim",pid="aip_assignee"):
    from flowgate.ai_roles import role_store as rs
    rs.save_account_roles(uid,{r:rs.RoleValue("assigned",pid,"account snapshot") for r in rs.ROLES})


def plan(doc,instruction="aip_card",execution="aip_assignee",review="aip_card",first=False):
    from modules.flow_gate.services import work_plan_service as wp
    from modules.flow_gate.services.work_plan_card_stages import upgrade
    from modules.flow_gate.db import documents
    row=_doc_row(doc)
    if first:
        _write_plan(doc,{"T":1})
    body=upgrade(json.loads(wp.plan_path_for_doc(row).read_text(encoding="utf-8")))
    # Use the real plan bytes, validation and revision contract; no AI execution.
    inst,result=body["steps"]
    inst.update(provider_id=instruction,provider_display_name=instruction,review_count=0)
    result.update(provider_id=execution,provider_display_name=execution,review_count=1 if review else 0,
                  reviewer_provider_id=review,reviewer_provider_display_name=review)
    validated=wp.validate(body,project_id="flowgate",doc_id=doc,action="save",enforce_provider_scope=False)
    wp.write_body_atomically(wp.plan_path_for_doc(row),validated)
    documents.update(doc,{"revision_no":int(row.get("revision_no") or 0)+1})


def card_state(key):
    from flowgate.ai_roles import role_store
    from flowgate.card_settings import repository
    return role_store.slots(FA,key),role_store.card_init(FA,key),repository.settings_version(FA,key)


def test_first_deploy_all_plan_skips_account_and_draft_creation_hook(dep,monkeypatch):
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    _,doc,_=_flow(dep); plan(doc,first=True)
    calls=[]
    def forbidden(*a):
        calls.append(a)
        raise AssertionError("no account read or generic creation hook during WP deploy")
    monkeypatch.setattr(role_store,"account_roles",forbidden)
    monkeypatch.setattr(ports,"cards_created",forbidden)
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    assert calls==[]
    slots,init,version=card_state(key)
    assert len(slots)==3 and all(s.source=="wp_plan" and s.set_by=="u_kim" for s in slots.values())
    assert init.state=="initialized" and init.creator_id=="u_kim" and version==1
    assert len(_sql(dep["db"],"SELECT * FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key)))==1


@pytest.mark.parametrize("account_state",["ready","failed"])
def test_missing_plan_role_uses_account_or_pending_fill_after_read_failure(dep,monkeypatch,account_state):
    from flowgate.ai_roles import card_init,role_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    _,doc,_=_flow(dep); plan(doc,instruction=None,first=True)
    if account_state=="ready": defaults()
    with monkeypatch.context() as m:
        if account_state=="failed":
            def fail(*a): raise RuntimeError("account read failed")
            m.setattr(role_store,"account_roles",fail)
        [key]=_execute(dep,doc,"first")["created_node_keys"]
    slots,init,version=card_state(key)
    assert slots["execution"].source==slots["review"].source=="wp_plan"
    if account_state=="ready":
        assert slots["instruction"].source=="creation_copy" and slots["instruction"].value.provider_id=="aip_assignee" and init.state=="initialized"
    else:
        assert "instruction" not in slots and init.state=="pending_init"
        defaults()
        assert with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))=="CHANGED"
        now=card_state(key)
        assert now[0]["instruction"].source=="pending_fill" and now[0]["execution"]==slots["execution"]
        assert now[1].state=="initialized" and now[2]==version+1


def test_deleted_plan_ai_still_has_slot_while_old_fields_skip(dep):
    from flowgate.ai_roles import resolve
    from flowgate.card_settings import repository
    _,doc,_=_flow(dep); plan(doc,instruction="deleted",first=True)
    preview=_preview(dep,doc,"first")
    assert {"field":"instruction_ai","reason":"unknown_ai"} in preview["cards"][0]["settings_skipped"]
    [key]=_execute(dep,doc,"first",baseline=preview["baseline"])["created_node_keys"]
    assert "instruction_ai" not in repository.values(FA,key)
    assert card_state(key)[0]["instruction"].value.provider_id=="deleted"
    r=resolve.resolve_role(FA,repository.card_node(FA,key),"instruction",project_id="flowgate")
    assert (r.outcome,r.provider_id,r.cause)==("unavailable","deleted","deleted")


def test_redeploy_pending_full_plan_then_remove_review_never_recopies_account(dep):
    from flowgate.ai_roles import card_init,resolve
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    _,doc,_=_flow(dep); plan(doc,instruction=None,execution=None,review=None,first=True)
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    assert card_state(key)[1].state=="pending_init"
    plan(doc)
    _execute(dep,doc,"add")
    slots,init,version=card_state(key)
    assert len(slots)==3 and init.state=="initialized" and init.creator_id=="u_kim"
    defaults()
    plan(doc,review=None)
    _execute(dep,doc,"add")
    slots,init,version=card_state(key)
    assert "review" not in slots and init.state=="initialized" and init.creator_id=="u_kim"
    assert with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))=="NO_CHANGE"
    r=resolve.resolve_role(FA,repository.card_node(FA,key),"review")
    assert r.outcome=="needs_setup" and r.source=="none" and not r.legacy_flag


def test_redeploy_slot_only_difference_runs_and_stable_plan_no_change(dep):
    from flowgate.card_settings import settings,repository
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    _,doc,_=_flow(dep); plan(doc,first=True)
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    assert _execute(dep,doc,"add")["no_change"]
    settings.write({"group_id":FA,"project_id":"flowgate"},key,"execution_role",{"kind":"not_used"},"u_kim")
    before=repository.values(FA,key); version=card_state(key)[2]
    assert not _preview(dep,doc,"add")["no_change"]
    assert not _execute(dep,doc,"add")["no_change"]
    assert repository.values(FA,key)==before
    assert card_state(key)[0]["execution"].source=="wp_plan" and card_state(key)[2]==version+1
    assert _execute(dep,doc,"add")["no_change"]


def test_redeploy_plan_missing_preserves_other_sources_and_legacy_full_markers(dep):
    from flowgate.ai_roles import card_init,role_store as rs
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    _,doc,_=_flow(dep); plan(doc,instruction=None,first=True); defaults()
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    before=card_state(key)[0]["instruction"]
    plan(doc,instruction=None,execution="aip_card")
    _execute(dep,doc,"add")
    assert card_state(key)[0]["instruction"]==before
    _sql(dep["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
    # A legacy card becomes initialized only after all applicable roles have markers.
    with_flow_guard(FA,lambda:card_init.apply_wp_redeploy_roles(FA,key,"run","u_kim",{"execution":"aip_card","review":"aip_card"}))
    assert card_state(key)[1].state=="initialized" and card_state(key)[1].creator_id is None
    plan(doc)
    _execute(dep,doc,"add")
    assert card_state(key)[0]["instruction"].source=="wp_plan"


def test_started_cards_keep_slots_and_existing_blocker_only(dep,monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter
    _,doc,_=_flow(dep); plan(doc,first=True)
    [key]=_execute(dep,doc,"first")["created_node_keys"]; before=card_state(key)
    plan(doc,execution="aip_card")
    monkeypatch.setattr(legacy_adapter,"node_started",lambda *a:True)
    p=_preview(dep,doc,"add")
    assert p["blockers"] and {b["code"] for b in p["blockers"]}=={"card_stage_settings_locked"}
    # Existing execute order returns no_change before blockers when nothing can be written.
    assert _execute(dep,doc,"add",baseline=p["baseline"])["no_change"]
    assert card_state(key)==before


def test_feature_off_deploy_and_redeploy_leave_no_role_writes(dep,monkeypatch):
    from flowgate.ai_roles import role_store
    monkeypatch.setattr(role_store,"available",lambda:False)
    _,doc,_=_flow(dep); plan(doc,first=True)
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    assert card_state(key)==({},None,0)
    plan(doc,execution="aip_card")
    _execute(dep,doc,"add")
    assert card_state(key)==({},None,0)


def test_redeploy_preserves_explicit_slot_and_partial_legacy_state(dep,monkeypatch):
    from flowgate.ai_roles import card_init,role_store as rs
    from flowgate.card_settings import settings
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    _,doc,_=_flow(dep); plan(doc,instruction=None,first=True); defaults()
    [key]=_execute(dep,doc,"first")["created_node_keys"]
    settings.write({"group_id":FA,"project_id":"flowgate"},key,"instruction_role",{"kind":"not_used"},"u_kim")
    explicit=card_state(key)[0]["instruction"]
    plan(doc,instruction=None,execution="aip_card")
    reads=[]
    def failed_account(uid):
        reads.append(uid)
        raise RuntimeError("redeploy must not read account defaults")
    monkeypatch.setattr(rs,"account_roles",failed_account)
    _execute(dep,doc,"add")
    assert card_state(key)[0]["instruction"]==explicit and reads==[]
    _sql(dep["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
    _sql(dep["db"],"DELETE FROM graph_node_role_slots WHERE group_id=? AND node_key=? AND role='instruction'",(FA,key))
    before=card_state(key)
    assert with_flow_guard(FA,lambda:card_init.apply_wp_redeploy_roles(FA,key,"run","u_kim",{"execution":"aip_card","review":"aip_card"}))=="NO_CHANGE"
    assert card_state(key)==before and card_state(key)[1] is None
