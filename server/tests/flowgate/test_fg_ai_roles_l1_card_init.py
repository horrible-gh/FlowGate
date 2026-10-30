"""0113 T#2 I: creation hooks, immutable snapshots and guarded pending initialization."""
import pytest
from test_fg_card_settings import db_path,env,decided,FA,FB,_sql,_h,node,put_draft,placed


def save_defaults(uid="u_kim",pid="aip_card"):
    from flowgate.ai_roles import role_store as rs
    return rs.save_account_roles(uid,{r:rs.RoleValue("assigned",pid,"snapshot") for r in rs.ROLES})


def state(flow,key):
    from flowgate.ai_roles import role_store as rs
    from flowgate.card_settings import repository
    return rs.slots(flow,key),rs.card_init(flow,key),repository.settings_version(flow,key)


def test_created_card_kinds_and_resave_move_label_revision_do_not_recopy(env):
    from flowgate.ai_roles import role_store
    save_defaults()
    response=put_draft(env,FA,0,[node("tmp_t","T"),node("tmp_ts","TS"),node("tmp_wp","WP"),node("tmp_r","R"),node("tmp_g",None,"gate")])
    km=response["key_map"]
    for ref,count in [("tmp_t",3),("tmp_ts",3),("tmp_wp",2)]:
        slots,init,version=state(FA,km[ref])
        assert len(slots)==count and init.state=="initialized" and init.creator_id=="u_kim" and version==1
        assert all(s.source=="creation_copy" and s.set_by=="u_kim" and s.value.provider_name=="snapshot" for s in slots.values())
    for ref in ("tmp_r","tmp_g"): assert state(FA,km[ref])==({},None,0)
    before={k:state(FA,k) for k in km.values()}
    save_defaults(pid="aip_assignee")
    nodes=[node(k,typ,nt) for k,typ,nt in [(km["tmp_t"],"T","document"),(km["tmp_ts"],"TS","document"),
                                            (km["tmp_wp"],"WP","document"),(km["tmp_r"],"R","document"),(km["tmp_g"],None,"gate")]]
    nodes[0]["label"]="renamed"
    put_draft(env,FA,1,nodes)
    assert before=={k:state(FA,k) for k in km.values()}


def test_revision_confirmation_does_not_recopy_new_account_values(env,decided):
    from test_fg_card_settings import edge
    save_defaults()
    first=put_draft(env,FA,0,[node("tmp_r","R"),node("tmp_t","T")],[edge("tmp_e","tmp_r","tmp_t")])
    key=first["key_map"]["tmp_t"]; before=state(FA,key)
    save_defaults(pid="aip_assignee")
    confirm=env["client"].post(f"/api/v1/flows/{FA}/graph/revisions",json={"draft_version":1},headers=_h())
    assert confirm.status_code==201,confirm.text
    assert state(FA,key)==before


def test_second_creator_only_copies_new_keys(env):
    save_defaults(); key=placed(env,FA)
    save_defaults("u_lee","aip_assignee")
    before=state(FA,key)
    r=env["client"].put(f"/api/v1/flows/{FA}/graph/draft",headers={"Authorization":"Bearer user:u_lee"},
                         json={"draft_version":1,"nodes":[node(key),node("tmp_new")],"edges":[]})
    assert r.status_code==200,r.text
    new=r.json()["key_map"]["tmp_new"]
    assert state(FA,key)==before
    slots,init,version=state(FA,new)
    assert init.creator_id=="u_lee" and all(s.value.provider_id=="aip_assignee" for s in slots.values()) and version==1


def test_unset_malformed_and_failed_account_read_leave_pending_without_losing_draft(env,monkeypatch):
    from flowgate.ai_roles import role_store as rs
    key=placed(env,FA)
    assert state(FA,key)[1].state=="pending_init" and state(FA,key)[0]=={}
    bad=rs.AccountRoles({"instruction":rs.RoleValue("assigned",""),"execution":rs.RoleValue("not_used"),
                         "review":rs.RoleValue("assigned","deleted","saved")},"t","t")
    monkeypatch.setattr(rs,"account_roles",lambda uid:bad)
    r=put_draft(env,FB,0,[node("tmp_a")]); other=r["key_map"]["tmp_a"]
    assert set(state(FB,other)[0])=={"execution","review"} and state(FB,other)[1].state=="pending_init"
    def fail(*a): raise RuntimeError("broken account SELECT")
    monkeypatch.setattr(rs,"account_roles",fail)
    r=put_draft(env,FB,1,[node(other),node("tmp_b"),node("tmp_c","WP")])
    for ref in ("tmp_b","tmp_c"): assert state(FB,r["key_map"][ref])[1].state=="pending_init"


@pytest.mark.parametrize("change,cause",[("delete","deleted"),("disable","inactive")])
def test_copy_deleted_and_inactive_original_id_and_snapshot(env,change,cause):
    from flowgate.ai_roles import resolve
    from flowgate.card_settings import repository
    save_defaults()
    _sql(env["db"],"DELETE FROM ai_providers WHERE provider_id='aip_card'" if change=="delete" else "UPDATE ai_providers SET enabled=0 WHERE provider_id='aip_card'")
    key=placed(env,FA)
    slots,init,_=state(FA,key)
    assert init.state=="initialized"
    assert all(s.value.provider_id=="aip_card" and s.value.provider_name=="snapshot" for s in slots.values())
    result=resolve.resolve_role(FA,repository.card_node(FA,key),"execution",project_id="flowgate")
    assert result.outcome=="unavailable" and result.cause==cause


def test_pending_fill_uses_creator_only_preserves_explicit_and_completes_once(env):
    from flowgate.ai_roles import card_init,role_store as rs
    from flowgate.card_settings import fields,settings
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key=placed(env,FA)
    run=lambda:with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,fields.RUN))
    with pytest.raises(RuntimeError): card_init.initialize_pending_card(FA,key,fields.RUN)
    assert run()=="NO_CHANGE"
    settings.write({"group_id":FA,"project_id":"flowgate"},key,"review_role",{"kind":"not_used"},"u_lee")
    before=state(FA,key); review=before[0]["review"]
    save_defaults(pid="deleted"); save_defaults("u_lee","aip_assignee")
    assert run()=="CHANGED"
    slots,init,version=state(FA,key)
    assert slots["review"]==review and init.creator_id=="u_kim" and init.state=="initialized" and version==before[2]+1
    assert all(slots[r].source=="pending_fill" and slots[r].set_by is None and slots[r].value.provider_id=="deleted"
               for r in ("instruction","execution"))
    done=state(FA,key); assert run()=="NO_CHANGE" and state(FA,key)==done


def test_pending_fill_failure_rolls_back_all_and_no_creator_legacy_do_nothing(env,monkeypatch):
    from flowgate.ai_roles import card_init,role_store as rs
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key=placed(env,FA); save_defaults(); before=state(FA,key)
    original=rs.write_slot
    calls=[]
    def fail_second(*args):
        calls.append(1)
        if len(calls)==2: raise RuntimeError("slot failed")
        return original(*args)
    with monkeypatch.context() as m:
        m.setattr(rs,"write_slot",fail_second)
        with pytest.raises(RuntimeError,match="slot failed"):
            with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))
    assert state(FA,key)==before
    _sql(env["db"],"UPDATE graph_node_role_init SET creator_id=NULL WHERE group_id=? AND node_key=?",(FA,key))
    assert with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))=="NO_CHANGE"
    _sql(env["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
    assert with_flow_guard(FA,lambda:card_init.initialize_pending_card(FA,key,"run"))=="NO_CHANGE"


def test_creation_write_failure_rolls_back_draft_and_feature_off_port_absent(env,monkeypatch):
    from flowgate.ai_roles import role_store as rs
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    save_defaults()
    def fail(*a): raise RuntimeError("slot failed")
    with monkeypatch.context() as m:
        m.setattr(rs,"write_slot",fail)
        response=env["client"].put(f"/api/v1/flows/{FA}/graph/draft",json={"draft_version":0,"nodes":[node("tmp_x")],"edges":[]},headers=_h())
        assert response.status_code==500
    assert not _sql(env["db"],"SELECT * FROM graph_drafts WHERE group_id=?",(FA,))
    with monkeypatch.context() as m:
        m.setattr(rs,"available",lambda:False)
        key=placed(env,FA)
        assert state(FA,key)==({},None,0)
    ports.unregister(ports.CARDS_CREATED)
    key=placed(env,FB)
    assert state(FB,key)==({},None,0)


def test_account_failed_select_is_isolated_savepoint_and_outer_work_commits(env,monkeypatch):
    from flowgate.ai_roles import card_init,role_store
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    def broken(uid):
        get_store()._fetch_one("SELECT * FROM missing_account_table",[])
    monkeypatch.setattr(role_store,"account_roles",broken)
    def body():
        assert card_init._read_account("u_kim",FA,"k") is None
        get_store()._execute("INSERT INTO gate_data_migrations(name,applied_at) VALUES ('after-failed-select','t')")
    with_flow_guard(FA,body)
    assert _sql(env["db"],"SELECT * FROM gate_data_migrations WHERE name='after-failed-select'")


def test_failed_account_select_recovers_aborted_transaction_before_following_reads(monkeypatch):
    from contextlib import contextmanager
    from flowgate.ai_roles import card_init,role_store
    statements=[]
    class AbortingStore:
        aborted=False
        @contextmanager
        def transaction(self): yield self
        def _execute(self,sql):
            statements.append(sql)
            if sql.startswith("ROLLBACK TO"):
                self.aborted=False
            elif self.aborted:
                raise AssertionError("transaction still aborted")
    store=AbortingStore()
    def broken(uid):
        store.aborted=True
        raise RuntimeError("failed SELECT")
    monkeypatch.setattr(card_init,"get_store",lambda:store)
    monkeypatch.setattr(role_store,"account_roles",broken)
    assert card_init._read_account("creator","flow","node",True) is None
    assert statements==["SAVEPOINT fg_role_acc_read","ROLLBACK TO SAVEPOINT fg_role_acc_read","RELEASE SAVEPOINT fg_role_acc_read"]
    assert not store.aborted
