"""0113 T#2 W/B: S-1..20, P-1 §4/5, preservation, validation order and feature off."""
import pytest
from test_fg_card_settings import db_path,env,FA,FB,_sql,node,placed,setting,patch,get_settings,put_draft


def setup_legacy(env,typ="T",assignee=None):
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key=placed(env,FA,typ,assignee)
    _sql(env["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
    preserved={"instruction_ai":"aip_assignee","run_ai":"aip_card","review_ai":"aip_assignee","review_count":0,
               "review_basis":"last","instruction_review_ai":"aip_card","instruction_review_count":2}
    with_flow_guard(FA,lambda:[repository.put(FA,key,k,v,"u_kim") for k,v in preserved.items()])
    return key,preserved


def snapshot(key):
    from flowgate.ai_roles import role_store
    from flowgate.card_settings import repository
    return repository.values(FA,key),role_store.slots(FA,key),role_store.card_init(FA,key),repository.settings_version(FA,key)


@pytest.mark.parametrize("case",["S1","S2","S3","S4","S5","S6","S7","S8","S9","S10","S11","S12","S13","S14","S15","S16","S17","S18","S19","S20"])
def test_save_decision_table_s1_through_s20(env,case):
    from flowgate.ai_roles import role_store as rs,resolve,card_init
    from flowgate.card_settings import repository,settings
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    human={"kind":"human","user_id":"u_kim"} if case in ("S2","S3") else None
    if case in ("S15","S16","S19"):
        key=placed(env,FA); preserved={}
    else:
        key,preserved=setup_legacy(env,assignee=human)
    if case in ("S4","S5"):
        with_flow_guard(FA,lambda:repository.put(FA,key,"review_count",2,"u_kim"))
        preserved={**preserved,"review_count":2}
    if case=="S6":
        with_flow_guard(FA,lambda:[repository.remove(FA,key,name) for name in ("instruction_ai","run_ai")])
        preserved={k:v for k,v in preserved.items() if k not in ("instruction_ai","run_ai")}
    before=snapshot(key)
    if case in ("S1","S2","S3"):
        chosen="instruction_role" if case=="S2" else "execution_role"
        setting(env,FA,key,chosen,{"kind":"assigned","provider_id":"aip_assignee"})
        assert rs.card_init(FA,key) is None
        result=resolve.resolve_role(FA,repository.card_node(FA,key),"review")
        assert result.outcome=="no_review"
        if human:
            untouched="execution" if case=="S2" else "instruction"
            assert resolve.resolve_role(FA,repository.card_node(FA,key),untouched).outcome=="person"
            assert resolve.resolve_role(FA,repository.card_node(FA,key),"instruction" if case=="S2" else "execution").outcome=="ai"
    elif case in ("S4","S5"):
        setting(env,FA,key,"review_role",{"kind":"not_used"})
        assert resolve.resolve_role(FA,repository.card_node(FA,key),"review").outcome=="person"
        if case=="S5":
            setting(env,FA,key,"review_role",{"kind":"assigned","provider_id":"aip_assignee"})
            assert resolve.resolve_role(FA,repository.card_node(FA,key),"review").outcome=="ai"
    elif case=="S6":
        setting(env,FA,key,"time_limit",90)
        assert snapshot(key)[1:3]==before[1:3]
        preserved={**preserved,"time_limit":90}
        assert resolve.resolve_role(FA,repository.card_node(FA,key),"execution").outcome=="needs_setup"
    elif case=="S7":
        from test_fg_card_settings import _h
        from test_fg_card_settings_review import _write_written
        get_settings(env,FA,key)
        title=env["client"].put(f"/api/v1/flows/{FA}/graph/nodes/{key}/title",json={"title":"Manual title"},headers=_h())
        assert title.status_code==200,title.text
        doc=_sql(env["db"],"SELECT doc_id FROM graph_node_documents WHERE group_id=? AND node_key=? AND slot='instruction'",(FA,key))[0]["doc_id"]
        _write_written(doc,"## Body saved without changing roles")
        assert snapshot(key)==before
    elif case=="S8":
        setting(env,FA,key,"instruction","omit")
        assert snapshot(key)[1:3]==before[1:3]; preserved={**preserved,"instruction":"omit"}
    elif case=="S9":
        setting(env,FA,key,"review_role",{"kind":"not_used"})
        slots=rs.slots(FA,key)
        setting(env,FA,key,"review_count",2)
        preserved={**preserved,"review_count":2}
        assert rs.slots(FA,key)==slots
        assert resolve.resolve_role(FA,repository.card_node(FA,key),"review").outcome=="person"
    elif case=="S10":
        setting(env,FA,key,"review_role",{"kind":"assigned","provider_id":"aip_card"})
        assert repository.values(FA,key)["review_count"]==0
        assert resolve.resolve_role(FA,repository.card_node(FA,key),"review").outcome=="ai"
    elif case=="S11":
        setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_card"})
        before=snapshot(key)
        r=setting(env,FA,key,"execution_role",None,422)
        assert r["error"]["params"]["errors"][0]["reason"]=="role_unset_not_allowed" and snapshot(key)==before
    elif case=="S12":
        setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_card"})
        first=snapshot(key)
        _sql(env["db"],"DELETE FROM ai_providers WHERE provider_id='aip_card'")
        setting(env,FA,key,"run_ai","aip_card")
        assert snapshot(key)==first
    elif case=="S13":
        setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_card"})
        old=rs.slots(FA,key)["execution"]
        _sql(env["db"],"DELETE FROM ai_providers WHERE provider_id='aip_card'")
        setting(env,FA,key,"review_role",{"kind":"not_used"})
        assert rs.slots(FA,key)["execution"]==old
    elif case=="S14":
        _sql(env["db"],"UPDATE ai_providers SET enabled=0 WHERE provider_id='aip_assignee'")
        r=setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_assignee"},422)
        assert r["error"]["params"]["errors"][0]["cause"]=="inactive" and snapshot(key)==before
    elif case in ("S15","S16","S17"):
        setting(env,FA,key,"review_role",{"kind":"assigned","provider_id":"aip_assignee"} if case=="S15" else {"kind":"not_used"})
        if case=="S15":
            rs.save_account_roles("u_kim",{r:rs.RoleValue("assigned","aip_card","snap") for r in rs.ROLES})
            saved=rs.slots(FA,key)["review"]
            assert with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))=="CHANGED"
            assert rs.slots(FA,key)["review"]==saved
        elif case in ("S16","S17"):
            for field in ("instruction_role","execution_role"): setting(env,FA,key,field,{"kind":"not_used"})
            assert rs.card_init(FA,key).state=="initialized" and rs.card_init(FA,key).creator_id==("u_kim" if case=="S16" else None)
        else:
            assert rs.card_init(FA,key).state=="pending_init" and rs.card_init(FA,key).creator_id=="u_kim"
    elif case=="S18":
        setting(env,FA,key,"instruction_review_ai",None)
        preserved={k:v for k,v in preserved.items() if k!="instruction_review_ai"}; assert snapshot(key)[1:3]==before[1:3]
    else:
        if case=="S19":
            for name in ("instruction_role","execution_role"): setting(env,FA,key,name,{"kind":"not_used"})
            raw={"kind":"UNKNOWN"}; name="review_role"
        else:
            setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_card"})
            raw={"kind":"not_used","provider_id":"aip_card"}; name="execution_role"
        before=snapshot(key)
        setting(env,FA,key,name,raw,422)
        assert snapshot(key)==before
    assert repository.values(FA,key)==preserved


@pytest.mark.parametrize("name,raw,reason,detail",[
 ("execution_role",None,"role_unset_not_allowed",None),("run_ai",None,"role_unset_not_allowed",None),
 ("execution_role","aip_card","role_value_malformed","not_an_object"),
 ("run_ai",7,"role_value_malformed","not_an_object"),("run_ai"," ","role_value_malformed","provider_id_required"),
 ("execution_role",{},"role_value_malformed","kind_missing"),
 ("execution_role",{"kind":"UNKNOWN"},"role_value_malformed","unknown_kind"),
 ("execution_role",{"kind":"assigned","provider_id":True},"role_value_malformed","provider_id_required"),
 ("execution_role",{"kind":"not_used","provider_id":"x"},"role_value_malformed","provider_id_not_allowed"),
 ("execution_role",{"kind":"assigned","provider_id":"deleted"},"ai_not_selectable",None)])
def test_card_rejection_grammar_no_mutation(env,name,raw,reason,detail):
    key=placed(env,FA); before=snapshot(key)
    r=setting(env,FA,key,name,raw,422)
    expected={"field":"value","reason":reason}
    if detail: expected["detail"]=detail
    if reason=="ai_not_selectable": expected["cause"]="deleted"
    assert r["error"]["params"]["errors"]==[expected] and snapshot(key)==before


def test_versions_applicability_and_general_ai_legacy_contract(env):
    key=placed(env,FA)
    before=snapshot(key)
    r=patch(env,FA,key,{"field":"run_ai","value":None,"expected_settings_version":0},409)
    assert r["error"]["code"]=="gate_context_changed" and snapshot(key)==before
    setting(env,FA,key,"run_ai","aip_card")
    r=get_settings(env,FA,key)
    assert not r["fields"]["run_ai"]["stored"] and r["ai_roles"]["roles"]["execution"]["selected"]["provider_id"]=="aip_card"
    assert setting(env,FA,key,"instruction_review_ai","missing",422)["error"]["params"]["errors"][0]["reason"]=="unknown_ai"
    setting(env,FA,key,"instruction_review_ai","aip_card")
    setting(env,FA,key,"instruction_review_ai",None)
    assert not get_settings(env,FA,key)["fields"]["instruction_review_ai"]["stored"]
    ts=placed(env,FB,"TS")
    assert setting(env,FB,ts,"instruction_ai","aip_card",422)["error"]["params"]["errors"]==[
        {"field":"field","reason":"not_a_setting_of_this_card","card_kind":"test"}]


def test_feature_off_new_name_refused_old_names_keep_current_contract(env,monkeypatch):
    from flowgate.ai_roles import role_store
    monkeypatch.setattr(role_store,"available",lambda:False)
    key=placed(env,FA)
    r=setting(env,FA,key,"execution_role",{"kind":"not_used"},409)
    assert r["error"]["code"]=="card_roles_unavailable" and r["error"]["params"]=={"flow_id":FA,"node_key":key}
    assert snapshot(key)[1:]==({},None,0)
    r=setting(env,FA,key,"run_ai","aip_card")
    assert r["ai_roles"] is None and r["fields"]["run_ai"]["stored"]
    assert setting(env,FA,key,"run_ai","missing",422)["error"]["params"]["errors"][0]["reason"]=="unknown_ai"
    r=setting(env,FA,key,"run_ai",None)
    assert not r["fields"]["run_ai"]["stored"] and r["ai_roles"] is None


def test_blocks_p1_keys_preserved_selected_viewer_options_and_read_only(env,monkeypatch):
    from flowgate.ai_roles import availability,role_store as rs
    from flowgate.card_settings import repository,settings
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key,preserved=setup_legacy(env)
    initial=get_settings(env,FA,key)["ai_roles"]
    assert initial["card_init_state"]=="legacy" and initial["has_legacy_roles"]
    assert initial["roles"]["execution"]["selected"] is None
    for k,v in preserved.items(): assert initial["preserved"][k]==v
    assert set(initial["preserved"])=={"instruction_ai","run_ai","review_ai","review_count","review_basis",
                                        "instruction_review_ai","instruction_review_count","assignee_kind","assignee_provider_id"}
    for uid in ("u_kim","u_lee"):
        _sql(env["db"],"INSERT INTO ai_providers(provider_id,name,exec_type,kind,enabled,owner_scope,owner_user_id,created_at,updated_at) VALUES (?,?,'api','openai',1,'user',?,'t','t')",(uid,uid,uid))
    setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"u_kim"})
    before=snapshot(key)
    for uid in ("u_kim","u_lee"):
        r=env["client"].get(f"/api/v1/flows/{FA}/nodes/{key}/settings",headers={"Authorization":"Bearer user:"+uid}).json()["ai_roles"]
        assert [o["id"] for o in r["options"]][-1]==uid
        assert r["roles"]["execution"]["selected"]=={"kind":"assigned","provider_id":"u_kim"}
        assert r["roles"]["execution"]["cause"]=="personal_not_runnable"
        assert set(r)=={"card_init_state","has_legacy_roles","roles","preserved","options_state","options"}
        assert set(r["roles"]["execution"])=={"state","outcome","source","legacy_flag","provider_id","display_name","cause","selected"}
        assert all(set(o)=={"id","name","exec_type","kind","owner_scope"} for o in r["options"])
    assert snapshot(key)==before
    other=placed(env,FB,"WP")
    def fail(*a,**kw): raise RuntimeError("private details")
    monkeypatch.setattr(rs,"slots",fail); monkeypatch.setattr(availability,"selectable_ais",fail)
    r=get_settings(env,FB,other)["ai_roles"]
    assert r["card_init_state"] is None and not r["has_legacy_roles"] and r["options_state"]=="unreadable" and r["options"]==[]
    assert set(r["roles"])=={"execution","review"}
    assert all(x["source"]=="store_error" and x["cause"]=="store_error" and x["selected"] is None for x in r["roles"].values())


def test_no_roles_key_on_non_role_cards_and_patch_hold_has_block(env):
    key=placed(env,FA,"R")
    assert "ai_roles" not in get_settings(env,FA,key)
    other=placed(env,FB)
    assert "ai_roles" in setting(env,FB,other,"time_limit",90)
    assert "ai_roles" in setting(env,FB,other,"hold",True)


def test_optional_registry_read_failure_does_not_undo_a_successful_role_write(env,monkeypatch):
    from flowgate.ai_roles import availability,role_store as rs
    from modules.flow_gate.db.connection import get_store
    rs.save_account_roles("u_kim",{r:rs.RoleValue("assigned","aip_card","snapshot") for r in rs.ROLES})
    key=placed(env,FA); before=snapshot(key)
    store=get_store(); execute=store._execute; statements=[]
    def recording(sql,params=None):
        statements.append(sql)
        return execute(sql,params)
    def failed_lookup(pid):
        return store._fetch_one("SELECT * FROM missing_optional_registry",[])
    monkeypatch.setattr(store,"_execute",recording)
    monkeypatch.setattr(availability,"lookup",failed_lookup)
    result=setting(env,FA,key,"review_role",{"kind":"not_used"})
    assert result["ai_roles"]["roles"]["execution"]["cause"]=="registry_unreadable"
    assert result["ai_roles"]["roles"]["review"]["state"]=="not_used"
    assert snapshot(key)[3]==before[3]+1 and snapshot(key)[1]["review"].value.kind=="not_used"
    assert "ROLLBACK TO SAVEPOINT fg_role_avail_read" in statements
    assert "ROLLBACK TO SAVEPOINT fg_role_name_read" in statements

@pytest.mark.parametrize("code",["graph_run_locked","graph_node_protected","node_settings_locked"])
def test_role_writes_keep_existing_locks_before_value_validation(env,monkeypatch,code):
    from flowgate.card_settings import settings
    key=placed(env,FA); before=snapshot(key)
    if code=="node_settings_locked":
        monkeypatch.setattr(settings.legacy,"node_running",lambda *a:True)
    else:
        def refuse(*a): raise settings.legacy.GraphApiError(409,code,{"flow_id":FA,"node_key":key})
        monkeypatch.setattr(settings.editing,"require_editable",refuse)
    r=setting(env,FA,key,"execution_role",None,409)
    assert r["error"]["code"]==code and snapshot(key)==before


@pytest.mark.parametrize("cause",["not_selectable","inactive","registry_unreadable"])
def test_new_card_role_selection_rejections_keep_all_values(env,monkeypatch,cause):
    from flowgate.ai_roles import availability
    key=placed(env,FA); before=snapshot(key)
    monkeypatch.setattr(availability,"check_availability",lambda *a,**k:cause)
    r=setting(env,FA,key,"execution_role",{"kind":"assigned","provider_id":"aip_card"},422)
    assert r["error"]["params"]["errors"]==[{"field":"value","reason":"ai_not_selectable","cause":cause}]
    assert snapshot(key)==before


def test_p1_initialized_and_pending_test_block_examples(env):
    from flowgate.ai_roles import role_store as rs
    rs.save_account_roles("u_kim",{"instruction":rs.RoleValue("assigned","aip_card","snap"),
                                   "execution":rs.RoleValue("not_used"),"review":rs.RoleValue("assigned","aip_assignee","review")})
    run=placed(env,FA)
    r=get_settings(env,FA,run)["ai_roles"]
    assert r["card_init_state"]=="initialized" and not r["has_legacy_roles"]
    assert [r["roles"][k]["outcome"] for k in rs.ROLES]==["ai","person","ai"]
    assert all(not v["legacy_flag"] and v["source"]=="creation_copy" for v in r["roles"].values())
    _sql(env["db"],"DELETE FROM user_ai_role_defaults WHERE user_id='u_kim'")
    ts=placed(env,FB,"TS")
    setting(env,FB,ts,"review_role",{"kind":"assigned","provider_id":"aip_card"})
    _sql(env["db"],"DELETE FROM ai_providers WHERE provider_id='aip_card'")
    r=get_settings(env,FB,ts)["ai_roles"]
    assert r["card_init_state"]=="pending_init" and r["has_legacy_roles"]
    for k in ("instruction","execution"):
        assert r["roles"][k]=={"state":"unset","outcome":"needs_setup","source":"none","legacy_flag":True,
                               "provider_id":None,"display_name":None,"cause":None,"selected":None}
    assert r["roles"]["review"]["cause"]=="deleted"
    assert r["roles"]["review"]["selected"]=={"kind":"assigned","provider_id":"aip_card"}
