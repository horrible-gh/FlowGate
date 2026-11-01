"""0113 T#2 V/X: every legacy truth-table row and slot/availability precedence."""
import pytest
from test_fg_card_settings import db_path, env, FA, placed


@pytest.mark.parametrize("row,kind,role,stored,node,outcome,pid,source", [
 ("I1","run","instruction",{}, {"assignee_kind":"bad"},"needs_setup",None,"store_error"),
 ("I2","run","instruction",{"instruction_ai":4},{"assignee_kind":"human"},"person",None,"legacy_fields"),
 ("I3","run","instruction",{"instruction_ai":4},{},"needs_setup",None,"store_error"),
 ("I4","run","instruction",{"instruction_ai":"A","run_ai":"X"},{},"ai","A","legacy_fields"),
 ("I5","run","instruction",{"instruction_ai":"","run_ai":" "},{},"needs_setup",None,"store_error"),
 ("I6","run","instruction",{"run_ai":"X"},{},"ai","X","legacy_fields"),
 ("I7","run","instruction",{},{"assignee_kind":"ai","assignee_provider_id":"P"},"ai","P","legacy_fields"),
 ("I8","run","instruction",{},{"assignee_kind":"ai","assignee_provider_id":3},"needs_setup",None,"legacy_fields"),
 ("I9","run","instruction",{},{},"needs_setup",None,"legacy_fields"),
 ("E1","run","execution",{}, {"assignee_kind":"bad"},"needs_setup",None,"store_error"),
 ("E2","run","execution",{"run_ai":"X"},{"assignee_kind":"human"},"person",None,"legacy_fields"),
 ("E3","run","execution",{"run_ai":False},{},"needs_setup",None,"store_error"),
 ("E4","run","execution",{"run_ai":"X"},{},"ai","X","legacy_fields"),
 ("E5","run","execution",{},{"assignee_kind":"ai","assignee_provider_id":"P"},"ai","P","legacy_fields"),
 ("E6","plan","execution",{},{"assignee_kind":"ai","assignee_provider_id":None},"needs_setup",None,"legacy_fields"),
 ("E7","plan","execution",{},{},"person",None,"legacy_fields"),
 ("E8","test","execution",{},{},"needs_setup",None,"legacy_fields"),
 ("R1","run","review",{"review_ai":4},{},"no_review",None,"legacy_fields"),
 ("R2","run","review",{"review_count":True},{},"needs_setup",None,"store_error"),
 ("R3","run","review",{"review_count":0,"review_ai":4},{},"no_review",None,"legacy_fields"),
 ("R4","run","review",{"review_count":2,"review_ai":" "},{},"needs_setup",None,"store_error"),
 ("R5","run","review",{"review_count":-5,"review_ai":"B"},{},"ai","B","legacy_fields"),
 ("R6","run","review",{"review_count":1},{},"needs_setup",None,"legacy_fields"),
 ("C2I","run","instruction",{"run_ai":"X"},None,"needs_setup",None,"store_error"),
 ("C2E","run","execution",{"run_ai":"X"},None,"needs_setup",None,"store_error"),
 ("C2R","run","review",{"review_count":2,"review_ai":"B"},None,"ai","B","legacy_fields"),
 ("TEST-I","test","instruction",{"instruction_ai":"A","run_ai":"X"},{},"ai","X","legacy_fields"),
])
def test_all_legacy_truth_table_rows(monkeypatch,row,kind,role,stored,node,outcome,pid,source):
    from flowgate.ai_roles import availability, resolve
    monkeypatch.setattr(availability,"check_availability",lambda *a,**k:None)
    result=resolve.resolve_legacy(kind,role,resolve.CardRoleInputs(stored=stored,node=node))
    assert (result.outcome,result.provider_id,result.source,result.legacy_flag)==(outcome,pid,source,True),row


@pytest.mark.parametrize("kind,stored,node,wanted", [
 ("run",{}, {},("needs_setup","needs_setup","no_review")),
 ("plan",{}, {},("not_applicable","person","no_review")),
 ("run",{"run_ai":"X","instruction_ai":"A"},{"assignee_kind":"human"},("person","person","no_review")),
 ("run",{"run_ai":"X","review_count":0,"review_ai":"B"},{},("ai","ai","no_review")),
 ("run",{"instruction_ai":"A","run_ai":"X","review_count":2,"review_ai":"B"},{},("ai","ai","ai")),
 ("run",{},{"assignee_kind":"ai","assignee_provider_id":"P"},("ai","ai","no_review")),
 ("run",{"review_count":1},{},("needs_setup","needs_setup","needs_setup")),
 ("run",{"run_ai":"deleted"},{},("unavailable","unavailable","no_review")),
 ("run",{"review_count":"2"},{},("needs_setup","needs_setup","needs_setup"))])
def test_nine_required_examples(monkeypatch,kind,stored,node,wanted):
    from flowgate.ai_roles import availability, resolve
    monkeypatch.setattr(availability,"check_availability",lambda p,*a,**k:"deleted" if p=="deleted" else None)
    n={"node_key":"k","node_type":"document","doc_type":{"run":"T","plan":"WP"}[kind]}
    inp=resolve.CardRoleInputs(stored=stored,node=node)
    assert tuple(resolve.resolve_role("f",n,r,inputs=inp).outcome for r in ("instruction","execution","review"))==wanted


@pytest.mark.parametrize("init",[None,"pending_init","initialized"])
def test_slots_override_all_legacy_fields_and_no_cross_role_inheritance(monkeypatch,init):
    from flowgate.ai_roles import availability,resolve,role_store as rs
    monkeypatch.setattr(availability,"check_availability",lambda *a,**k:None)
    slot=rs.RoleSlot("execution",rs.RoleValue("assigned","X"),"card_explicit","u","t")
    n={"node_key":"k","node_type":"document","doc_type":"T"}
    inp=resolve.CardRoleInputs(slots={"execution":slot},init=rs.CardInit(init,"u","t",None) if init else None,
                               stored={"run_ai":4},node={"assignee_kind":"human"})
    assert resolve.resolve_role("f",n,"execution",inputs=inp).outcome=="ai"
    r=resolve.resolve_role("f",n,"instruction",inputs=inp)
    assert r.outcome==("person" if init is None else "needs_setup")
    assert r.pending_init==(init=="pending_init")
    inp.slots["execution"]=rs.RoleSlot("execution",rs.RoleValue("assigned",""),"card_explicit","u","t")
    assert resolve.resolve_role("f",n,"execution",inputs=inp).source=="store_error"


@pytest.mark.parametrize("row,allowed,cause",[(None,None,"deleted"),
 ({"enabled":False},set(),"inactive"),
 ({"enabled":True,"owner_scope":"user"},set(),"not_allowed_in_project"),
 ({"enabled":True,"owner_scope":"user"},None,"personal_not_runnable"),
 ({"enabled":True,"owner_scope":"system","name":"None"},None,None)])
def test_card_availability_order(monkeypatch,row,allowed,cause):
    from flowgate.ai_roles import availability
    monkeypatch.setattr(availability,"lookup",lambda p:row)
    monkeypatch.setattr(availability.workspace_service,"allowed_resource_ids",lambda *a:allowed)
    assert availability.check_availability("X","card",project_id="p")==cause


def test_registry_exception_is_unavailable_without_loss(monkeypatch):
    from flowgate.ai_roles import availability,resolve
    def fail(*a,**k): raise RuntimeError("secret")
    monkeypatch.setattr(availability,"lookup",fail)
    r=resolve.with_availability("execution","X","saved","creation_copy","p")
    assert (r.outcome,r.cause,r.original)==("unavailable","registry_unreadable",{"provider_id":"X","name":"saved"})


@pytest.mark.parametrize("failure",["unavailable","slots","init","values","node"])
def test_store_error_and_node_definition_failure(env,monkeypatch,failure):
    from flowgate.ai_roles import resolve,role_store
    from flowgate.card_settings import repository
    key=placed(env,FA)
    node={"node_key":key,"node_type":"document","doc_type":"T"}
    def fail(*a,**k): raise RuntimeError("read failed")
    if failure=="unavailable": monkeypatch.setattr(repository,"available",lambda:False)
    elif failure in ("slots","init"): monkeypatch.setattr(role_store,"slots" if failure=="slots" else "card_init",fail)
    elif failure=="values": monkeypatch.setattr(repository,"values",fail)
    else:
        # C-2 is a legacy-card row; the pending state otherwise wins before legacy resolution.
        from test_fg_card_settings import _sql
        _sql(env["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
        monkeypatch.setattr(repository,"card_node",fail)
    assert resolve.resolve_role(FA,node,"execution").source=="store_error"
    if failure=="node": assert resolve.resolve_role(FA,node,"review").outcome=="no_review"


def test_assignment_wins_and_read_errors_propagate(monkeypatch):
    from flowgate.ai_roles import resolve,assignment_store
    from flowgate.card_settings import repository
    n={"node_key":"k","node_type":"document","doc_type":"T"}
    monkeypatch.setattr(assignment_store,"available",lambda:True)
    def forbidden(*a,**k): raise AssertionError("must not read card settings")
    monkeypatch.setattr(repository,"available",forbidden)
    for actor,expected in [("ai",True),("person",False)]:
        monkeypatch.setattr(assignment_store,"latest",lambda *a:{"actor":actor})
        assert resolve.card_execution_is_ai("f",n,"attempt")==expected
    def fail(*a,**k): raise RuntimeError("assignment failed")
    monkeypatch.setattr(assignment_store,"latest",fail)
    with pytest.raises(RuntimeError,match="assignment failed"): resolve.card_execution_is_ai("f",n,"attempt")
    assert resolve.card_execution_is_ai("f",{"node_type":"gate","assignee_kind":"ai"},"attempt")


def test_no_assignment_fallback_read_only_and_port_remains_existing(env,monkeypatch):
    from flowgate.ai_roles import resolve,role_store
    from flowgate.card_settings import repository,settings
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key=placed(env,FA)
    from test_fg_card_settings import _sql
    _sql(env["db"],"DELETE FROM graph_node_role_init WHERE group_id=? AND node_key=?",(FA,key))
    with_flow_guard(FA,lambda:repository.put(FA,key,"run_ai","aip_card","u_kim"))
    n=repository.card_node(FA,key)
    before=(repository.values(FA,key),repository.settings_version(FA,key),role_store.slots(FA,key),role_store.card_init(FA,key))
    assert resolve.card_execution_is_ai(FA,n)
    assert resolve.resolve_role(FA,n,"instruction").outcome=="ai"
    assert before==(repository.values(FA,key),repository.settings_version(FA,key),role_store.slots(FA,key),role_store.card_init(FA,key))
    assert ports._handler(ports.USES_AI) is settings.uses_ai
    monkeypatch.setattr(repository,"available",lambda:False)
    assert not resolve.card_execution_is_ai(FA,n)


def test_optional_read_restores_aborted_transaction_before_reraising(monkeypatch):
    from flowgate.ai_roles import legacy_adapter
    statements=[]
    class Store:
        aborted=False
        def _execute(self,sql):
            statements.append(sql)
            if sql.startswith("ROLLBACK TO"):
                self.aborted=False
            elif self.aborted:
                raise AssertionError("transaction still aborted")
    store=Store()
    monkeypatch.setattr(legacy_adapter,"in_transaction",lambda:True)
    monkeypatch.setattr(legacy_adapter,"get_store",lambda:store)
    def failed():
        store.aborted=True
        raise RuntimeError("failed optional SELECT")
    with pytest.raises(RuntimeError,match="failed optional SELECT"):
        legacy_adapter.isolated_read(failed,"fg_role_test_read")
    assert not store.aborted
    assert statements==["SAVEPOINT fg_role_test_read","ROLLBACK TO SAVEPOINT fg_role_test_read","RELEASE SAVEPOINT fg_role_test_read"]

# Reuse T#1's real SQLite assignment fixture to exercise the durable R-5 path.
from test_fg_ai_roles_db1_stores import env as assignment_env, _assignment


def test_actual_durable_result_assignment_wins_over_card_store_failure(assignment_env,monkeypatch):
    from flowgate.ai_roles import assignment_store,resolve
    from flowgate.card_settings import repository
    with assignment_env["store"].transaction():
        assignment_store.insert_if_absent(_assignment())
    def fail(*a): raise RuntimeError("card settings broken")
    monkeypatch.setattr(repository,"available",fail)
    assert resolve.card_execution_is_ai("g1",{"node_key":"n1","node_type":"document","doc_type":"T"},"a1")


def test_initialized_roles_ignore_star_chain_personal_and_project_defaults(monkeypatch):
    from flowgate.ai_roles import availability,resolve,role_store as rs
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import ai_providers
    from modules.flow_gate.settings import ai_settings_service
    node={"node_key":"k","node_type":"document","doc_type":"T"}
    slot=rs.RoleSlot("execution",rs.RoleValue("assigned","explicit"),"card_explicit","u","t")
    inputs=resolve.CardRoleInputs(slots={"execution":slot},init=rs.CardInit("initialized",None,"t","t"),
                                  stored={"instruction_ai":"old_instruction","run_ai":"old_run","review_ai":"old_review","review_count":2},
                                  node={"assignee_kind":"ai","assignee_provider_id":"old_assignee"})
    monkeypatch.setattr(availability,"check_availability",lambda *a,**k:None)
    touched=[]
    for pid in ("default_A","default_B"):
        def default(*a,**k):
            touched.append(pid)
            return pid
        monkeypatch.setattr(ai_providers,"get_system_default_provider_id",default)
        monkeypatch.setattr(ai_settings_service,"resolve_global",default)
        monkeypatch.setattr(ai_settings_service,"resolve_effective",default)
        monkeypatch.setattr(ai_settings_service._user_default_db,"get",default)
        monkeypatch.setattr(legacy_adapter,"default_reviewer",default)
        results=[resolve.resolve_role("f",node,r,inputs=inputs) for r in rs.ROLES]
        assert [(r.outcome,r.provider_id) for r in results]==[("needs_setup",None),("ai","explicit"),("needs_setup",None)]
    assert touched==[]
