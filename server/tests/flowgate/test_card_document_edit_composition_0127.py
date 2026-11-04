"""0127 T#1 rework: exercise the real composition root and its shared middleware.

The reference app executes the same root with only the new router registration
omitted. The historical clean-ref run of all 55 legacy tests is recorded in TR0013.
No server lifespan/bootstrap runs; data and storage come from the isolated fixture.
"""
import runpy
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Match

from test_card_document_edit_0127 import env, F, payload, url  # noqa: F401


def _call_name(call):
    return (getattr(call, "__module__", None), getattr(call, "__qualname__", None))


def _dependencies(dep):
    if dep is None:
        return None
    fields = tuple((kind, tuple((f.name, f.alias, f.required, str(f.type_))
                               for f in getattr(dep, kind, ())))
                   for kind in ("path_params", "query_params", "header_params", "cookie_params", "body_params"))
    return (_call_name(dep.call), dep.name, dep.use_cache, fields,
            tuple(_dependencies(child) for child in dep.dependencies))


def _route_signature(route):
    return (route.path, tuple(sorted(route.methods or ())), _call_name(route.endpoint),
            _dependencies(getattr(route, "dependant", None)),
            getattr(route, "mutation_resource", None), getattr(route, "group_resolver", None),
            getattr(route, "openapi_extra", None))


@pytest.fixture
def composition(env, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
    monkeypatch.setenv("CONTEXT", "/flowgate")
    monkeypatch.setenv("DB_TYPE", "sqlite")
    from config import settings
    from flowgate.card_document_edit import routes as edit_routes
    from modules.flow_gate.services import mutation_policy

    monkeypatch.setattr(settings, "CONTEXT", "/flowgate")
    monkeypatch.setattr(settings, "ALLOWED_ORIGIN", "http://localhost")
    # Preserve the actual middleware chain; isolate its policy inputs, as with auth
    # and the document service inputs in env. Denial is tested separately below.
    monkeypatch.setattr(mutation_policy, "principal_from_request",
                        lambda request: mutation_policy.MutationPrincipal("human", user_id="user"))
    checks = []
    monkeypatch.setattr(mutation_policy, "assert_group_mutation_allowed",
                        lambda group, principal, operation: checks.append((group, operation)))
    include = FastAPI.include_router
    omitted = []

    def without_edit(self, router, *args, **kwargs):
        if router is edit_routes.router:
            omitted.append(router)
            return None
        return include(self, router, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(FastAPI, "include_router", without_edit)
        reference = runpy.run_module("routers.main", run_name="routers._composition_0127")["app"]
    current = runpy.run_module("routers.main", run_name="routers._composition_0127")["app"]
    assert omitted == [edit_routes.router]
    return reference, current, checks


def test_main_keeps_legacy_routes_dependencies_and_middleware(composition):
    reference, current, _ = composition
    from flowgate.card_document_edit import routes as edit_routes, chat_routes

    endpoints = (edit_routes.get_document, edit_routes.post_revision,
                 # 0127 T#5 (L#2 §2.15): the structured TS projection reads and revisions, still /api/v2.
                 edit_routes.get_test_spec, edit_routes.get_test_brief,
                 edit_routes.post_test_spec_revision, edit_routes.post_test_brief_revision,
                 chat_routes.get_document, chat_routes.post_revision,
                 # 0127 CH0028: chat doc_id read / existing-body revision / new-document create.
                 chat_routes.get_document_by_id, chat_routes.post_document_revision, chat_routes.post_document)
    added = [r for r in current.routes if isinstance(r, APIRoute) and r.endpoint in endpoints]
    assert len(added) == 11
    expected = "/flowgate/api/v2/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document"
    assert {(r.path, tuple(sorted(r.methods))) for r in added} == {
        (expected, ("GET",)), (expected + "/revisions", ("POST",)),
        (expected + "/test-spec", ("GET",)), (expected + "/test-brief", ("GET",)),
        (expected + "/test-spec/revisions", ("POST",)), (expected + "/test-brief/revisions", ("POST",)),
        (expected.replace("/api/v2/", "/api/v2/chat/"), ("GET",)),
        (expected.replace("/api/v2/", "/api/v2/chat/") + "/revisions", ("POST",)),
        ("/flowgate/api/v2/chat/documents/{doc_id}", ("GET",)),
        ("/flowgate/api/v2/chat/documents/{doc_id}/revisions", ("POST",)),
        ("/flowgate/api/v2/chat/documents", ("POST",))}
    before = [r for r in reference.routes if isinstance(r, APIRoute)]
    after = [r for r in current.routes if isinstance(r, APIRoute) and r not in added]
    assert [_route_signature(r) for r in before] == [_route_signature(r) for r in after]
    assert current.dependency_overrides == reference.dependency_overrides == {}
    assert [(m.cls, m.args, m.kwargs) for m in current.user_middleware] == [
        (m.cls, m.args, m.kwargs) for m in reference.user_middleware]
    assert [m.cls.__name__ for m in current.user_middleware] == [
        "CORSMiddleware", "GroupMutationPolicyMiddleware", "RequestScopeMiddleware", "SlowAPIMiddleware"]
    assert {k: _call_name(v) for k, v in current.exception_handlers.items()} == {
        k: _call_name(v) for k, v in reference.exception_handlers.items()}
    for new in added:
        assert not any(new.path == old.path and new.methods & old.methods for old in before)
        concrete = new.path.replace("{flow_id}", F).replace("{node_key}", "research").replace("{slot}", "instruction")
        scope = {"type": "http", "path": concrete, "method": next(iter(new.methods)), "root_path": ""}
        first = next(r for r in current.routes if r.matches(scope)[0] is Match.FULL)
        assert first is new
    for posted in (r for r in added if "POST" in r.methods and "/flows/" in r.path):
        assert posted.mutation_resource == "system"
        assert posted.mutation_reason == "explicit_non_group_route"
        assert posted.group_resolver is None  # Flow-coordinate routes enforce policy inside the service.
    # 0127 CH0028: the chat doc_id routes keep the standard group resolver (lease check in front of
    # the service, which re-checks the lease owner itself).
    for posted in (r for r in added if "POST" in r.methods and "/chat/documents" in r.path):
        assert posted.mutation_resource == "group"
        assert posted.mutation_reason == "standard_group_resolver"
    print(f"COMPOSITION: {len(before)} legacy API routes unchanged; 11 distinct v2 routes (including chat); 4 middleware unchanged")


@pytest.mark.parametrize("method,path", [
    ("POST", "/flowgate/api/v1/flows/{flow}/nodes/research/slots/instruction/document"),
    ("GET", "/flowgate/api/v2/flows/{flow}/nodes/research/slots/instruction/document"),
    ("GET", "/flowgate/api/v2/chat/flows/{flow}/nodes/research/slots/instruction/document"),
    ("POST", "/flowgate/api/v2/chat/flows/{flow}/nodes/research/slots/instruction/document/revisions"),
    ("POST", "/flowgate/api/v2/flows/{flow}/nodes/research/slots/instruction/document/revisions"),
])
def test_main_routes_require_auth(composition, method, path):
    reference, current, _ = composition
    path = path.format(flow=F)
    response = TestClient(current).request(method, path, json={})
    assert response.status_code == 401, response.text
    if "/v1/" in path:
        old = TestClient(reference).request(method, path, json={})
        assert old.status_code == response.status_code
        assert old.json()["error"] == response.json()["error"]


def test_main_dispatches_v1_create_and_v2_read_revise_through_shared_stack(env, composition, monkeypatch):
    reference, current, checks = composition
    from flowgate.card_documents import compose, legacy_adapter
    from modules.flow_gate.api import request_scope_middleware

    observed = []
    scope = request_scope_middleware.request_cache.request_scope

    @contextmanager
    def record_scope(label):
        observed.append(label)
        with scope(label) as value:
            yield value

    monkeypatch.setattr(request_scope_middleware.request_cache, "request_scope", record_scope)
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda *args: True)
    calls = []

    def create(group, node_key, slot, content, user_id, locale):
        calls.append((group["group_id"], node_key, slot, content, user_id))
        return {"transport": "legacy_create"}

    monkeypatch.setattr(compose, "write_line", create)
    path = f"/flowgate/api/v1/flows/{F}/nodes/research/slots/instruction/document"
    headers = {"Authorization": "Bearer user", "Origin": "http://localhost"}
    for app in (reference, current):
        response = TestClient(app).post(path, json={"content": "legacy"}, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["transport"] == "legacy_create"
        assert response.headers["access-control-allow-origin"] == "http://localhost"
    assert calls == [(F, "research", "instruction", "legacy", "user")] * 2
    assert checks == []  # Existing v1 card-create policy remains handler-owned.
    client = TestClient(current)
    before = client.get(url(env.req), headers=headers)
    assert before.status_code == 200 and before.json()["revision_no"] == 0
    saved = client.post(url(env.req, True), json=payload(env.req), headers=headers)
    assert saved.status_code == 200 and saved.json()["revision_no"] == 1, saved.text
    after = client.get(url(env.req), headers=headers)
    assert after.status_code == 200 and after.json()["content"] == env.req.content
    assert len(calls) == 2  # v2 must never fall through to the v1 create handler.
    assert observed == ["POST " + path] * 2 + ["GET " + url(env.req), "POST " + url(env.req, True), "GET " + url(env.req)]
    assert [e[0] for e in env.events] == ["document", "canvas", "graph"]
    assert checks and all(group == F and operation == "card_document_edit" for group, operation in checks)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_main_shared_mutation_middleware_still_blocks_writes(env, composition, monkeypatch, version):
    reference, current, _ = composition
    from modules.flow_gate.services import mutation_policy
    from test_card_document_edit_0127 import business_state

    def denied(group, principal, operation):
        raise mutation_policy.MutationPolicyError(423, "GROUP_AI_RUN_LOCKED", "locked", group_id=group)

    monkeypatch.setattr(mutation_policy, "assert_group_mutation_allowed", denied)
    before = business_state(env)
    path = (f"/flowgate/api/v1/flows/{F}/nodes/research/slots/instruction/document"
            if version == "v1" else url(env.req, True))
    responses = []
    for app in ((reference, current) if version == "v1" else (current,)):
        response = TestClient(app).post(path, params={"group_id": F}, json=payload(env.req),
                                        headers={"Authorization": "Bearer user"})
        assert response.status_code == 423, response.text
        assert response.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"
        responses.append(response.json())
    assert all(body == responses[0] for body in responses)
    assert business_state(env) == before


def test_main_v2_service_enforces_policy_without_middleware_group_hint(env, composition, monkeypatch):
    _, current, _ = composition
    from modules.flow_gate.services import mutation_policy
    from test_card_document_edit_0127 import business_state, ensure_guard

    def denied(group, principal, operation):
        assert group == F and operation == "card_document_edit"
        raise mutation_policy.MutationPolicyError(423, "GROUP_AI_RUN_LOCKED", "locked", group_id=group)

    monkeypatch.setattr(mutation_policy, "assert_group_mutation_allowed", denied)
    ensure_guard(env)
    before = business_state(env)
    response = TestClient(current).post(url(env.req, True), json=payload(env.req),
                                        headers={"Authorization": "Bearer user"})
    assert response.status_code == 423, response.text
    assert response.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"
    assert business_state(env) == before
