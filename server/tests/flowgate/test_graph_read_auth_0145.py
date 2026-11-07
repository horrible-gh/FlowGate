"""0145 T#3 boundary matrix: synthetic auth rows, real FastAPI routing and projections."""
import copy
import json
import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from flowgate.card_document_edit import call_ledger, chat_authz
from flowgate.graph_read import authz, routes, service, tools

FLOW = "flowgate.v02.0145"
DOC = FLOW + ".0001-B"


@pytest.fixture
def world(monkeypatch):
    token = dict(token_id="test-token-id", project="flowgate", group_id=FLOW, doc_ref=DOC,
                 issued_to="test-user", ai_run_id="test-ai", action_scope="chat",
                 expires_at="2099-01-01T00:00:00Z", source_access="read")
    run = dict(run_id="test-ai", current_token_id="test-token-id", project_id="flowgate",
               group_id=FLOW, doc_ref=DOC, issued_to="test-user", action_scope="chat", status="running",
               graph_exec_id="test-exec")
    mapped = dict(run_id="test-graph", node_key="test-node", attempt_id="test-attempt", map_kind="ai_start",
                  group_id=FLOW, run_status="running", attempt_status="active", active_node_key="test-node")
    execution = dict(exec_id="test-exec", ai_run_id="test-ai", status="running")
    group = dict(group_id=FLOW, project_id="flowgate")
    state = dict(token=token, run=run, mapped=mapped, execution=execution, group=group,
                 permission=True, reads=[], lookups=[])
    def verify(raw):
        if raw != "synthetic":
            raise HTTPException(401, "invalid")
        return state["token"]
    def lookup(flow):
        state["lookups"].append(flow)
        if state["group"] is None:
            authz.refuse("flow_not_found", 404)
        return state["group"]
    monkeypatch.setattr(authz.token_service, "verify", verify)
    monkeypatch.setattr(authz.tokens, "get_by_id", lambda _: state["token"])
    monkeypatch.setattr(chat_authz, "_read_token", lambda _: state["token"])
    monkeypatch.setattr(chat_authz, "_get_run_record", lambda _: state["run"])
    monkeypatch.setattr(authz.documents, "get_by_id", lambda _: dict(doc_id=DOC, project_id="flowgate", group_id=FLOW))
    monkeypatch.setattr(authz.canvas_service, "load_flow", lookup)
    monkeypatch.setattr(authz, "has_permission", lambda u,p,perm: state["permission"] and perm == "perm_document_read")
    monkeypatch.setattr(authz.run_store, "_one", lambda sql, args: state["mapped"] if "graph_run_token_map" in sql else state["execution"])
    monkeypatch.setattr(authz.run_store, "active_run", lambda _: dict(run_id="test-graph"))
    monkeypatch.setattr(service, "with_read_snapshot", lambda f: f())
    def read(scope, group, resource, view):
        state["reads"].append((scope, resource, view))
        if state.get("during"):
            state["during"]()
        return {"flow_id": scope.flow_id, "run": None}
    monkeypatch.setattr(service, "_read", read)
    app = FastAPI()
    app.include_router(routes.router)
    state["client"] = TestClient(app)
    return state


def get(w, resource="canvas", flow=FLOW, query="", auth=True):
    return w["client"].get("/api/v2/ai/flows/" + flow + "/" + resource + query,
                           headers={"Authorization": "Bearer synthetic"} if auth else {})


@pytest.mark.parametrize("resource", ["canvas", "runs/current"])
@pytest.mark.parametrize("kind", ["chat", "edit", "new", "review"])
def test_live_bound_read_is_allowed_without_write_kind(world, kind, resource):
    world["token"]["action_scope"] = world["run"]["action_scope"] = kind
    world["token"]["source_access"] = "none"
    response = get(world, resource)
    assert response.status_code == 200, response.text
    scope = world["reads"][0][0]
    assert scope.node_key == (None if kind == "chat" else "test-node")


@pytest.mark.parametrize("axis", ["project_id", "group_id", "run_id", "issued_to", "doc_ref", "current_token_id", "action_scope"])
@pytest.mark.parametrize("kind", ["chat", "edit"])
def test_every_token_run_axis_is_required(world, axis, kind):
    world["token"]["action_scope"] = world["run"]["action_scope"] = kind
    world["run"][axis] = "different"
    response = get(world)
    assert response.status_code == 403 and response.json()["error"]["code"] == "token_binding_mismatch"
    assert not world["reads"]


@pytest.mark.parametrize("state", ["finished", "failed", "cancelled", "cancelling", None, "event", "finished_at", "missing"])
@pytest.mark.parametrize("kind", ["chat", "edit"])
def test_dead_or_cancelled_runs_never_read(world, state, kind):
    world["token"]["action_scope"] = world["run"]["action_scope"] = kind
    if state == "event":
        world["run"]["cancel_event"] = threading.Event()
        world["run"]["cancel_event"].set()
    elif state == "finished_at":
        world["run"]["finished_at"] = "done"
    elif state == "missing":
        world["run"] = None
    else:
        world["run"]["status"] = state
    response = get(world)
    assert response.status_code == 409
    assert response.json()["error"]["code"] in ("run_finished", "run_cancelled")
    assert not world["reads"]


@pytest.mark.parametrize("field,value", [("revoked_at","x"), ("consumed_at","x"), ("expires_at","2000-01-01T00:00:00Z"), ("expires_at","invalid")])
def test_dead_tokens_are_unauthorized(world, field, value):
    world["token"][field] = value
    assert get(world).status_code == 401
    assert not world["lookups"] and not world["reads"]


@pytest.mark.parametrize("flow", ["flowgate.v02.9999", "elsewhere.v02.0145", "flowgate.v02.0001"])
def test_foreign_and_nonexistent_foreign_flow_indistinguishable_before_lookup(world, flow):
    response = get(world, flow=flow)
    assert response.status_code == 403 and response.json()["error"]["code"] == "flow_access_denied"
    assert not world["lookups"] and not world["reads"]


@pytest.mark.parametrize("case,expected,code", [
    ("missing_auth",401,"unauthorized"), ("kind",403,"token_kind_not_allowed"),
    ("permission",403,"flow_access_denied"), ("project",403,"flow_access_denied"),
    ("missing_group",404,"flow_not_found"), ("bad_id",422,"invalid_request"),
    ("bad_view",422,"invalid_request"), ("query",422,"invalid_request"),
    ("duplicate",422,"invalid_request")])
def test_error_classes_and_input_rejection(world, case, expected, code):
    kwargs = {}
    if case == "missing_auth": kwargs["auth"] = False
    elif case == "kind": world["token"]["action_scope"] = "command"
    elif case == "permission": world["permission"] = False
    elif case == "project": world["group"]["project_id"] = "other"
    elif case == "missing_group": world["group"] = None
    elif case == "bad_id": kwargs["flow"] = "bad id"
    elif case == "bad_view": kwargs["query"] = "?view=draft"
    elif case == "query": kwargs["query"] = "?node_key=other"
    elif case == "duplicate": kwargs["query"] = "?view=latest&view=revision"
    response = get(world, **kwargs)
    assert response.status_code == expected and response.json()["error"]["code"] == code
    assert not world["reads"]


@pytest.mark.parametrize("case", ["missing_map","legacy_map","foreign_map","no_attempt","ended_attempt",
    "invalidated_attempt","stale_node","ended_graph","stopping","no_execution","foreign_execution",
    "ended_execution","cancelled_execution","latest"])
def test_worker_map_attempt_and_execution_fences(world, case):
    world["token"]["action_scope"] = world["run"]["action_scope"] = "edit"
    query = ""
    if case == "missing_map": world["mapped"] = None
    elif case == "legacy_map": world["mapped"]["map_kind"] = "dispatch"
    elif case == "foreign_map": world["mapped"]["group_id"] = "foreign"
    elif case == "no_attempt": world["mapped"] = None
    elif case == "ended_attempt": world["mapped"]["ended_at"] = "x"
    elif case == "invalidated_attempt": world["mapped"]["attempt_status"] = "invalidated"
    elif case == "stale_node": world["mapped"]["active_node_key"] = None
    elif case == "ended_graph": world["mapped"]["finished_at"] = "x"
    elif case == "stopping": world["mapped"]["run_status"] = "stopping"
    elif case == "no_execution": world["execution"] = None
    elif case == "foreign_execution": world["execution"]["ai_run_id"] = "other"
    elif case == "ended_execution": world["execution"]["status"] = "ended"
    elif case == "cancelled_execution": world["execution"]["cancel_requested_at"] = "x"
    else: query = "?view=latest"
    response = get(world, query=query)
    assert response.status_code in (403,409)
    assert not world["reads"]


@pytest.mark.parametrize("change", ["permission","token","rotation","cancel","binding"])
def test_rechecks_after_projection_discard_stale_results(world, change):
    def during():
        if change == "permission": world["permission"] = False
        elif change == "token": world["token"]["revoked_at"] = "x"
        elif change == "rotation": world["run"]["current_token_id"] = "replacement"
        elif change == "cancel": world["run"]["status"] = "cancelling"
        else: world["run"]["group_id"] = "foreign"
    world["during"] = during
    response = get(world)
    assert response.status_code in (401,403,409)
    assert "flow_id" not in response.json()


@pytest.mark.parametrize("method", ["post","put","patch","delete"])
@pytest.mark.parametrize("path", ["canvas","runs/current","canvas/layout","runs","graph/draft"])
def test_no_write_or_execution_routes_exist(world, method, path):
    response = getattr(world["client"], method)("/api/v2/ai/flows/" + FLOW + "/" + path,
                                                headers={"Authorization":"Bearer synthetic"})
    assert response.status_code in (404,405)
    assert not world["reads"]


def test_api_tools_share_auth_boundary_and_accepted_call_identity(world, monkeypatch):
    from flowgate.conversation.integration import provide_chat_tools
    descriptor = next(d for d in provide_chat_tools(world["run"]) if d["name"] == "read_flow_canvas")
    ctx = call_ledger.accept_call(world["run"], {"id":"call-1","name":descriptor["name"],"input":{"flow_id":FLOW}})
    status, body = descriptor["call_handler"](world["run"], ctx)
    assert status == 200 and body["flow_id"] == FLOW
    status, body = descriptor["handler"](world["run"], {"flow_id":FLOW})
    assert status == 422 and body["error"]["code"] == "invalid_request"
    ctx = call_ledger.accept_call(world["run"], {"id":"call-2","name":descriptor["name"],"input":{"flow_id":"foreign"}})
    assert descriptor["call_handler"](world["run"], ctx)[0] == 403
    monkeypatch.setattr(tools, "tool_result_limit", lambda: 20)
    ctx = call_ledger.accept_call(world["run"], {"id":"call-3","name":descriptor["name"],"input":{"flow_id":FLOW}})
    assert descriptor["call_handler"](world["run"], ctx)[0] == 413
    assert "synthetic-secret" not in tools.cli_mention("http://example/flowgate/api/v1", "synthetic-secret")


def test_unexpected_errors_never_log_or_return_credentials(world, caplog, monkeypatch):
    def broken(*a):
        raise RuntimeError("synthetic-credential")
    monkeypatch.setattr(authz.token_service, "verify", broken)
    response = get(world)
    assert response.status_code == 500
    assert "synthetic-credential" not in response.text + caplog.text


@pytest.mark.parametrize("case", ["own", "foreign_run", "foreign_group", "instruction", "review", "missing", "loop"])
def test_result_edit_writer_requires_its_own_result_execution(monkeypatch, case):
    from types import SimpleNamespace
    from flowgate.card_settings import legacy_adapter as legacy
    run=dict(action_scope="edit", mode="single", graph_exec_id="exec",run_id="ai",group_id=FLOW)
    row=dict(ai_run_id="ai",kind="write",assignment_stage="result",group_id=FLOW)
    if case=="foreign_run": row["ai_run_id"]="foreign"
    elif case=="foreign_group": row["group_id"]="foreign"
    elif case=="instruction": row["assignment_stage"]="instruction"
    elif case=="review": row["kind"]="review"
    elif case=="missing": row=None
    elif case=="loop": run["document_review_loop"]={"stage":"rework"}
    monkeypatch.setattr(legacy,"get_store",lambda:SimpleNamespace(_fetch_one=lambda *a:row))
    assert legacy.is_result_writer(run) is (case=="own")
