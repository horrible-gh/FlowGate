"""card (Node) run settings: stored on the card, read and changed one value at a time.

The feature lives in the v0.2 package ``flowgate.card_settings``; the Graph workflow reaches it
only through ``card_settings_ports`` (node dispatch), registered here as ``startup`` does. ``tests/flowgate/``
placement.

On a real SQLite DB (all migrations, TestClient over the Graph routes and the card settings routes): a card
placed in a draft and a card whose run is done both read and write their settings; every value is saved by
its own PATCH; the items the v0.1 sequence row never stored (instruction use / omit, restart, time limit,
review basis) are stored (after review is gone, the earlier change the earlier implementation: no setting, never read, migration 147); values outside a setting are refused; a running card is locked; and an
AI node starts with its card's run AI, time limit and restarts (the AI invoke start receives them), or with the shared execution
defaults when the card stores nothing. Auth, RBAC and SSE delivery are stubbed.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
# (T0 baseline): the app settings these suites reach (the card approval's v0.1 approval)
# need these when the module runs on its own, as the other suites set them (a Self-check passes no env).
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# (T): this suite's own cards are cards made before the AI role feature (legacy fields,
# L-1 §2.4.5); the stage assignment feature stays on (``legacy_cards`` below, defined in this module).
pytestmark = pytest.mark.usefixtures("legacy_cards")

FA = "flowgate.v02.0091"      # unassigned, live root R (approved)
FB = "flowgate.v02.0092"      # unassigned, no documents
TEST_VALUE, TEST_VERSION = "card_settings_test", "1"
# the run cards' instruction AI comes right after the instruction it writes.
# flowgate.v02test.0053 0003-T: the two 검증 options follow the card's review settings (the list also names
# ``after_review_exhausted``, which the field table already had).
RUN_FIELDS = ["instruction", "instruction_ai", "run_ai", "restart", "review_ai", "review_count", "time_limit",
              "review_basis", "after_review_exhausted", "verify_max_runs", "verify_run_minutes",
              "instruction_review_ai", "instruction_review_count",
              "instruction_note", "run_note", "run_pre_instruction"]


@pytest.fixture()
def db_path(tmp_path, migrated_sqlite_db):
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    path = migrated_sqlite_db("cards.db")
    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    conn_mod.STORE = store
    try:
        yield path
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture()
def legacy_cards(monkeypatch):
    """(L-1, L-2): the cards drawn while it is used are cards made before the AI role
    feature — no role slots, no init row: their stored legacy fields (assignee, ``instruction_ai``, ``run_ai``,
    ``review_count`` / ``review_ai``) are what L-1 resolves each new attempt's stage assignments from. The suites written
    before the earlier implementation/the earlier implementation describe such cards; their attempts run the assigned path (the feature on) all the same."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    monkeypatch.setattr(card_settings_ports, "cards_created", lambda group, user_id, nodes: None)


@pytest.fixture()
def pre_feature_attempts(monkeypatch):
    """(L-2, T): the attempts opened while it is used are pre-feature attempts (opened
    as before the stage assignments: no assignment rows). Call the returned function to switch the feature back on —
    the attempts opened so far stay pre-feature to their end (K-12, G-14)."""
    from flowgate.card_settings import stage_assignment

    real = stage_assignment.enabled
    monkeypatch.setattr(stage_assignment, "enabled", lambda: False)
    return lambda: monkeypatch.setattr(stage_assignment, "enabled", real)


@pytest.fixture()
def passive_person_results(env):
    """(rej_01M6DQW6YF0SCQ5D P1): a pre-feature attempt's person result of a run / test card
    now takes its card's review setting at once (``review_stage.person_result``: review 'none' completes it as
    ``human_submitted``, a review set starts its one review) and a lost step is taken by the run read
    (``review_stage._reconcile_person_result``). The suites that use such a person card as a passive vehicle — a
    result bound with the node's token, its completion / approval / intervention then driven by the test itself — keep
    it passive with this fixture: the card's step after the bind and the run read's reconciliation take nothing for a
    pre-feature run / test person card. An assigned attempt and a work plan (0083 WP-4) are followed as ever. The
    contract itself is tested without it (set up after env, which registers the real handler; ``test_fg_person_wp_review_0083`` test_6_a_person_s_run_card_*,
    ``test_fg_card_approval_0056``)."""
    from flowgate.card_settings import fields, manual_stage, review_stage
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    real_result, real_reconcile = review_stage.person_result, review_stage._reconcile_person_result

    def followed(node) -> bool:
        return review_stage._kind(node) == fields.PLAN

    def person_result(flow_id, node, doc_id):
        if sa.enabled() and sa.role_card(node):
            job = manual_stage.person_result(flow_id, node, doc_id)
            if job != sa.LEGACY:
                return job
        return real_result(flow_id, node, doc_id) if followed(node) else None

    def reconcile(flow_id, run_row, row):
        node = review_stage.legacy.node_of_key(flow_id, row["node_key"])
        if node is not None and followed(node):
            real_reconcile(flow_id, run_row, row)

    had = ports.registered(ports.PERSON_RESULT)
    ports.register(ports.PERSON_RESULT, person_result)
    review_stage._reconcile_person_result = reconcile
    try:
        yield
    finally:
        review_stage._reconcile_person_result = real_reconcile
        if had:
            ports.register(ports.PERSON_RESULT, real_result)
        else:
            ports.unregister(ports.PERSON_RESULT)


@pytest.fixture()
def env(db_path, monkeypatch, tmp_path, request):
    from modules.flow_gate.db import _SqliteTxnAdapter
    from modules.flow_gate.graph_workflow.api import canvas_routes, envelope, graph_routes, run_routes
    from modules.flow_gate.graph_workflow.services import canvas_events, node_dispatch, run_events

    from flowgate.card_documents import integration as card_documents
    from flowgate.card_settings import integration, routes
    from flowgate.card_document_edit import write_gate
    from modules.flow_gate.graph_workflow.services import card_document_ports

    # this isolated app has no startup lifespan. Register the current real document write gate
    # before seeding, as the 0121/0143 harnesses do; keep production write protection enabled.
    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, write_gate.dispatch)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    # this shared harness seeds documents before any app lifespan registers the document write
    # gate. Register the real gate (``fg_harness_write_gate``: only the harness's own direct row writes bypass it)
    # instead of inheriting whatever an earlier test module leaked into the process-wide port table; run on its own
    # (or in another order, as ``--dist loadfile`` does), the suites that use this harness otherwise error on setup
    # with 409 card_document_edit_required.
    import fg_harness_write_gate

    fg_harness_write_gate.install(monkeypatch)
    state = {"events": [], "db": db_path, "jobs": []}

    def fake_verify(request):
        raw = request.headers.get("Authorization", "")
        if raw.startswith("Bearer user:"):
            return {"_is_user_jwt": True, "issued_to": raw[len("Bearer user:"):]}
        return JSONResponse(status_code=401, content={"ok": False})

    monkeypatch.setattr(envelope, "verify_bearer", fake_verify)
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: True)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(run_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    # Token scratch stays where token_scratch.create requires it: <storage root>/work/<project>/<token>.
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "settingstest")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_settingstest", "card-settings-test-pepper")
    # Wire the current startup ports and restore their registries after this fixture.
    from flowgate.graph_run import integration as graph_run
    from modules.flow_gate.graph_workflow.services import run_state_ports
    from modules.flow_gate.services import graph_execution_hooks, graph_source_hooks
    monkeypatch.setattr(run_state_ports, "_handlers", dict(run_state_ports._handlers))
    monkeypatch.setattr(graph_execution_hooks, "_handler", graph_execution_hooks._handler)
    for attr in ("_handler", "_mutation_handler", "_fence_handler"):
        monkeypatch.setattr(graph_source_hooks, attr, getattr(graph_source_hooks, attr))
    graph_run.register()
    from flowgate.graph_validation.run_scope import run_scope
    from modules.flow_gate.graph_workflow.domain import run_scope_ports
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope)
    # every migration is applied here, so the stage assignment store exists and every suite runs
    # its new attempts on the assigned path (L-2), as a deployment with migration 164 does. A pre-feature attempt is made
    # explicitly by the test that needs one (``pre_feature_attempts``), never by switching a whole suite off.
    def record_job(job):
        # Recovery may redeliver a durable execution until the fake worker starts it.
        # Record each execution once, like a worker's execution lease admission.
        if job.get("graph_exec_id") and any(
                old.get("graph_exec_id") == job["graph_exec_id"] for old in state["jobs"]):
            return
        state["jobs"].append(job)
    old_starter = node_dispatch.set_ai_starter(record_job)
    card_documents.register()
    integration.register()
    app = FastAPI()
    for module in (canvas_routes, graph_routes, run_routes, routes):
        app.include_router(module.router)
    state["client"] = TestClient(app)
    _seed(db_path)
    yield state
    integration.unregister()
    card_documents.unregister()
    node_dispatch.set_ai_starter(old_starter)


class _TestPolicy:
    """Decided test double: start nodes, wait on rejection, all done = complete."""

    def select(self, *args):
        return args[0].start_keys() if len(args) == 2 else args[2]

    def admit(self, candidates, _open):
        return candidates

    def openable(self, _target, states):
        return "open" if all(s["state"] == "completed" for s in states) else "wait"

    def route(self, *_args):
        return [], []

    def is_complete(self, revision, state):
        return all(state.node(k) is not None and state.node(k).status == "done" for k in revision.node_keys())


_POLICY = _TestPolicy()


@pytest.fixture()
def decided():
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    old = {}
    for key in policies.POLICY_KEYS:
        run_policies.register(key, TEST_VALUE, TEST_VERSION, _POLICY)
        old[key] = policies.set_system_default(key, TEST_VALUE, TEST_VERSION)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _doc(flow, seq, typ, review=None, status="open"):
    from modules.flow_gate.db import documents as db_documents

    doc_id = f"{flow}.{seq:04d}-{typ}"
    db_documents.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": flow,
                         "type_code": typ, "seq": seq, "title": f"{typ} {seq}", "status": status})
    if review is not None:
        db_documents.update(doc_id, {"doc_review_status": review})
    return doc_id


def _seed(db):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    for uid in ("u_kim", "u_lee"):
        _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'x', 't', 't')", (uid, uid, f"{uid}@x"))
    for pid in ("aip_assignee", "aip_card"):
        _sql(db, "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                 "VALUES (?, ?, 'cli', 'claude', 't', 't')", (pid, pid))
    for gid in (FA, FB):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
    _doc(FA, 1, "R", "approved")


def _h():
    return {"Authorization": "Bearer user:u_kim", "x-canvas-client-id": "tab_1"}


def node(ref, doc_type="T", node_type="document", assignee=None):
    key = {"node_key": None, "client_key": ref} if ref.startswith("tmp_") else {"node_key": ref, "client_key": None}
    return {**key, "node_type": node_type, "doc_type": doc_type if node_type == "document" else None,
            "palette_item_id": None, "label": doc_type or node_type, "assignee": assignee}


def edge(ref, source, target):
    return {"edge_id": None, "client_key": ref, "source": source, "target": target, "condition": None}


def put_draft(env, flow, version, nodes, edges=()):
    res = env["client"].put(f"/api/v1/flows/{flow}/graph/draft",
                            json={"draft_version": version, "nodes": nodes, "edges": list(edges)}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def get_settings(env, flow, key, expect=200):
    res = env["client"].get(f"/api/v1/flows/{flow}/nodes/{key}/settings", headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def patch(env, flow, key, body, expect=200):
    res = env["client"].patch(f"/api/v1/flows/{flow}/nodes/{key}/settings", json=body, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def setting(env, flow, key, field, value, expect=200):
    return patch(env, flow, key, {"field": field, "value": value}, expect)


def legacy_setting(env, flow, key, field, value):
    """seed a legacy role field for tests of the unchanged execution path.

    PATCH itself uses slots now. These callers exercise run/approval/token behavior
    until the earlier implementation; their fixture must still represent an old card's stored fields.
    """
    if field not in ("instruction_ai", "run_ai", "review_ai", "delegation"):
        return setting(env, flow, key, field, value)
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    with_flow_guard(flow, lambda: repository.put(flow, key, field, value, "u_kim"))
    return get_settings(env, flow, key)


def stored_rows(env, flow, key=None):
    sql = "SELECT node_key, field, value_json FROM graph_node_settings WHERE group_id = ?"
    rows = _sql(env["db"], sql + (" AND node_key = ?" if key else "") + " ORDER BY field",
                (flow, key) if key else (flow,))
    return {(r["node_key"], r["field"]): r["value_json"] for r in rows}


def shown(body):
    return {name: f["value"] for name, f in body["fields"].items()}


def placed(env, flow, doc_type="T", assignee=None):
    """One card placed in a new draft of ``flow``; returns its node key."""
    first = put_draft(env, flow, 0, [node("tmp_c", doc_type, assignee=assignee)])
    return first["key_map"]["tmp_c"]


@pytest.mark.parametrize("client_id", ["tab_1", "tab_2", None])
def test_settings_event_carries_request_origin_and_only_follows_changes(env, client_id):
    key = placed(env, FA)
    headers = {"Authorization": "Bearer user:u_kim"}
    if client_id is not None:
        headers["x-canvas-client-id"] = client_id
    env["events"].clear()
    url = f"/api/v1/flows/{FA}/nodes/{key}/settings"
    body = {"field": "restart", "value": 0}
    response = env["client"].patch(url, json=body, headers=headers)
    assert response.status_code == 200, response.text
    events = [ev for ev in env["events"] if ev.event_type == "graph_canvas_changed"]
    assert len(events) == 1
    assert events[0].payload["origin_client_id"] == client_id
    assert events[0].payload["reason"] == "settings"
    assert events[0].payload["node_keys"] == [key]
    version = response.json()["settings_version"]
    env["events"].clear()
    assert env["client"].patch(url, json=body, headers=headers).status_code == 200
    refused = env["client"].patch(url, json={"field": "restart", "value": "invalid"}, headers=headers)
    assert refused.status_code == 422
    assert not env["events"]
    assert get_settings(env, FA, key)["settings_version"] == version


@pytest.mark.parametrize("client_id", ["", "bad id!", "x" * 65])
def test_invalid_settings_origin_is_refused_before_mutation(env, client_id):
    key = placed(env, FA)
    before = get_settings(env, FA, key)
    env["events"].clear()
    response = env["client"].patch(
        f"/api/v1/flows/{FA}/nodes/{key}/settings",
        json={"field": "restart", "value": 0},
        headers={**_h(), "x-canvas-client-id": client_id})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["params"]["errors"] == [
        {"field": "header.x-canvas-client-id", "reason": "invalid_format"}]
    assert not env["events"]
    after = get_settings(env, FA, key)
    assert after["fields"] == before["fields"]
    assert after["settings_version"] == before["settings_version"]


# ── the settings of each card kind (pure) ────────────────────────────────────

def test_card_kinds_and_their_settings_follow_the_v7_blocks():
    from flowgate.card_settings import fields as f

    assert [f.card_kind("document", t) for t in ("N", "T", "T2", "D", "P", "L", "DB")] == [f.RUN] * 7
    assert (f.card_kind("custom", None), f.card_kind("document", "TS"), f.card_kind("document", "r"),
            f.card_kind("document", "B"), f.card_kind("gate", None)) == (f.RUN, f.TEST, f.REQUIREMENT,
                                                                       f.REQUIREMENT, f.CHECK)
    # a work plan card keeps the plan settings (values only).
    assert f.card_kind("document", "WP") == f.PLAN and f.card_kind("document", "TSR") is None
    names = {kind: [x.name for x in f.fields_of(kind)] for kind in f.CARD_KINDS}
    assert names == {
        f.RUN: RUN_FIELDS,
        f.TEST: ["instruction", "run_ai", "review_ai", "review_count", "result_cause", "follow_scope"],
        f.REQUIREMENT: [],
        # no usage; the earlier change the earlier implementation: the local merge branch next to the remote
        f.CHECK: ["archive", "merge_mode", "merge_target", "merge_target_branch", "push", "keep_branch"],
        f.PLAN: ["pre_instruction_auto", "run_ai", "restart", "review_ai", "review_count", "time_limit",
                 "review_basis", "after_review_exhausted"],
    }


def test_the_run_plan_hands_on_stored_values_and_execution_defaults():
    from flowgate.card_settings import fields as f

    assert f.run_plan(f.RUN, {}) == {"settings": {}, "step_timeout_sec": 14400, "restart_max": 3}
    # a stored after review value is no setting any more, so it is not handed on
    plan = f.run_plan(f.RUN, {"run_ai": "aip_card", "time_limit": 120, "restart": 0, "after_review": "follow"})
    assert plan == {"settings": {"run_ai": "aip_card", "time_limit": 120, "restart": 0},
                    "provider_id": "aip_card", "step_timeout_sec": 7200, "restart_max": 0}
    assert f.run_plan(f.TEST, {"run_ai": "aip_card", "review_count": 2}) == {
        "settings": {"run_ai": "aip_card", "review_count": 2}, "provider_id": "aip_card"}
    assert f.run_plan(f.REQUIREMENT, {"delegation": "on"}) == {} and f.run_plan(None, {"run_ai": "x"}) == {}


def test_the_time_limit_choices_and_default_are_the_engine_budget():
    from modules.flow_gate.services.ai_invoke import admission, runtime

    from flowgate.card_settings import fields as f

    assert all(runtime.STEP_TIMEOUT_MIN_SEC <= m * 60 <= runtime.STEP_TIMEOUT_MAX_SEC for m in f.TIME_LIMIT_MINUTES)
    # The card explicitly supplies its shared default instead of using the generic single-run budget.
    plan = f.run_plan(f.RUN, {})
    assert plan["step_timeout_sec"] == f.DEFAULT_TIME_LIMIT_MINUTES * 60 == 14400
    assert admission._resolve_timeout_sec("single", 1, False, plan["step_timeout_sec"]) == 14400
    # A pick: the single run takes it as its budget.
    assert admission._resolve_timeout_sec("single", 1, False, 120 * 60) == 7200


# ── read / write one value at a time ─────────────────────────────────────────

def test_a_new_card_reads_its_defaults_and_saves_each_value_at_once(env):
    key = placed(env, FB, "T")
    body = get_settings(env, FB, key)
    assert (body["card_kind"], body["locked"], list(body["fields"])) == ("run", False, RUN_FIELDS + ["hold"])
    assert shown(body) == {"instruction": "use", "instruction_ai": None, "run_ai": None, "restart": 3,
                           "review_ai": None, "review_count": 0, "time_limit": 240, "review_basis": "type",
                           "after_review_exhausted": "continue", "verify_max_runs": 10, "verify_run_minutes": 1,
                           "instruction_review_ai": None, "instruction_review_count": 0,
                           "instruction_note": "", "run_note": "", "run_pre_instruction": "", "hold": False}
    assert not any(f["stored"] for f in body["fields"].values())
    assert body["fields"]["time_limit"]["choices"] == [30, 45, 60, 90, 120, 180, 240]
    assert body["fields"]["restart"]["choices"][:3] == [-1, 0, 1] and body["fields"]["run_ai"]["choices"] is None
    assert stored_rows(env, FB) == {}

    one = setting(env, FB, key, "restart", 2)
    assert one["fields"]["restart"] == {"value": 2, "stored": True, "default": 3,
                                        "choices": one["fields"]["restart"]["choices"]}
    assert stored_rows(env, FB) == {(key, "restart"): "2"}               # exactly the one value
    two = setting(env, FB, key, "instruction", "omit")
    assert (shown(two)["restart"], shown(two)["instruction"]) == (2, "omit")
    assert stored_rows(env, FB) == {(key, "instruction"): '"omit"', (key, "restart"): "2"}
    back = setting(env, FB, key, "restart", None)                        # null: the default again
    assert back["fields"]["restart"]["stored"] is False and shown(back)["restart"] == 3
    assert stored_rows(env, FB) == {(key, "instruction"): '"omit"'}
    assert shown(get_settings(env, FB, key)) == shown(back)


def test_the_items_the_sequence_row_never_stored_are_saved(env):
    key = placed(env, FB, "N")
    for field, value in (("instruction", "omit"), ("restart", 1), ("time_limit", 120), ("review_basis", "last")):
        setting(env, FB, key, field, value)
    body = get_settings(env, FB, key)
    assert {k: v for k, v in shown(body).items() if body["fields"][k]["stored"]} == {
        "instruction": "omit", "restart": 1, "time_limit": 120, "review_basis": "last"}
    assert stored_rows(env, FB, key) == {(key, "instruction"): '"omit"', (key, "restart"): "1",
                                         (key, "review_basis"): '"last"', (key, "time_limit"): "120"}


def test_each_card_kind_keeps_its_own_settings(env):
    from modules.flow_gate.db import ai_groups as db_ai_groups

    db_ai_groups.create({"ai_group_id": "team_auth", "name": "auth", "name_key": "auth",
                         "name_key_hash": "a" * 64, "description": ""})
    first = put_draft(env, FB, 0, [node("tmp_ts", "TS"), node("tmp_r", "R"), node("tmp_g", None, "gate"),
                                   node("tmp_wp", "T"), node("tmp_x", None, "custom")])
    km = first["key_map"]
    setting(env, FB, km["tmp_ts"], "review_count", -1)
    ts = setting(env, FB, km["tmp_ts"], "review_ai", "aip_card")
    assert ts["card_kind"] == "test" and (shown(ts)["review_count"], shown(ts)["review_ai"]) == (-1, None)
    assert ts["ai_roles"]["roles"]["review"]["selected"] == {"kind": "assigned", "provider_id": "aip_card"}
    setting(env, FB, km["tmp_ts"], "time_limit", 60, expect=422)          # not a test card setting
    for field, value in (("scope", "confirm"), ("team", "team_auth"), ("limit_max_nodes", 12),
                         ("limit_total_reviews", 30)):
        setting(env, FB, km["tmp_r"], field, value, expect=422)
    assert shown(get_settings(env, FB, km["tmp_r"])) == {"hold": False}
    # a check card has the archive flag and a remote (null | origin) as merge target, no usage.
    for field, value in (("archive", True), ("merge_mode", "merge"), ("merge_target", "origin"),
                         ("push", True), ("keep_branch", False)):
        gate = setting(env, FB, km["tmp_g"], field, value)
    assert gate["card_kind"] == "check" and shown(gate) == {
        "archive": True, "merge_mode": "merge", "merge_target": "origin", "merge_target_branch": None, "push": True,
        "keep_branch": False, "hold": False}
    setting(env, FB, km["tmp_g"], "usage", "end", expect=422)
    # Seed a historical WP card, rather than using the retired new-card path.
    _sql(env["db"], "UPDATE graph_draft_nodes SET doc_type = 'WP' WHERE group_id = ? AND node_key = ?", (FB, km["tmp_wp"]))
    # Historical plan settings still work.
    plan = setting(env, FB, km["tmp_wp"], "restart", 1)
    assert plan["card_kind"] == "plan" and shown(plan)["restart"] == 1
    refused = setting(env, FB, km["tmp_wp"], "instruction", "omit", expect=422)
    assert refused["error"]["params"]["errors"][0]["reason"] == "not_a_setting_of_this_card"
    assert get_settings(env, FB, km["tmp_x"])["card_kind"] == "run"       # a palette [+] card: like work


@pytest.mark.parametrize("field, value, reason", [
    ("restart", 99, "not_a_choice"), ("restart", "1", "not_a_choice"), ("restart", True, "not_a_choice"),
    ("review_count", -2, "not_a_choice"), ("time_limit", 61, "not_a_choice"), ("time_limit", 60.0, "not_a_choice"),
    ("instruction", "skip", "not_a_choice"), ("review_basis", None, None), ("run_ai", "", "not_a_text"),
    ("run_ai", "a" * 192, "invalid_text"), ("review_ai", 3, "not_a_text"), ("run_ai", "aip_missing", "unknown_ai"),
])
def test_values_outside_a_run_setting_are_refused(env, field, value, reason):
    key = placed(env, FB, "T")
    if reason is None:                                                    # null is the default, never refused
        assert setting(env, FB, key, field, value)["fields"][field]["stored"] is False
        return
    body = setting(env, FB, key, field, value, expect=422)
    assert body["error"]["code"] == "invalid_request"
    if field in ("run_ai", "review_ai"):
        error = ({"field": "value", "reason": "role_value_malformed", "detail":
                  "provider_id_required" if value == "" else "not_an_object"} if not isinstance(value, str) or value == ""
                 else {"field": "value", "reason": "ai_not_selectable", "cause": "deleted"})
    else:
        error = {"field": "value", "reason": reason}
    assert body["error"]["params"]["errors"] == [error]
    assert stored_rows(env, FB) == {}


def test_other_refusals(env):
    first = put_draft(env, FB, 0, [node("tmp_r", "R"), node("tmp_g", None, "gate")])
    r, g = first["key_map"]["tmp_r"], first["key_map"]["tmp_g"]
    assert setting(env, FB, r, "team", "no_such_team", expect=422)["error"]["params"]["errors"][0]["reason"] == \
        "not_a_setting_of_this_card"
    for bad in (0, 1000, "3"):
        setting(env, FB, r, "limit_replans", bad, expect=422)
    for bad in ("a..b", "-x", "main.lock", "x/", "has space", "release/v0.2", "upstream"):      # a remote, never a branch
        setting(env, FB, g, "merge_target", bad, expect=422)
    setting(env, FB, g, "push", "yes", expect=422)
    bodies = [({"value": 1}, [{"field": "field", "reason": "required"}]),
              ({"field": "restart"}, [{"field": "value", "reason": "required"}]),
              ({"field": "push", "value": True, "extra": 1}, [{"field": "extra", "reason": "unknown_field"}]),
              ([], [{"field": "body", "reason": "not_an_object"}])]
    for body, errors in bodies:
        assert patch(env, FB, g, body, expect=422)["error"]["params"]["errors"] == errors
    assert get_settings(env, FB, "gn_nothing", expect=404)["error"]["code"] == "node_not_found"
    assert setting(env, FB, "gn_nothing", "push", True, expect=404)["error"]["code"] == "node_not_found"
    assert get_settings(env, "flowgate.v02.0999", "gn_x", expect=404)["error"]["code"] == "flow_not_found"
    res = env["client"].get(f"/api/v1/flows/{FB}/nodes/{g}/settings", headers={"Authorization": "Bearer tok_x"})
    assert res.status_code == 401
    assert stored_rows(env, FB) == {}


# ── a completed card, a running card ─────────────────────────────────────────

def _run_one_card(env, assignee):
    """A confirmed R -> T Flow (FA) with its run started; returns the T node key."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=assignee)],
                      [edge("tmp_e", "tmp_r", "tmp_t")])
    key = first["key_map"]["tmp_t"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return key


def _start(env):
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text


def test_a_running_card_is_locked_and_a_completed_card_reads_and_writes(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    from flowgate.card_settings import legacy_adapter
    from test_fg_ai_roles_l2_assignment import roles, save_instruction, write_line
    from flowgate.card_documents import routes as document_routes, legacy_adapter as documents

    monkeypatch.setattr(documents, "has_permission", lambda u, p, perm: True)
    env["client"].app.include_router(document_routes.router)
    _sql(env["db"], "UPDATE users SET is_admin = 1 WHERE user_id = ?", ("u_kim",))

    key = _run_one_card(env, {"kind": "human", "user_id": "u_lee", "provider_id": None})
    # Use real required instruction/report outputs; review is explicitly unused for this human card.
    roles(key, "not_used", "not_used", "not_used")
    setting(env, FA, key, "review_count", 2)                              # before the run: the confirmed card
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_draft_nodes WHERE group_id = ?", (FA,))[0]["n"] == 0
    _start(env)
    assert get_settings(env, FA, key)["locked"] is False                  # a person's open card waits
    monkeypatch.setattr(legacy_adapter, "node_ai_run_active", lambda flow_id, node_key: node_key == key)
    assert get_settings(env, FA, key)["locked"] is True                   # an AI execution of it works: running
    locked = setting(env, FA, key, "restart", 1, expect=409)
    assert locked["error"]["code"] == "node_settings_locked"
    monkeypatch.setattr(legacy_adapter, "node_ai_run_active", lambda flow_id, node_key: False)
    instruction = save_instruction(env, key)
    assert db_documents.get_by_id(instruction)["doc_review_status"] == "approved"
    write_line(env, key, "report", "## Report\n\nCompleted settings test report.\n")
    status = _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (key,))[0]["status"]
    assert status == "done"
    done = get_settings(env, FA, key)                                     # a completed card, revision only
    assert (done["card_kind"], done["locked"], shown(done)["review_count"]) == ("run", False, 2)
    after = setting(env, FA, key, "restart", 1)
    assert shown(after)["restart"] == 1 and stored_rows(env, FA, key)[(key, "restart")] == "1"


# ── the run path reads the card ──────────────────────────────────────────────

AI = {"kind": "ai", "user_id": None, "provider_id": "aip_assignee"}


def _fake_ai_invoke(monkeypatch):
    """The v0.1 AI invoke admission as the Graph sees it: what start_run is called with."""
    from modules.flow_gate.services import ai_invoke_service

    calls = []

    def fake_start_run(**kw):
        calls.append(kw)
        return {"run_id": "air_card_1", "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    return calls


def _start_job(env, monkeypatch, job):
    from modules.flow_gate.graph_workflow.services import ai_node_starter

    calls = _fake_ai_invoke(monkeypatch)
    ai_node_starter.start_ai_invoke(job)
    assert len(calls) == 1
    return calls[0]


def test_an_ai_node_starts_with_its_card_run_ai_time_limit_and_restarts(env, decided, monkeypatch):
    key = _run_one_card(env, AI)
    for field, value in (("run_ai", "aip_card"), ("time_limit", 120), ("restart", 2), ("review_basis", "last"),
                         ("instruction", "omit")):
        legacy_setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["step_timeout_sec"], job["restart_max"]) == (
        key, "aip_card", 7200, 2)
    assert job["card_settings"] == {"instruction": "omit", "run_ai": "aip_card", "restart": 2, "time_limit": 120,
                                    "review_basis": "last"}
    token = _sql(env["db"], "SELECT provider_id FROM tokens WHERE token_id = ?", (job["token"]["token_id"],))[0]
    assert token["provider_id"] == "aip_card"                           # the node token names the card's AI
    call = _start_job(env, monkeypatch, job)
    assert {k: call[k] for k in ("mode", "action_scope", "provider_id", "continuation_step_timeout_sec",
                                 "node_restart_max_attempts")} == {
        # (``result_slot_binding``): a card's result stage writes into its preallocated result slot, so
        # the start is an edit of that document (the assertion lagged behind with "new").
        "mode": "single", "action_scope": "edit", "provider_id": "aip_card",
        "continuation_step_timeout_sec": 7200, "node_restart_max_attempts": 2}


def test_a_card_that_stores_nothing_starts_with_shared_defaults(env, decided, monkeypatch):
    key = _run_one_card(env, AI)
    _start(env)
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["step_timeout_sec"], job["restart_max"],
            job["card_settings"]) == (key, "aip_assignee", 14400, 3, {})
    call = _start_job(env, monkeypatch, job)
    assert (call["provider_id"], call["continuation_step_timeout_sec"], call["node_restart_max_attempts"]) == (
        "aip_assignee", 14400, 3)






# ── AI delegation: the requirement card's line follows its setting ───────────

def _card_lines(env, flow, key):
    rows = _sql(env["db"], "SELECT slot, doc_type, doc_id, owned FROM graph_node_documents "
                           "WHERE group_id = ? AND node_key = ? ORDER BY ordinal", (flow, key))
    return [(r["slot"], r["doc_type"]) for r in rows], rows





# ── the earlier change the earlier implementation: after review is gone (migration 147) ─────────────







# ── migration 146, wiring and placement ──────────────────










# ── the earlier change the earlier implementation: the AI delegation line's document in the PATCH request's language ─────────────────────




# result confirmation settings are persisted, but never become AI plan settings.


# ── the earlier change the earlier implementation: the check card's local merge target branch ─────────────────

import base64  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

_GIT = shutil.which("git") is not None


def _gate_key(env, flow=FB):
    return put_draft(env, flow, 0, [node("tmp_g", None, "gate")])["key_map"]["tmp_g"]


def _put_raw(env, flow, key, field, value_json):
    """A stored row as an old server (or migration 158's leftovers) left it."""
    _sql(env["db"], "DELETE FROM graph_node_settings WHERE group_id = ? AND node_key = ? AND field = ?",
         (flow, key, field))
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, ?, ?, 't')", (flow, key, field, value_json))


def _get_refreshed(env, flow, key):
    res = env["client"].get(f"/api/v1/flows/{flow}/nodes/{key}/settings?refresh_merge_targets=true", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


















def _git_cmd(args, cwd):
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture()
def git_flow(env, tmp_path, monkeypatch, git_repositories):
    """The project on a temporary bare origin: main (2 commits), feature/v0.2.0 (main + 1), old (main~1: lacks the
    groups' work base history), remote-only; base checkout provisioned with the local release/local (= main, never
    pushed); FB and FA have their worktrees."""
    if not _GIT:
        pytest.skip("git binary unavailable")
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.storage.paths import src_root

    monkeypatch.setenv("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
    monkeypatch.setattr(svc, "_sweep_daemon_started", True)
    # What is judged, not how fast this host spawns Git: the 3 s budget itself is tested with the merge target suite.
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 60.0)
    bare, seed = git_repositories("card-settings-v02", tmp_path)
    svc.save_config("flowgate", {"repo_url": bare.as_uri(), "provider": "generic", "base_branch": "main",
                                 "default_finalize_action": "merge", "enabled": True,
                                 "author_name": "T", "author_email": "t@t"})
    assert svc.ensure_worktree("flowgate", "v02", FB) == "ok"
    assert svc.ensure_worktree("flowgate", "v02", FA) == "ok"
    base = src_root("flowgate", "main")
    _git_cmd(["branch", "feature/v0.2.0", "origin/feature/v0.2.0"], base)
    _git_cmd(["branch", "old", "origin/old"], base)
    _git_cmd(["branch", "release/local", "main"], base)
    mt.reset_check_targets()
    yield {"base": base, "own": db_git.get_state(FB)["branch"], "other": db_git.get_state(FA)["branch"]}
    mt.reset_check_targets()
    svc.delete_config("flowgate")


# the two Git-heavy tests that ended this file (``test_the_merge_target_branch_is_saved_only_for_a_target_the_judgement_allows``,
# ``test_a_group_without_its_worktree_yet_lists_and_saves_real_branch_names``) were moved verbatim to
# ``test_fg_card_settings_merge_target_git_0146.py`` / ``test_fg_card_settings_no_worktree_git_0146.py`` (they used the ``git_flow``
# fixture above) so ``--dist loadfile`` can run them on different workers; the 255 s they took here was the long pole of this file.


# a client explicitly keeps only the merge target view.
_TARGET_VIEW_KEYS_0148 = {"merge_target_options", "default_target_branch", "merge_target_state"}
