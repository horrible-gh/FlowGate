"""0127 T#1: real SQLite/file transaction invariants and P#1 transport decisions.

Only auth, display-card definition, and notifications are mocked; the Flow guard, CAS,
revision history, events, file replacement and rollback callbacks execute for real.
"""
import importlib
import itertools
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from modules.flow_gate.db import documents, groups, projects, connection, document_revisions, workflow_events
from modules.flow_gate.graph_workflow.services import flow_guard
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.storage import files, paths
from flowgate.card_documents import repository
from flowgate.card_documents import legacy_adapter as legacy
from flowgate.card_document_edit import contracts, boundary, guards, target, idempotency, contacts, type_rules
from flowgate.card_document_edit import dedicated_bridge, legacy_store, common_checks
from flowgate.card_document_edit.contracts import Actor, Origin, ReviseRequest, Built, DedicatedRequest
from flowgate.card_document_edit.errors import GraphApiError
from flowgate.card_document_edit.routes import router

service = importlib.import_module("flowgate.card_document_edit.revise")
reads = importlib.import_module("flowgate.card_document_edit.read")
store_module = importlib.import_module("flowgate.card_document_edit.store")
F = "flowgate.v02.0127"
D = F + ".0002-N"
HEADER = "---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: N\ndoc_number: 0002-N\n---\n"


@pytest.fixture
def env(monkeypatch, tmp_path, migrated_sqlite_db):
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    db_path = migrated_sqlite_db("card_document_edit.db")
    db = connection.FlowGateStore.__new__(connection.FlowGateStore)
    db._db, db._sq = _SqliteDbAdapter(db_path), None
    monkeypatch.setattr(connection, "STORE", db)
    # T#2 owns startup/legacy-port wiring. Exercise its registered/unregistered contract here.
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "_handlers", {**card_document_ports._handlers, target.EDIT_PORT: service.revise})
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", os.environ.get("FLOWGATE_AGENT_PEPPER_ID", "primary"))
    projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    for user in ("user", "other"):
        db._execute("INSERT INTO users (user_id, username, email, password, created_at, updated_at) VALUES (?, ?, ?, 'x', 't', 't')",
                    [user, user, user + "@test.invalid"])
    groups.create({"group_id": F, "project_id": "flowgate", "module": "v02", "title": F, "status": "OPEN"})
    path = tmp_path / "storage" / "documents" / "original.md"
    path.parent.mkdir(parents=True)
    path.write_bytes((HEADER + "original\r\n").encode())
    doc = documents.create({"doc_id": D, "project_id": "flowgate", "module": "v02", "group_id": F,
                            "type_code": "N", "seq": 2, "title": "Original", "status": "draft", "owner_id": "user",
                            "file_path": paths.to_storage_relative(path, "flowgate")})
    repository.insert(F, "research", "instruction", "N", D, True, 0, "user")
    node = {"node_key": "research", "node_type": "document", "doc_type": "N"}
    monkeypatch.setattr(target.settings, "card_node", lambda f, k: node if f == F and k == "research" else None)
    monkeypatch.setattr(legacy, "load_flow", lambda f: groups.get_by_id(f) or fail(404, "flow_not_found", {"flow_id": f}))
    monkeypatch.setattr(guards, "has_permission", lambda *a: True)
    monkeypatch.setattr(legacy, "require_writable", lambda f: None)
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", lambda *a: None)
    monkeypatch.setattr(guards.workflow_rework_service, "assert_no_revert_pending", lambda f: None)
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: False)
    events = []
    monkeypatch.setattr(contacts, "broadcast_event_threadsafe", lambda ev: events.append(("document", ev)))
    monkeypatch.setattr(contacts.canvas_events, "publish_canvas_changed", lambda **kw: events.append(("canvas", kw)))
    monkeypatch.setattr(contacts.v01_contact, "after_inbox_edited", lambda *a: events.append(("graph", a)))
    def auth(request):
        value = request.headers.get("Authorization")
        if value == "Bearer worker":
            return {"issued_to": "user", "_is_user_jwt": False}
        if value in {"Bearer user", "Bearer other"}:
            return {"issued_to": value.split()[1], "_is_user_jwt": True}
        return JSONResponse(status_code=401, content={})
    monkeypatch.setattr(envelope, "verify_bearer", auth)
    app = FastAPI()
    app.include_router(router, prefix="/flowgate")
    client = TestClient(app)
    req = ReviseRequest(F, "research", "instruction", D, 0, HEADER + "changed\r\n", Actor("user"), idempotency_key="save-1")
    return SimpleNamespace(db=db, path=path, doc=doc, req=req, events=events, client=client, root=tmp_path, node=node)


def fail(status=500, code="internal_error", params=None):
    raise GraphApiError(status, code, params or {})


def payload(req):
    return {k: getattr(req, k) for k in ("expected_doc_id", "expected_revision", "content", "idempotency_key")}


def url(req, save=False):
    return f"/flowgate/api/v2/flows/{req.flow_id}/nodes/{req.node_key}/slots/{req.slot}/document" + ("/revisions" if save else "")


def post(env, req=None):
    req = req or env.req
    return env.client.post(url(req, True), headers={"Authorization": "Bearer user"}, json=payload(req))


def count(env, table):
    return env.db._fetch_one("SELECT COUNT(*) AS n FROM " + table)["n"]


def business_state(env):
    return (documents.get_by_id(D), env.path.read_bytes() if env.path.exists() else None,
            env.db._fetch_all("SELECT * FROM document_revisions"), env.db._fetch_all("SELECT * FROM workflow_events"),
            env.db._fetch_all("SELECT * FROM graph_flow_guards"), list(env.path.parent.glob("revisions/*")))


def ensure_guard(env):
    # ensure_guard_row is deliberately outside the write transaction, as in the established guard.
    flow_guard.layout_store.ensure_guard_row(F)


def test_saved_once_replay_and_no_service_notifications(env):
    original = env.path.read_bytes()
    outcome = service.revise(env.req)
    assert outcome.kind == "SAVED" and outcome.contacts == "NORMAL"
    assert outcome.result["revision_no"] == 1 and outcome.result["replayed"] is False
    assert env.path.read_bytes() == env.req.content.encode()
    assert env.events == []
    revisions = document_revisions.list_by_doc(D)
    assert len(revisions) == 1 and revisions[0]["revision_no"] == 0
    assert paths.resolve_storage_path(revisions[0]["backup_path"], "flowgate").read_bytes() == original
    row = env.db._fetch_one("SELECT * FROM workflow_events WHERE event_type = 'doc_edited'")
    metadata = json.loads(row["metadata"])
    assert row["document_id"] == env.doc["id"] and row["actor_user_id"] == "user"
    assert row["metadata"] == idempotency.dumps(metadata)
    assert metadata["content_sha256"] == idempotency.fingerprint(env.req.content)
    assert metadata["actor_kind"] == "person" and metadata["origin"] == "designer"
    assert documents.get_by_id(D)["status"] == env.doc["status"]
    contacts.run(outcome.plan)
    assert [x[0] for x in env.events] == ["document", "canvas", "graph"]
    assert env.events[-1][1][-1] is None
    assert not contacts.run(outcome.plan)
    state = business_state(env)
    replay = service.revise(env.req)
    assert replay.kind == "REPLAYED" and replay.result["updated_at"] == outcome.result["updated_at"]
    assert business_state(env) == state
    contacts.run_replay(replay.plan)
    assert [x[0] for x in env.events] == ["document", "canvas", "graph"] * 2
    assert not list(env.path.parent.glob("*.keep-*"))


def test_human_routes_read_write_replay_and_pure_get_body(env):
    got = env.client.get(url(env.req), headers={"Authorization": "Bearer user"})
    assert got.status_code == 200
    body = got.json()
    assert body["revision_no"] == 0 and body["content"] == env.path.read_bytes().decode()
    assert "document" not in body["line"] and "revision_no" not in body["line"]
    result = post(env)
    assert result.status_code == 200, result.text
    assert result.json()["revision_no"] == 1
    assert [e[0] for e in env.events] == ["document", "canvas", "graph"]
    assert post(env).json()["replayed"] is True


@pytest.mark.parametrize("changes,code,reason", [
    ({"expected_doc_id": None}, "card_binding_missing", "required"),
    ({"expected_doc_id": ""}, "card_binding_missing", "invalid_format"),
    ({"expected_doc_id": 2}, "card_binding_missing", "invalid_type"),
    ({"expected_revision": None}, "expected_revision_missing", "required"),
    ({"expected_revision": True}, "expected_revision_missing", "invalid_type"),
    ({"expected_revision": "0"}, "expected_revision_missing", "invalid_type"),
    ({"expected_revision": 0.0}, "expected_revision_missing", "invalid_type"),
    ({"expected_revision": -1}, "expected_revision_missing", "invalid_format"),
    ({"content": None}, "invalid_content", "required"),
    ({"content": 7}, "invalid_content", "invalid_type"),
    ({"idempotency_key": None}, "invalid_request", "required"),
    ({"idempotency_key": "a b"}, "invalid_request", "invalid_format"),
    ({"idempotency_key": "a" * 129}, "invalid_request", "invalid_format"),
    ({"idempotency_key": "_start"}, "invalid_request", "invalid_format"),
    ({"idempotency_key": "é"}, "invalid_request", "invalid_format"),
])
def test_input_contract_and_no_effects(env, changes, code, reason):
    state = business_state(env)
    res = post(env, replace(env.req, **changes))
    assert res.status_code == 422 and res.json()["error"]["code"] == code
    assert res.json()["error"]["params"]["errors"][0]["reason"] == reason
    assert business_state(env) == state and env.events == []


@pytest.mark.parametrize("auth,status,code", [(None, 401, "unauthorized"), ("worker", 403, "token_kind_not_allowed"), ("user", 422, "invalid_request")])
def test_auth_before_json_parse(env, auth, status, code):
    res = env.client.post(url(env.req, True), headers={"Authorization": "Bearer " + auth} if auth else {}, content="{")
    assert res.status_code == status and res.json()["error"]["code"] == code


def test_unknown_fields_and_validation_order(env):
    bad = {**payload(env.req), "force": True, "actor": "other", "expected_revision": None}
    res = env.client.post(url(env.req, True), headers={"Authorization": "Bearer user"}, json=bad)
    assert res.json()["error"] == {"code": "invalid_request", "message": res.json()["error"]["message"],
        "params": {"errors": [{"field": "actor", "reason": "unknown_field"}, {"field": "force", "reason": "unknown_field"}]}}
    res = post(env, replace(env.req, expected_doc_id=None, expected_revision=None, content=None, idempotency_key=None))
    assert res.json()["error"]["code"] == "card_binding_missing"


def test_permission_precedes_current_document_and_does_not_disclose_it(env, monkeypatch):
    monkeypatch.setattr(guards, "has_permission", lambda *a: False)
    res = post(env, replace(env.req, expected_doc_id="foreign"))
    assert res.status_code == 403
    assert res.json()["error"]["params"] == {"flow_id": F, "required": "perm_document_update"}


def test_slot_and_node_checks(env, monkeypatch):
    assert post(env, replace(env.req, node_key="absent")).json()["error"]["code"] == "node_not_found"
    assert post(env, replace(env.req, slot="absent")).json()["error"]["params"]["errors"][0]["reason"] == "not_a_slot_of_this_card"
    monkeypatch.setattr(target.settings, "card_node", lambda *a: {**env.node, "doc_type": "TS"})
    res = post(env, replace(env.req, slot="scenario"))
    assert res.json()["error"]["params"]["errors"] == [{"field": "slot", "reason": "slot_shares_a_document", "shares": "instruction"}]


def test_no_document_and_slot_changed_before_editability(env):
    res = post(env, replace(env.req, expected_doc_id="old"))
    assert res.json()["error"]["code"] == "slot_document_changed"
    assert res.json()["error"]["params"]["doc_id"] == D
    repository.delete(F, "research", "instruction", "N")
    res = post(env)
    assert res.json()["error"]["code"] == "no_document"
    assert res.json()["error"]["params"]["doc_id"] is None


@pytest.mark.parametrize("status", ["cancelled", "archived"])
def test_terminal_before_final_type_body_idempotency(env, monkeypatch, status):
    # Terminal rows are normally filtered out by live_end. Isolate the editable decision itself.
    current = {**env.doc, "status": status}
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: True)
    with pytest.raises(GraphApiError) as e:
        guards.check_editable(env.req, current)
    assert e.value.params["reason"] == "terminal_status" and e.value.params["status"] == status


def test_lock_and_revert_mapping_and_order(env, monkeypatch):
    def locked(*a):
        raise guards.mutation_policy.MutationPolicyError(423, "GROUP_AI_RUN_LOCKED", "locked", group_id=F, run_id="run", run_live=True)
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", locked)
    monkeypatch.setattr(guards.workflow_rework_service, "assert_no_revert_pending", lambda f: pytest.fail("revert after lock"))
    res = post(env, replace(env.req, expected_doc_id="foreign"))
    assert res.status_code == 423 and res.json()["error"]["params"] == {"group_id": F, "run_id": "run", "run_live": True}
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", lambda *a: None)
    class Pending(Exception):
        code = "workflow_revert_pending"
        details = {"doc_ids": ["secret"]}
    def pending(f):
        raise Pending()
    monkeypatch.setattr(guards.workflow_rework_service, "assert_no_revert_pending", pending)
    res = post(env)
    assert res.status_code == 409 and res.json()["error"]["params"] == {"group_id": F}


@pytest.mark.parametrize("change,code", [("body", "idempotency_key_reused"), ("base", "idempotency_key_reused"),
                                           ("actor", "idempotency_key_reused"), ("newer", "revision_conflict")])
def test_idempotency_conflicts(env, change, code):
    service.revise(env.req)
    req = env.req
    if change == "body": req = replace(req, content=req.content + "different")
    if change == "base": req = replace(req, expected_revision=1)
    if change == "actor": req = replace(req, actor=Actor("other"))
    if change == "newer": service.revise(replace(req, expected_revision=1, idempotency_key="save-2"))
    state = business_state(env)
    with pytest.raises(GraphApiError) as e: service.revise(req)
    assert e.value.code == code
    assert "actor_user_id" not in e.value.params
    assert business_state(env) == state


def agent_request(env, **kw):
    token = {"token_id": "token_1", "issued_to": "user", "group_id": F, "doc_ref": D, "action_scope": "edit"}
    return replace(env.req, actor=Actor("user", "worker"), origin=Origin("agent", token_id="token_1", token=token),
                   idempotency_key="token_1", **kw)


@pytest.mark.parametrize("already_saved", [False, True])
def test_dry_run_rolls_back_guard_and_replay_never_produces_contacts(env, already_saved):
    req = agent_request(env)
    if already_saved: service.revise(req)
    ensure_guard(env)
    state = business_state(env)
    outcome = service.revise(replace(req, validate_only=True))
    assert outcome.kind == "VALIDATED" and outcome.plan is None and outcome.contacts == "NONE"
    assert outcome.verdict["expected_revision"] == 0
    assert "revision_no" not in outcome.verdict and "replayed" not in outcome.verdict
    assert business_state(env) == state and env.events == []


def test_size_guard_runs_after_body_and_before_idempotency_and_revision(env, monkeypatch):
    ensure_guard(env)
    state = business_state(env)
    seen = []
    def size(doc, body, line):
        seen.append(reads.compose_get_body(doc, body, line))
        fail(413, "result_too_large")
    monkeypatch.setattr(idempotency, "lookup", lambda *a: pytest.fail("size guard precedes idempotency"))
    with pytest.raises(GraphApiError) as e: service.revise(replace(env.req, expected_revision=9, size_guard=size))
    assert e.value.code == "result_too_large" and seen[0]["content"] == env.path.read_bytes().decode()
    assert business_state(env) == state
    env.path.unlink()
    with pytest.raises(GraphApiError) as e: service.revise(replace(env.req, size_guard=size))
    assert e.value.params["reason"] == "body_unreadable" and len(seen) == 1


@pytest.mark.parametrize("failure", ["history", "event", "replace", "commit", "restore"])
def test_failure_atomicity_and_restore_incomplete(env, monkeypatch, failure):
    ensure_guard(env)
    state = business_state(env)
    def boom(*a, **kw): raise RuntimeError("injected")
    if failure == "history": monkeypatch.setattr(document_revisions, "create_once", boom)
    if failure == "event": monkeypatch.setattr(workflow_events, "create", boom)
    if failure == "replace": monkeypatch.setattr(files, "replace_restorably", boom)
    if failure in {"commit", "restore"}:
        original = env.db._transaction_frame
        failed = False
        @contextmanager
        def commit_failure():
            nonlocal failed
            with original():
                yield
                if not failed:
                    failed = True
                    raise RuntimeError("commit injected")
        monkeypatch.setattr(env.db, "_transaction_frame", commit_failure)
    if failure == "restore": monkeypatch.setattr(files.ReplacedFile, "restore", lambda self: False)
    res = post(env)
    assert res.status_code == 500, res.text
    now = business_state(env)
    assert now[0] == state[0] and now[2:] == state[2:]
    assert env.events == []
    if failure == "restore":
        assert list(env.path.parent.glob("*.keep-*"))
        assert post(env).status_code == 500
        assert env.client.get(url(env.req), headers={"Authorization": "Bearer user"}).status_code == 500
    else:
        assert now == state and not list(env.path.parent.glob("*.keep-*"))


@pytest.mark.parametrize("affected", [0, 2, -1, None, True])
def test_cas_result_is_authority_before_any_files(env, monkeypatch, affected):
    ensure_guard(env)
    state = business_state(env)
    original = env.db._execute_affected
    def execute(sql, params=None):
        if "revision_no = revision_no + 1" in sql:
            return affected
        return original(sql, params)
    monkeypatch.setattr(env.db, "_execute_affected", execute)
    res = post(env)
    assert res.status_code == (409 if affected == 0 else 500)
    assert business_state(env) == state


def test_concurrent_writers_one_revision_one_backup_and_loser_preserves_winner(env):
    gate = threading.Barrier(2)
    def save(n):
        gate.wait(timeout=5)
        try:
            out = service.revise(replace(env.req, content=HEADER + str(n), idempotency_key="writer-" + str(n)))
            return "saved", n, out
        except GraphApiError as exc:
            return exc.code, n, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sorted(r[0] for r in results) == ["revision_conflict", "saved"]
    winner = next(n for kind, n, out in results if kind == "saved")
    assert env.path.read_bytes() == (HEADER + str(winner)).encode()
    assert count(env, "document_revisions") == 1 and count(env, "workflow_events") == 1


def test_notification_failures_are_isolated_and_projection_failure_still_notifies(env, monkeypatch):
    calls = []
    def broken(ev): calls.append("document"); raise RuntimeError("publish")
    monkeypatch.setattr(contacts, "broadcast_event_threadsafe", broken)
    res = post(env)
    assert res.status_code == 200 and calls == ["document"]
    assert [e[0] for e in env.events] == ["canvas", "graph"]
    monkeypatch.setattr(target, "line_projection", lambda *a: fail())
    res = post(env, replace(env.req, expected_revision=1, idempotency_key="next"))
    assert res.status_code == 500 and documents.get_by_id(D)["revision_no"] == 2
    assert calls == ["document"] * 2
    assert [e[0] for e in env.events] == ["canvas", "graph"] * 2


@pytest.mark.parametrize("data,state,content", [(b"", "readable", ""), (HEADER.encode(), "readable", HEADER),
    (b"\xff", "read_error", None), (None, "file_not_found", None)])
def test_snapshot_empty_frontmatter_unreadable(env, data, state, content):
    if data is None: env.path.unlink()
    else: env.path.write_bytes(data)
    got = reads.snapshot(D)
    assert got["body_state"] == state and got["content"] == content
    assert got["revision_no"] == 0


def test_protected_current_body_is_read_once(env, monkeypatch):
    calls = []
    original = service.read_body_bytes
    def read(doc): calls.append(doc["doc_id"]); return original(doc)
    monkeypatch.setattr(service, "read_body_bytes", read)
    service.revise(env.req)
    assert calls == [D]


def test_idempotency_scans_past_120_case_insensitive_candidates_and_stops_at_two(env):
    original = service.revise(env.req)
    template = env.db._fetch_one("SELECT * FROM workflow_events")
    env.db._execute("DELETE FROM workflow_events")
    key = "ABCDEFGH"
    variants = ["".join(parts) for parts in itertools.product(*[(c.lower(), c) for c in key]) if "".join(parts) != key][:120]
    for k in [*variants, key]:
        meta = json.loads(template["metadata"]); meta["idem_key"] = k
        workflow_events.create({**template, "metadata": idempotency.dumps(meta)})
    rows = idempotency.lookup("flowgate", key)
    assert len(rows) == 1 and rows[0]["parsed"]["idem_key"] == key
    workflow_events.create({**template, "metadata": '{"idem_key":"ABCDEFGH", broken'})
    rows = idempotency.lookup("flowgate", key)
    assert len(rows) == 2 and rows[-1]["corrupt"]
    assert idempotency.judge(rows, {}, {}).kind == "REUSED"


def test_expired_record_and_literal_underscore(env):
    req = replace(env.req, idempotency_key="literal_key")
    service.revise(req)
    cutoff = (datetime.fromisoformat(connection.now_iso()) - timedelta(seconds=86401)).isoformat(timespec="seconds")
    assert len(idempotency.lookup("flowgate", "literal_key")) == 1
    assert idempotency.lookup("flowgate", "literalXkey") == []
    env.db._execute("UPDATE workflow_events SET created_at = ?", [cutoff])
    assert idempotency.lookup("flowgate", "literal_key") == []
    with pytest.raises(GraphApiError) as e: service.revise(req)
    assert e.value.code == "revision_conflict"


@pytest.mark.parametrize("fault", ["before", "after", "rows", "docs", "port"])
def test_classification_unavailable_never_means_non_card(env, monkeypatch, fault):
    assert target.classify_card_slot(env.doc) == target.CARD
    if fault == "before": monkeypatch.setattr(repository, "available", lambda: False)
    if fault == "after":
        original = repository.rows
        def rows(*a):
            result = original(*a)
            monkeypatch.setattr(repository, "available", lambda: False)
            return result
        monkeypatch.setattr(repository, "rows", rows)
    if fault == "rows": monkeypatch.setattr(repository, "rows", lambda *a: fail())
    if fault == "docs": monkeypatch.setattr(legacy, "flow_documents", lambda *a: fail())
    if fault == "port": monkeypatch.setattr(target, "card_edit_port_registered", lambda: False)
    assert target.classify_card_slot(env.doc) == target.UNAVAILABLE
    assert target.notify_scope(target.UNAVAILABLE) is True
    with pytest.raises(GraphApiError) as e: target.require_non_card(env.doc)
    assert e.value.params["errors"][0]["validation"]["reason"] == "card_edit_unavailable"
    assert target.classify_card_slot({"group_id": None}) == target.NON_CARD


def test_own_join_create_modes_and_wrong_guard(env):
    with env.db.transaction():
        with pytest.raises(GraphApiError): service.revise(env.req)
        with pytest.raises(flow_guard.GuardNotHeld): boundary.enter(env.doc, boundary.JOIN, lambda ctx: None)
        with pytest.raises(flow_guard.GuardNotHeld): boundary.enter(env.doc, boundary.CREATE, lambda ctx: None)
    result = boundary.enter(env.doc, boundary.OWN, lambda ctx: boundary.enter(env.doc, boundary.JOIN, lambda inner: inner.guard_key))
    assert result == ("flow", F)


def test_type_lock_precedes_guard_and_row_is_second_statement(env, monkeypatch):
    trace = []
    original = env.db._execute_affected
    def execute(sql, params=None):
        trace.append(sql)
        return original(sql, params)
    monkeypatch.setattr(env.db, "_execute_affected", execute)
    boundary.enter(env.doc, boundary.OWN, lambda ctx: ctx.assert_held())
    assert "guard_seq" in trace[0] and "updated_at = updated_at" in trace[1]
    class Lock:
        def acquire(self, **kw): trace.append(("type", kw)); return False
        def release(self): pytest.fail("not acquired")
    monkeypatch.setattr(boundary, "_lock", lambda doc: Lock())
    with pytest.raises(flow_guard.FlowLockTimeout):
        boundary.enter({**env.doc, "type_code": "WP"}, boundary.OWN, lambda ctx: pytest.fail())
    assert trace[-1] == ("type", {"timeout": 30})
    def joined(ctx):
        with pytest.raises(flow_guard.FlowLockTimeout):
            boundary.enter({**env.doc, "type_code": "WP"}, boundary.JOIN, lambda c: pytest.fail())
    boundary.enter(env.doc, boundary.OWN, joined)
    assert trace[-1] == ("type", {"blocking": False})


class RuleCall:
    needs_current = False
    def __init__(self, path, policy="CURRENT"):
        self.path, self.policy = path, policy
    def precheck(self, doc):
        if doc is None: fail(404, "missing")
    def conflict(self, doc): return GraphApiError(409, "tr2_spec_changed", {})
    def build(self, fresh, body): return Built(b"new bytes", target_path=self.path, snapshot_policy=self.policy)


@pytest.mark.parametrize("rollback", [False, True])
def test_create_pending_contacts_wait_for_outer_commit_or_discard(env, rollback):
    new_id = F + ".0003-TR2"
    path = env.path.parent / "created.json"
    request = DedicatedRequest(new_id, 0, Actor("user"), RuleCall(path), Origin("tr2_window"), new_document=True)
    pending = None
    try:
        with env.db.transaction():
            documents.create({"doc_id": new_id, "project_id": "flowgate", "module": "v02", "group_id": F,
                              "type_code": "TR2", "seq": 3, "title": "Created", "status": "draft", "owner_id": "user"})
            result, pending = dedicated_bridge.dedicated_create(request)
            assert pending.state == "STAGED" and env.events == []
            with pytest.raises(AssertionError): pending.run_if_committed()
            if rollback: raise RuntimeError("outer transaction failed")
    except RuntimeError:
        assert rollback
    if rollback:
        assert pending.state == "DISCARDED" and not path.exists()
        assert pending.run_if_committed() == "NOT_RUN" and env.events == []
        assert document_revisions.list_by_doc(new_id) == []
    else:
        assert pending.state == "COMMITTED" and env.events == []
        assert pending.run_if_committed() == "RUN"
        assert [e[0] for e in env.events] == ["document"]
        assert pending.run_if_committed() == "NOT_RUN"
        assert document_revisions.list_by_doc(new_id)[0]["revision_no"] == 1


def test_legacy_rechecks_classification_and_missing_body_has_no_backup(env, monkeypatch):
    repository.delete(F, "research", "instruction", "N")
    req = DedicatedRequest(D, 999, Actor("user"), RuleCall(env.path, "PREVIOUS"), Origin("agent"))
    original = boundary.enter
    def connect_then_enter(*args, **kwargs):
        repository.insert(F, "research", "instruction", "N", D, True, 0, "user")
        return original(*args, **kwargs)
    monkeypatch.setattr(boundary, "enter", connect_then_enter)
    with pytest.raises(GraphApiError) as e: legacy_store.legacy_store(req)
    assert e.value.params["errors"][0]["validation"]["reason"] == "ambiguous_or_inconsistent_binding"
    monkeypatch.setattr(boundary, "enter", original)
    repository.delete(F, "research", "instruction", "N")
    env.path.unlink()
    result, plan = legacy_store.legacy_store(req)
    assert result["revision_no"] == 1 and document_revisions.list_by_doc(D) == []
    contacts.run(plan)
    assert [e[0] for e in env.events] == ["document", "graph"]


def test_identity_and_type_validation_follow_revision(env):
    req = replace(env.req, content=env.req.content.replace("type: N", "type: T"))
    assert post(env, replace(req, expected_revision=99)).json()["error"]["code"] == "revision_conflict"
    res = post(env, req)
    assert res.status_code == 422 and res.json()["error"]["params"]["errors"][0]["reason"] == "document_identity_changed"
    assert post(env, replace(env.req, content="")).status_code == 422


def test_body_unreadable_blocks_successful_replay(env):
    service.revise(env.req)
    env.path.unlink()
    res = post(env)
    assert res.status_code == 422 and res.json()["error"]["params"]["reason"] == "body_unreadable"
    assert count(env, "workflow_events") == 1 and env.events == []


@pytest.mark.parametrize("existing", ["valid", "missing", "ambiguous", "orphan"])
def test_previous_backup_single_source_and_orphan_preservation(env, existing):
    backup = env.path.parent / "revisions" / f"{D}.r0.md"
    backup.parent.mkdir()
    backup.write_bytes(b"old recovery source")
    if existing != "orphan":
        document_revisions.create_once({"doc_id": D, "revision_no": 0,
            "backup_path": paths.to_storage_relative(backup, "flowgate"), "edit_reason": "user_comment", "created_by": "user"})
    if existing == "missing": backup.unlink()
    if existing == "ambiguous":
        env.db._execute("INSERT INTO document_revisions (doc_id, revision_no, backup_path, edit_reason, created_by, created_at) "
                        "SELECT doc_id, revision_no, backup_path, edit_reason, created_by, created_at FROM document_revisions")
    old = env.path.read_bytes()
    res = post(env)
    if existing in {"missing", "ambiguous"}:
        assert res.status_code == 500 and env.path.read_bytes() == old
        assert documents.get_by_id(D)["revision_no"] == 0
    else:
        assert res.status_code == 200, res.text
        assert count(env, "document_revisions") == 1
        if existing == "valid": assert backup.read_bytes() == b"old recovery source"
        else:
            assert backup.read_bytes() == old
            assert next(backup.parent.glob("*.orphan-*")).read_bytes() == b"old recovery source"


def test_keep_sibling_rejects_before_idempotency(env, monkeypatch):
    keep = env.path.with_name(env.path.name + ".keep-123-456")
    keep.write_bytes(b"previous")
    monkeypatch.setattr(idempotency, "lookup", lambda *a: pytest.fail("must not lookup unstable body"))
    assert post(env).status_code == 500
    assert keep.read_bytes() == b"previous"


def test_lookup_database_failure_cannot_become_new(env, monkeypatch):
    ensure_guard(env)
    before = business_state(env)
    monkeypatch.setattr(idempotency, "lookup", lambda *a: fail())
    assert post(env).status_code == 500
    assert business_state(env) == before


def test_snapshot_waits_for_writer_and_never_mixes_revision_and_body(env, monkeypatch):
    replaced, release = threading.Event(), threading.Event()
    original = store_module.body_replace
    def paused(inp):
        original(inp)
        replaced.set()
        assert release.wait(5)
    monkeypatch.setattr(store_module, "body_replace", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(service.revise, env.req)
        assert replaced.wait(5)
        reading = pool.submit(reads.snapshot, D)
        release.set()
        assert saving.result(timeout=10).kind == "SAVED"
        snapshot = reading.result(timeout=10)
    assert snapshot["revision_no"] == 1 and snapshot["content"] == env.req.content


def test_pure_composer_does_not_read_again(env, monkeypatch):
    from flowgate.card_document_edit.contracts import Body
    line = target.resolve_current(F, env.node, "instruction")
    body = Body("readable", b"", "")
    line = target.with_body(line, env.doc, body)
    monkeypatch.setattr(documents, "get_by_id", lambda *a: pytest.fail("pure composer DB read"))
    monkeypatch.setattr(reads, "read_body_bytes", lambda *a: pytest.fail("pure composer body read"))
    result = reads.compose_get_body(env.doc, body, line)
    assert result["content"] == "" and result["body_state"] == "readable"


def test_work_plan_rule_uses_protected_current_bytes_and_shared_validators(env, monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp, work_plan_apply_service as apply
    doc = {**env.doc, "type_code": "WP"}
    old = wp.empty_recovery_body("flowgate")
    current = contracts.Body("readable", wp.dumps(old).encode(), wp.dumps(old), env.path)
    seen = []
    monkeypatch.setattr(wp, "load_previous_body", lambda *a: pytest.fail("must not reread current WP"))
    monkeypatch.setattr(apply, "instruction_mode_lock_errors", lambda d, before, after: seen.append(before) or [])
    built = type_rules.WorkPlanRule(env.req).validate_and_build(wp.dumps(old), doc, current)
    assert seen == [old] and built.snapshot_policy == "PREVIOUS"
    assert built.body_saved_source == "work_plan_save" and len(built.type_tasks) == 2
    assert set(built.row_updates) == {"title", "meta"}
    assert json.loads(built.stored_bytes)["steps"] == old["steps"]
    monkeypatch.setattr(apply, "instruction_mode_lock_errors", lambda *a: [{"reason": "locked"}])
    with pytest.raises(GraphApiError) as exc:
        type_rules.WorkPlanRule(env.req).validate_and_build(wp.dumps(old), doc, current)
    assert exc.value.params["errors"][0]["validation"]["errors"] == [{"reason": "locked"}]


def test_tr2_rule_builds_only_and_snapshot_policy_uses_new_revision(env, monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    current = contracts.Body("readable", b"{}", "{}", env.path)
    doc = {**env.doc, "type_code": "TR2"}
    proposal = {"edit_spec": {"edits": [], "deferred": []}, "codebase_root": "dropped"}
    calls = []
    monkeypatch.setattr(tr2, "_assert_mutable", lambda d: calls.append("mutable"))
    monkeypatch.setattr(tr2, "validate", lambda data, **kw: calls.append("validate") or data)
    monkeypatch.setattr(tr2, "verify_pair", lambda *a: calls.append("pair"))
    monkeypatch.setattr(tr2, "canonicalize", lambda d: {"edit_spec": d["edit_spec"]})
    monkeypatch.setattr(tr2, "target_set", lambda *a: [])
    monkeypatch.setattr(tr2, "resolve_source_root", lambda *a: env.root)
    monkeypatch.setattr(tr2, "target_fingerprint", lambda *a: "baseline")
    monkeypatch.setattr(tr2, "derived_files", lambda *a: [])
    monkeypatch.setattr(tr2, "spec_fingerprint", lambda *a: "spec")
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda *a: env.path.with_suffix(".json"))
    built = type_rules.Tr2Rule().validate_and_build(json.dumps(proposal), doc, current)
    assert calls == ["mutable", "validate", "pair"]
    assert built.snapshot_policy == "CURRENT" and built.body_saved_source == "tr2_save"
    assert built.result["body"]["baseline_fingerprint"] == "baseline"
    assert built.result["dropped_keys"] == ["codebase_root"]
    assert not built.target_path.exists() and count(env, "document_revisions") == 0


def test_type_rule_selection_and_unsupported_non_markdown(env, monkeypatch):
    from modules.flow_gate import template_provision
    monkeypatch.setattr(template_provision, "is_design_type", lambda t: t in {"D", "P", "L", "DB"})
    for code, cls in [("WP", type_rules.WorkPlanRule), ("TR2", type_rules.Tr2Rule), ("TS", type_rules.MarkdownRule),
                      ("N", type_rules.MarkdownRule), ("D", type_rules.MarkdownRule)]:
        assert isinstance(type_rules.rule_for({**env.doc, "type_code": code}), cls)
    assert type_rules.rule_for({**env.doc, "type_code": "CH"}) is None
    assert type_rules.rule_for({**env.doc, "type_code": "M", "file_path": "document.bin"}) is None


def test_dedicated_save_preserves_conflict_and_unavailable_notification_scope(env, monkeypatch):
    req = DedicatedRequest(D, 0, Actor("user"), RuleCall(env.path, "PREVIOUS"))
    monkeypatch.setattr(target, "classify_card_slot", lambda *a: target.UNAVAILABLE)
    result = dedicated_bridge.dedicated_save(req)
    assert result["revision_no"] == 1 and [e[0] for e in env.events] == ["document", "canvas", "graph"]
    with pytest.raises(GraphApiError) as e: dedicated_bridge.dedicated_save(req)
    assert e.value.code == "tr2_spec_changed"



def test_actual_port_unregistration_fails_closed(env):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    assert target.classify_card_slot(env.doc) == target.CARD
    card_document_ports.unregister(target.EDIT_PORT)
    assert target.classify_card_slot(env.doc) == target.UNAVAILABLE


def test_restore_never_overwrites_file_replaced_by_someone_else(env, monkeypatch):
    import os
    original_restore = files.ReplacedFile.restore
    original_frame = env.db._transaction_frame
    failed = False
    @contextmanager
    def fail_commit():
        nonlocal failed
        with original_frame():
            yield
            if not failed:
                failed = True
                raise RuntimeError("commit")
    def replace_before_restore(replaced):
        other = replaced.path.with_name("foreign-writer.md")
        other.write_bytes(b"foreign winner")
        os.replace(other, replaced.path)
        return original_restore(replaced)
    monkeypatch.setattr(env.db, "_transaction_frame", fail_commit)
    monkeypatch.setattr(files.ReplacedFile, "restore", replace_before_restore)
    res = post(env)
    assert res.status_code == 500
    assert env.path.read_bytes() == b"foreign winner"
    assert next(env.path.parent.glob("*.keep-*")).read_bytes().endswith(b"original\r\n")
    assert documents.get_by_id(D)["revision_no"] == 0


def test_wrong_type_lock_current_group_and_recheck_rejection(env, monkeypatch):
    old = target.resolve_current
    def wrong_group(*a):
        line = old(*a)
        return {**line, "document": {**line["document"], "group_id": "other"}}
    monkeypatch.setattr(target, "resolve_current", wrong_group)
    assert post(env).status_code == 500
    monkeypatch.setattr(target, "resolve_current", old)
    ensure_guard(env)
    before = business_state(env)
    def recheck(): fail(403, "chat_document_read_only")
    with pytest.raises(GraphApiError) as e:
        service.revise(replace(env.req, recheck=recheck))
    assert e.value.code == "chat_document_read_only" and business_state(env) == before


def test_stale_keys_reused_target_and_both_actor_kinds_are_distinct(env):
    service.revise(env.req)
    req = replace(env.req, actor=Actor("user", "person_delegated"), origin=Origin("chat", run_id="chat-run"))
    with pytest.raises(GraphApiError) as e: service.revise(req)
    assert e.value.code == "idempotency_key_reused"
    new = service.revise(replace(req, idempotency_key="chat-new", expected_revision=1))
    meta = json.loads(env.db._fetch_one("SELECT metadata FROM workflow_events ORDER BY id DESC LIMIT 1")["metadata"])
    assert meta["actor_kind"] == "person_delegated" and meta["origin"] == "chat" and meta["run_id"] == "chat-run"
    assert new.plan.token_id is None
    assert guards.principal_of(req).kind == "human"


def test_read_re_resolves_slot_after_snapshot(env, monkeypatch):
    original = target.resolve_current
    calls = 0
    def unstable(*args):
        nonlocal calls
        calls += 1
        line = original(*args)
        if calls % 2 == 0: return {**line, "doc_id": "replaced"}
        return line
    monkeypatch.setattr(target, "resolve_current", unstable)
    with pytest.raises(GraphApiError) as e:
        reads.read_card(F, "research", "instruction", Actor("user"))
    assert e.value.code == "internal_error" and calls == 4


def test_tr2_current_snapshot_uses_worker_self_for_rejected_agent(env):
    documents.update(D, {"type_code": "TR2"})
    fresh = documents.get_by_id(D)
    req = DedicatedRequest(D, 0, Actor("user", "worker"), RuleCall(env.path), Origin("agent"), edit_reason="rejected")
    result, plan = dedicated_bridge.dedicated_store(req)
    revisions = document_revisions.list_by_doc(D)
    assert result["revision_no"] == 1 and len(revisions) == 1
    assert revisions[0]["revision_no"] == 1 and revisions[0]["edit_reason"] == "worker_self"
    assert paths.resolve_storage_path(revisions[0]["backup_path"], "flowgate").read_bytes() == b"new bytes"



def test_real_work_plan_v2_saves_and_backs_up_once(env, monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp, work_plan_apply_service as apply
    plan = wp.empty_recovery_body("flowgate")
    old = wp.dumps(plan).encode()
    env.path.write_bytes(old)
    documents.update(D, {"type_code": "WP"})
    repository.delete(F, "research", "instruction", "N")
    repository.insert(F, "research", "plan", "WP", D, True, 0, "user")
    monkeypatch.setattr(target.settings, "card_node", lambda *a: {**env.node, "doc_type": "WP"})
    monkeypatch.setattr(apply, "instruction_mode_lock_errors", lambda *a: [])
    req = replace(env.req, slot="plan", content=wp.dumps(plan))
    result = service.revise(req)
    assert result.kind == "SAVED" and result.result["revision_no"] == 1
    assert env.path.read_bytes() == wp.dumps(plan).encode()
    revision = document_revisions.list_by_doc(D)[0]
    assert paths.resolve_storage_path(revision["backup_path"], "flowgate").read_bytes() == old
    assert len(result.plan.type_tasks) == 2
    assert count(env, "workflow_events") == 1
    assert result.plan.doc["title"] == wp.derived_title(plan, wp.resolve_title_locale(None, current_title="Original"))
    replay = service.revise(req)
    assert replay.kind == "REPLAYED" and replay.plan.type_tasks == []


def test_current_body_readability_and_final_status_precede_key_and_revision(env, monkeypatch):
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: True)
    res = post(env, replace(env.req, expected_revision=999))
    assert res.json()["error"]["params"]["reason"] == "final_approved"
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: False)
    monkeypatch.setattr(type_rules, "rule_for", lambda *a: None)
    res = post(env)
    assert res.json()["error"]["params"]["reason"] == "unsupported_type"


def test_max_content_length_and_unencodable_content_fail_without_mutation(env):
    ensure_guard(env)
    before = business_state(env)
    res = post(env, replace(env.req, content="a" * 500001))
    assert res.status_code == 422
    assert res.json()["error"]["params"]["errors"][0]["validation"] == {"reason": "too_long", "max": 500000}
    with pytest.raises(GraphApiError) as e:
        service.revise(replace(env.req, content="\ud800"))
    assert e.value.code == "invalid_content"
    assert business_state(env) == before


def test_rule_row_updates_cannot_mutate_document_or_graph_state(env, monkeypatch):
    class InvalidRule:
        def validate_and_build(self, text, doc, body):
            return Built(text.encode(), row_updates={"status": "closed"})
    monkeypatch.setattr(type_rules, "rule_for", lambda *a: InvalidRule())
    res = post(env)
    assert res.status_code == 500
    assert documents.get_by_id(D)["status"] == "draft" and documents.get_by_id(D)["revision_no"] == 0


def test_empty_readable_body_can_be_explicitly_replaced(env):
    env.path.write_bytes(b"")
    res = post(env)
    assert res.status_code == 200, res.text
    rev = document_revisions.list_by_doc(D)[0]
    assert paths.resolve_storage_path(rev["backup_path"], "flowgate").read_bytes() == b""
