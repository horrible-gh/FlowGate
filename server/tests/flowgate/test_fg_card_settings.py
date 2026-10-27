"""flowgate.v02.0053 T#2 — card (Node) run settings: stored on the card, read and changed one value at a time.

The feature lives in the v0.2 package ``flowgate.card_settings`` (D0006 §2.1); the Graph workflow reaches it
only through ``card_settings_ports`` (node dispatch), registered here as ``startup`` does. ``tests/flowgate/``
placement (NR0003 §6.3).

On a real SQLite DB (all migrations, TestClient over the Graph routes and the card settings routes): a card
placed in a draft and a card whose run is done both read and write their settings; every value is saved by
its own PATCH; the items the v0.1 sequence row never stored (instruction use / omit, restart, time limit,
review basis) are stored (after review is gone, flowgate.v02.0056 T#1: no setting, never read, migration 147); values outside a setting are refused; a running card is locked; and an
AI node starts with its card's run AI, time limit and restarts (the AI invoke start receives them), or exactly
as before when the card stores nothing. Auth, RBAC and SSE delivery are stubbed.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
# flowgate.v02.0083 T#1 (T0 baseline): the app settings these suites reach (the card approval's v0.1 approval)
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

FA = "flowgate.v02.0091"      # unassigned, live root R (approved)
FB = "flowgate.v02.0092"      # unassigned, no documents
TEST_VALUE, TEST_VERSION = "card_settings_test", "1"
# flowgate.v02.0056 T#2: the run cards' instruction AI comes right after the instruction it writes.
RUN_FIELDS = ["instruction", "instruction_ai", "run_ai", "restart", "review_ai", "review_count", "time_limit",
              "review_basis"]


@pytest.fixture()
def db_path(tmp_path):
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    path = str(tmp_path / "cards.db")
    conn = sqlite3.connect(path)
    for mig in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.close()
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
def env(db_path, monkeypatch, tmp_path):
    from modules.flow_gate.db import _SqliteTxnAdapter
    from modules.flow_gate.graph_workflow.api import canvas_routes, envelope, graph_routes, run_routes
    from modules.flow_gate.graph_workflow.services import canvas_events, node_dispatch, run_events

    from flowgate.card_approval import routes as card_approval_routes
    from flowgate.card_documents import integration as card_documents
    from flowgate.card_settings import integration, routes

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
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
    for module in (canvas_routes, graph_routes, run_routes, routes, card_approval_routes):
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


# ── the settings of each card kind (pure) ────────────────────────────────────

def test_card_kinds_and_their_settings_follow_the_v7_blocks():
    from flowgate.card_settings import fields as f

    assert [f.card_kind("document", t) for t in ("N", "T", "T2", "D", "P", "L", "DB")] == [f.RUN] * 7
    assert (f.card_kind("custom", None), f.card_kind("document", "TS"), f.card_kind("document", "r"),
            f.card_kind("document", "B"), f.card_kind("gate", None)) == (f.RUN, f.TEST, f.REQUIREMENT,
                                                                       f.REQUIREMENT, f.CHECK)
    # flowgate.v02.0072 T#3: a work plan card keeps the plan settings (values only).
    assert f.card_kind("document", "WP") == f.PLAN and f.card_kind("document", "TSR") is None
    names = {kind: [x.name for x in f.fields_of(kind)] for kind in f.CARD_KINDS}
    assert names == {
        f.RUN: RUN_FIELDS,
        f.TEST: ["instruction", "run_ai", "review_ai", "review_count", "result_cause", "follow_scope"],
        f.REQUIREMENT: ["delegation", "scope", "team", "limit_max_nodes", "limit_total_reviews",
                        "limit_retries_per_card", "limit_replans"],
        # 0085 T#1: no usage; flowgate.v02.0098 T#1: the local merge branch next to the remote
        f.CHECK: ["archive", "merge_mode", "merge_target", "merge_target_branch", "push", "keep_branch"],
        f.PLAN: ["pre_instruction_auto", "run_ai", "restart", "review_ai", "review_count", "time_limit",
                 "review_basis"],
    }


def test_the_run_plan_hands_on_only_what_the_card_stores():
    from flowgate.card_settings import fields as f

    assert f.run_plan(f.RUN, {}) == {"settings": {}}
    # flowgate.v02.0056 T#1: a stored after review value is no setting any more, so it is not handed on
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
    # No pick: a single node run's own budget (one document) is the default the card shows.
    assert admission._resolve_timeout_sec("single", 1, False, None) == f.DEFAULT_TIME_LIMIT_MINUTES * 60
    # A pick: the single run takes it as its budget (0446 T0010).
    assert admission._resolve_timeout_sec("single", 1, False, 120 * 60) == 7200


# ── read / write one value at a time ─────────────────────────────────────────

def test_a_new_card_reads_its_defaults_and_saves_each_value_at_once(env):
    key = placed(env, FB, "T")
    body = get_settings(env, FB, key)
    assert (body["card_kind"], body["locked"], list(body["fields"])) == ("run", False, RUN_FIELDS)
    assert shown(body) == {"instruction": "use", "instruction_ai": None, "run_ai": None, "restart": 0,
                           "review_ai": None, "review_count": 0, "time_limit": 60, "review_basis": "type"}
    assert not any(f["stored"] for f in body["fields"].values())
    assert body["fields"]["time_limit"]["choices"] == [30, 45, 60, 90, 120, 180, 240]
    assert body["fields"]["restart"]["choices"][:3] == [-1, 0, 1] and body["fields"]["run_ai"]["choices"] is None
    assert stored_rows(env, FB) == {}

    one = setting(env, FB, key, "restart", 2)
    assert one["fields"]["restart"] == {"value": 2, "stored": True, "default": 0,
                                        "choices": one["fields"]["restart"]["choices"]}
    assert stored_rows(env, FB) == {(key, "restart"): "2"}               # exactly the one value
    two = setting(env, FB, key, "instruction", "omit")
    assert (shown(two)["restart"], shown(two)["instruction"]) == (2, "omit")
    assert stored_rows(env, FB) == {(key, "instruction"): '"omit"', (key, "restart"): "2"}
    back = setting(env, FB, key, "restart", None)                        # null: the default again
    assert back["fields"]["restart"]["stored"] is False and shown(back)["restart"] == 0
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
                                   node("tmp_wp", "WP"), node("tmp_x", None, "custom")])
    km = first["key_map"]
    setting(env, FB, km["tmp_ts"], "review_count", -1)
    ts = setting(env, FB, km["tmp_ts"], "review_ai", "aip_card")
    assert ts["card_kind"] == "test" and (shown(ts)["review_count"], shown(ts)["review_ai"]) == (-1, "aip_card")
    setting(env, FB, km["tmp_ts"], "time_limit", 60, expect=422)          # not a test card setting
    for field, value in (("scope", "confirm"), ("team", "team_auth"), ("limit_max_nodes", 12),
                         ("limit_total_reviews", 30)):
        req = setting(env, FB, km["tmp_r"], field, value)
    assert req["card_kind"] == "requirement"
    assert {k: v for k, v in shown(req).items()} == {
        "delegation": "off", "scope": "confirm", "team": "team_auth", "limit_max_nodes": 12,
        "limit_total_reviews": 30, "limit_retries_per_card": None, "limit_replans": None}
    # flowgate.v02.0085 T#1: a check card has the archive flag and a remote (null | origin) as merge target, no usage.
    for field, value in (("archive", True), ("merge_mode", "merge"), ("merge_target", "origin"),
                         ("push", True), ("keep_branch", False)):
        gate = setting(env, FB, km["tmp_g"], field, value)
    assert gate["card_kind"] == "check" and shown(gate) == {
        "archive": True, "merge_mode": "merge", "merge_target": "origin", "merge_target_branch": None, "push": True,
        "keep_branch": False, "hold": False}
    setting(env, FB, km["tmp_g"], "usage", "end", expect=422)
    # flowgate.v02.0072 T#3: a work plan card keeps the plan settings; a run card's instruction is not one of them.
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
    assert body["error"]["params"]["errors"] == [{"field": "value", "reason": reason}]
    assert stored_rows(env, FB) == {}


def test_other_refusals(env):
    first = put_draft(env, FB, 0, [node("tmp_r", "R"), node("tmp_g", None, "gate")])
    r, g = first["key_map"]["tmp_r"], first["key_map"]["tmp_g"]
    assert setting(env, FB, r, "team", "no_such_team", expect=422)["error"]["params"]["errors"][0]["reason"] == \
        "unknown_team"
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


def test_a_running_card_is_locked_and_a_completed_card_reads_and_writes(env, decided):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    key = _run_one_card(env, {"kind": "human", "user_id": "u_lee", "provider_id": None})
    setting(env, FA, key, "review_count", 2)                              # before the run: the confirmed card
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_draft_nodes WHERE group_id = ?", (FA,))[0]["n"] == 0
    _start(env)
    assert get_settings(env, FA, key)["locked"] is True
    locked = setting(env, FA, key, "restart", 1, expect=409)
    assert locked["error"]["code"] == "node_settings_locked"
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    result = _doc(FA, 90, "T")
    v01_contact.after_inbox_registered(FA, result, "T", token)
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
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
        setting(env, FA, key, field, value)
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
        "mode": "single", "action_scope": "new", "provider_id": "aip_card",
        "continuation_step_timeout_sec": 7200, "node_restart_max_attempts": 2}


def test_a_card_that_stores_nothing_starts_exactly_as_before(env, decided, monkeypatch):
    key = _run_one_card(env, AI)
    _start(env)
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["step_timeout_sec"], job["restart_max"],
            job["card_settings"]) == (key, "aip_assignee", None, None, {})
    call = _start_job(env, monkeypatch, job)
    assert (call["provider_id"], call["continuation_step_timeout_sec"], call["node_restart_max_attempts"]) == (
        "aip_assignee", None, None)


def test_without_the_port_handler_a_node_ignores_its_card(env, decided):
    from flowgate.card_settings import integration

    key = _run_one_card(env, AI)
    setting(env, FA, key, "run_ai", "aip_card")
    integration.unregister()
    _start(env)
    [job] = env["jobs"]
    assert (job["provider_id"], job["step_timeout_sec"], job["restart_max"]) == ("aip_assignee", None, None)


def test_a_human_node_does_not_read_its_card(env, decided):
    key = _run_one_card(env, {"kind": "human", "user_id": "u_lee", "provider_id": None})
    setting(env, FA, key, "run_ai", "aip_card")
    _start(env)
    assert env["jobs"] == []
    token = _sql(env["db"], "SELECT t.provider_id, t.issued_to FROM tokens t JOIN graph_run_token_map m "
                            "ON m.token_id = t.token_id WHERE m.node_key = ?", (key,))[0]
    assert (token["provider_id"], token["issued_to"]) == (None, "u_lee")


# ── AI delegation: the requirement card's line follows its setting ───────────

def _card_lines(env, flow, key):
    rows = _sql(env["db"], "SELECT slot, doc_type, doc_id, owned FROM graph_node_documents "
                           "WHERE group_id = ? AND node_key = ? ORDER BY ordinal", (flow, key))
    return [(r["slot"], r["doc_type"]) for r in rows], rows


def test_turning_ai_delegation_on_and_off_adds_and_removes_its_line(env):
    from modules.flow_gate.db import documents as db_documents

    first = put_draft(env, FB, 0, [node("tmp_r", "R")])
    key = first["key_map"]["tmp_r"]
    assert _card_lines(env, FB, key)[0] == [("requirement", "R")]
    setting(env, FB, key, "delegation", "on")
    lines, rows = _card_lines(env, FB, key)
    assert lines == [("requirement", "R"), ("delegation", "M")]
    delegation_doc = rows[1]["doc_id"]
    assert db_documents.get_by_id(delegation_doc)["status"] == "draft" and rows[1]["owned"] == 1
    setting(env, FB, key, "delegation", "on")                             # once
    assert len(_card_lines(env, FB, key)[1]) == 2
    resaved = put_draft(env, FB, 1, [node(key, "R")])                     # an unchanged card keeps it
    assert [(x["slot"], x["doc_type"]) for x in resaved["card_documents"][key]] == lines
    retyped = put_draft(env, FB, 2, [node(key, "B")])                     # retyped: the setting still holds
    assert ("delegation", "M") in [(x["slot"], x["doc_type"]) for x in retyped["card_documents"][key]]
    assert _card_lines(env, FB, key)[1][-1]["doc_id"] == delegation_doc
    setting(env, FB, key, "delegation", None)                            # back to the default: off
    assert ("delegation", "M") not in _card_lines(env, FB, key)[0]
    assert db_documents.get_by_id(delegation_doc) is None                # its untouched draft goes with it


# ── flowgate.v02.0056 T#1: after review is gone (migration 147) ─────────────

def test_after_review_is_no_setting_and_a_stored_value_is_not_read(env):
    from flowgate.card_settings import fields as f

    key = placed(env, FB, "T")
    refused = setting(env, FB, key, "after_review", "human", expect=422)
    assert refused["error"]["params"]["errors"] == [
        {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": "run"}]
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, 'after_review', '\"human\"', 't')", (FB, key))
    body = get_settings(env, FB, key)                                     # a row left from before: not read
    assert "after_review" not in body["fields"] and list(body["fields"]) == RUN_FIELDS
    assert f.run_plan(f.RUN, {"after_review": "human"}) == {"settings": {}}
    # what follows a result is the former follow, whatever was stored
    assert [f.after_result(kind, {"after_review": "human"}) for kind in (f.RUN, f.TEST)] == [f.APPROVE, f.APPROVE]
    assert all(f.field_of(kind, "after_review") is None for kind in f.CARD_KINDS)


def test_migration_147_deletes_only_the_after_review_rows(env):
    key = placed(env, FB, "T")
    setting(env, FB, key, "restart", 1)
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, 'after_review', '\"human\"', 't')", (FB, key))
    sql = (_MIGRATIONS_DIR / "147_graph_node_settings_after_review.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(env["db"])
    try:
        conn.executescript(sql)
    finally:
        conn.close()
    assert stored_rows(env, FB) == {(key, "restart"): "1"}


def test_migration_147_is_the_same_delete_in_three_dialects():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "147_graph_node_settings_after_review.sql").read_text(
            encoding="utf-8")
        statements = [line.strip() for line in sql.splitlines()
                      if line.strip() and not line.startswith("--") and line.strip() not in ("BEGIN;", "COMMIT;")]
        assert statements == ["DELETE FROM graph_node_settings WHERE field = 'after_review';"], dialect


# ── migration 146, wiring and placement (D0006 §2.1 / §4.1) ──────────────────

def test_migration_146_table_trigger_and_flow_delete_cleanup(env):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert "graph_node_settings" in tables and "trg_graph_node_settings_group_deleted" in triggers
    setting(env, FB, placed(env, FB, "T"), "restart", 1)
    setting(env, FA, placed(env, FA, "N"), "restart", 1)
    conn = sqlite3.connect(env["db"])
    try:
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FB,))
        conn.commit()
    finally:
        conn.close()
    assert stored_rows(env, FB) == {} and len(stored_rows(env, FA)) == 1


def test_migration_146_has_the_same_table_in_three_dialects():
    found = {}
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "146_graph_node_settings.sql").read_text(encoding="utf-8")
        found[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), dialect
        assert not re.search(r"\b(ALTER|DROP|UPDATE)\b", sql), dialect
        assert "PRIMARY KEY (group_id, node_key, field)" in sql, dialect
    assert found["sqlite"] == found["postgres"] == found["mysql"] == ["graph_node_settings"]


def test_startup_registers_the_port_and_main_mounts_the_routes():
    import startup
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert not ports.registered(ports.RUN_SETTINGS)
        startup.start_card_settings_integration()
        assert ports.registered(ports.RUN_SETTINGS)
        assert "start_card_settings_integration()" in (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    finally:
        integration.unregister()
    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "from flowgate.card_settings.routes import router as _card_settings_router" in main
    assert 'app.include_router(_card_settings_router, prefix=f"{CONTEXT}", tags=["CardSettings"])' in main


def test_the_feature_is_in_the_v02_package_and_the_graph_holds_only_the_port():
    import ast

    pkg = _SERVER_DIR / "flowgate" / "card_settings"
    assert {p.stem for p in pkg.glob("*.py")} == {
        "__init__", "fields", "settings", "review_stage", "routes", "repository", "legacy_adapter", "integration"}
    graph = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow"
    callers = sorted(p.relative_to(graph).as_posix() for p in graph.rglob("*.py")
                     if "card_settings_ports" in p.read_text(encoding="utf-8") and p.name != "card_settings_ports.py")
    assert callers == ["services/node_approval.py", "services/node_dispatch.py", "services/result_binder.py"]
    tree = ast.parse((graph / "services" / "card_settings_ports.py").read_text(encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "threading", "typing"}



# ── flowgate.v02.0088 T#1: the AI delegation line's document in the PATCH request's language ─────────────────────

@pytest.mark.parametrize("locale, name", [("ko", "AI 위임"), ("ja", "AI委任"), ("en", "AI delegation")])
def test_turning_ai_delegation_on_titles_its_new_document_in_the_request_s_language(env, locale, name):
    from modules.flow_gate.db import documents as db_documents

    key = put_draft(env, FB, 0, [node("tmp_r", "R")])["key_map"]["tmp_r"]
    in_locale = {**_h(), "x-locale": locale}
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{key}/settings", json={"field": "delegation", "value": "on"},
                              headers=in_locale)
    assert res.status_code == 200, res.text
    lines, rows = _card_lines(env, FB, key)
    assert lines == [("requirement", "R"), ("delegation", "M")]
    delegation_doc = rows[1]["doc_id"]
    assert db_documents.get_by_id(delegation_doc)["title"] == f"R · {name}"
    assert db_documents.get_by_id(rows[0]["doc_id"])["title"] == "R · 요구사항"         # placed in ko: kept
    other = "ja" if locale != "ja" else "en"
    again = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{key}/settings",
                                json={"field": "delegation", "value": "on"}, headers={**_h(), "x-locale": other})
    assert again.status_code == 200, again.text                                  # on again: the same line, no new title
    assert _card_lines(env, FB, key)[1] == rows
    assert db_documents.get_by_id(delegation_doc)["title"] == f"R · {name}"
    resaved = env["client"].put(f"/api/v1/flows/{FB}/graph/draft", headers={**_h(), "x-locale": other},
                                json={"draft_version": 1, "nodes": [node(key, "R")], "edges": []})
    assert resaved.status_code == 200, resaved.text                              # a re-save keeps it too
    assert _card_lines(env, FB, key)[1] == rows and db_documents.get_by_id(delegation_doc)["title"] == f"R · {name}"


# 0071 T1: result confirmation settings are persisted, but never become AI plan settings.
def test_test_card_confirmation_settings_round_trip_and_stay_out_of_ai_plan(env):
    from flowgate.card_settings import fields as f
    first = put_draft(env, FB, 0, [node("tmp_ts", "TS"), node("tmp_t", "T")])
    ts_key, task_key = first["key_map"]["tmp_ts"], first["key_map"]["tmp_t"]
    initial = get_settings(env, FB, ts_key)
    assert shown(initial)["result_cause"] == "ai"
    assert shown(initial)["follow_scope"] == "summary"
    assert initial["fields"]["result_cause"]["choices"] == ["ai", "human"]
    assert initial["fields"]["follow_scope"]["choices"] == ["summary", "full"]
    for name, value in (("result_cause", "human"), ("follow_scope", "full")):
        setting(env, FB, ts_key, name, value)
        assert shown(get_settings(env, FB, ts_key))[name] == value
        setting(env, FB, ts_key, name, "unsupported", expect=422)
        assert shown(get_settings(env, FB, ts_key))[name] == value
        setting(env, FB, task_key, name, value, expect=422)
    assert f.run_plan(f.TEST, {"run_ai": "aip_card", "result_cause": "human", "follow_scope": "full"}) == {
        "settings": {"run_ai": "aip_card"}, "provider_id": "aip_card"}


# ── flowgate.v02.0098 T#1: the check card's local merge target branch (D0004 §3.1 / §3.3 / §3.5) ─────────────────

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


def test_local_branch_checks_the_value_alone_and_never_rewrites_it():
    from flowgate.card_settings import fields as f

    field = f.field_of(f.CHECK, "merge_target_branch")
    assert (field.kind, field.default, f.choices_of(field, ())) == (f.LOCAL_BRANCH, None, None)
    assert f.field_of(f.CHECK, "merge_target").choices == ("origin",)          # the remote keeps its meaning
    assert f.field_of(f.RUN, "merge_target_branch") is None                    # a check card setting only
    kw = {"repeat_choices": (), "team_exists": lambda _: False, "ai_exists": lambda _: False}
    # Git's own name rules and the branch itself are the save's repository check, not the value check
    for name in ("main", "feature/v0.2.0", "a..b", "release/v0.2", "한글", "x" * 300):
        assert f.check_value(field, name, **kw) == name
    for bad, reason in (("", "not_a_text"), (1, "not_a_text"), (True, "not_a_text"), (["main"], "not_a_text"),
                        (" main", "surrounding_whitespace"), ("main\n", "surrounding_whitespace"),
                        ("   ", "surrounding_whitespace"), ("a\tb", "control_character"),
                        ("a\x7fb", "control_character")):
        with pytest.raises(f.InvalidValue) as caught:
            f.check_value(field, bad, **kw)
        assert caught.value.reason == reason, bad


def test_a_check_card_without_git_answers_its_target_state_and_refuses_a_branch(env):
    g = _gate_key(env)
    body = get_settings(env, FB, g)
    assert body["fields"]["merge_target_branch"] == {"value": None, "stored": False, "default": None, "choices": None}
    assert (body["merge_target_options"], body["default_target_branch"], body["push_remote_check_required"]) == (
        [], None, False)
    state = body["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("git_unavailable", "gate_git_unavailable",
                                                                 "git_disabled")
    assert state["current"]["inherited"] is True and state["current"]["branch"] is None
    for mode in (None, "hold", "commit"):                     # every merge mode checks a new branch the same way
        if mode:
            setting(env, FB, g, "merge_mode", mode)
        version = get_settings(env, FB, g)["settings_version"]
        refused = setting(env, FB, g, "merge_target_branch", "main", expect=409)
        assert refused["error"]["code"] == "gate_git_unavailable"
        assert refused["error"]["params"]["reason"] == "git_disabled"
        assert (refused["error"]["params"]["field"], refused["error"]["params"]["target_branch"]) == (
            "merge_target_branch", "main")
        assert (g, "merge_target_branch") not in stored_rows(env, FB, g)
        assert get_settings(env, FB, g)["settings_version"] == version
    bad = setting(env, FB, g, "merge_target_branch", " main", expect=422)      # the value itself: no Git asked
    assert bad["error"]["params"]["errors"] == [{"field": "value", "reason": "surrounding_whitespace"}]
    # a stored target is shown with its state, and null clears it with no Git read
    _put_raw(env, FB, g, "merge_target_branch", '"feature/gone"')
    held = get_settings(env, FB, g)
    assert held["fields"]["merge_target_branch"]["value"] == "feature/gone"
    assert held["merge_target_state"]["current"] == {
        "branch": "feature/gone", "inherited": False, "checked": True, "error": "gate_git_unavailable",
        "unmerge_supported": None, "warnings": []}
    version = held["settings_version"]
    cleared = patch(env, FB, g, {"field": "merge_target_branch", "value": None, "expected_settings_version": version})
    assert cleared["fields"]["merge_target_branch"]["stored"] is False and cleared["settings_version"] == version + 1
    # the repository is the authenticated flow's own project: a client cannot name another one
    other = patch(env, FB, g, {"field": "merge_target_branch", "value": "main", "project_id": "other"}, expect=422)
    assert other["error"]["params"]["errors"] == [{"field": "project_id", "reason": "unknown_field"}]


def test_push_on_saves_the_origin_remote_with_it_in_one_change(env):
    g = _gate_key(env)
    on = setting(env, FB, g, "push", True)
    assert (shown(on)["push"], shown(on)["merge_target"], on["settings_version"]) == (True, "origin", 1)
    assert on["push_remote_check_required"] is False and on["legacy"] == {}
    assert stored_rows(env, FB, g) == {(g, "merge_target"): '"origin"', (g, "push"): "true"}
    assert setting(env, FB, g, "push", True)["settings_version"] == 1           # both already so: no change
    off = setting(env, FB, g, "push", False)                                    # off keeps the chosen remote
    assert (shown(off)["push"], shown(off)["merge_target"], off["settings_version"]) == (False, "origin", 2)
    assert shown(setting(env, FB, g, "push", None))["merge_target"] == "origin"
    # an old client's single write of the remote keeps its allowed values
    assert shown(setting(env, FB, g, "merge_target", None))["merge_target"] is None
    assert shown(setting(env, FB, g, "merge_target", "origin"))["merge_target"] == "origin"
    setting(env, FB, g, "merge_target", "main", expect=422)


def test_push_on_with_no_remote_is_shown_and_corrected_by_saving_push_again(env):
    g = _gate_key(env)
    _put_raw(env, FB, g, "push", "true")                                         # an old push=true with no remote
    before = get_settings(env, FB, g)
    assert (before["push_remote_check_required"], before["settings_version"]) == (True, 0)
    assert get_settings(env, FB, g)["push_remote_check_required"] is True        # a GET corrects nothing
    assert stored_rows(env, FB, g) == {(g, "push"): "true"}
    fixed = patch(env, FB, g, {"field": "push", "value": True, "expected_settings_version": 0})
    assert (shown(fixed)["merge_target"], fixed["settings_version"], fixed["push_remote_check_required"]) == (
        "origin", 1, False)


def test_push_on_keeps_the_legacy_branch_and_refuses_a_conflicting_one(env):
    g = _gate_key(env)
    _put_raw(env, FB, g, "legacy_merge_target", '"release/old"')
    kept = setting(env, FB, g, "push", True)
    assert kept["legacy"] == {"legacy_merge_target": "release/old"} and shown(kept)["merge_target"] == "origin"
    assert shown(kept)["merge_target_branch"] is None                           # never moved into the new field
    keys = put_draft(env, FA, 0, [node("tmp_a", None, "gate"), node("tmp_b", None, "gate")])["key_map"]
    a, b = keys["tmp_a"], keys["tmp_b"]
    # an old branch name migration 158 has not moved yet: kept as the legacy value in the same change
    _put_raw(env, FA, a, "merge_target", '"release/v0.2"')
    moved = setting(env, FA, a, "push", True)
    assert moved["legacy"] == {"legacy_merge_target": "release/v0.2"} and moved["settings_version"] == 1
    assert stored_rows(env, FA, a) == {(a, "legacy_merge_target"): '"release/v0.2"',
                                       (a, "merge_target"): '"origin"', (a, "push"): "true"}
    # a different legacy value already kept: the whole change is refused and nothing is written
    _put_raw(env, FA, b, "merge_target", '"release/v0.2"')
    _put_raw(env, FA, b, "legacy_merge_target", '"older"')
    rows = stored_rows(env, FA, b)
    refused = setting(env, FA, b, "push", True, expect=409)
    assert refused["error"]["code"] == "gate_settings_invalid"
    assert refused["error"]["params"]["reason"] == "legacy_merge_target_conflict"
    assert stored_rows(env, FA, b) == rows and get_settings(env, FA, b)["settings_version"] == 0


def test_push_on_writes_both_or_nothing_and_a_stale_version_writes_neither(env, monkeypatch):
    from flowgate.card_settings import repository

    g = _gate_key(env)
    real = repository.put

    def failing(flow_id, node_key, field, value, by):
        if field == "push":
            raise RuntimeError("write failed")
        return real(flow_id, node_key, field, value, by)

    monkeypatch.setattr(repository, "put", failing)
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{g}/settings", json={"field": "push", "value": True},
                              headers=_h())
    assert res.status_code == 500
    monkeypatch.setattr(repository, "put", real)
    assert stored_rows(env, FB, g) == {} and get_settings(env, FB, g)["settings_version"] == 0   # remote rolled back
    # two screens drawn from version 0: the first change wins, the other writes neither push nor the remote
    assert patch(env, FB, g, {"field": "archive", "value": True, "expected_settings_version": 0})["settings_version"] == 1
    stale = patch(env, FB, g, {"field": "push", "value": True, "expected_settings_version": 0}, expect=409)
    assert stale["error"]["code"] == "gate_context_changed"
    assert stored_rows(env, FB, g) == {(g, "archive"): "true"} and get_settings(env, FB, g)["settings_version"] == 1


@pytest.mark.parametrize("status, code, details", [
    (422, "merge_target_invalid", {}),
    (409, "merge_target_remote_only", {}),
    (409, "merge_target_internal_slot", {"connected_group_id": "flowgate.v02.0091"}),
    (404, "merge_target_not_found", {}),
    (409, "merge_target_same_branch", {}),
    (409, "merge_target_incoming_work_base", {"incoming_work_base_commits": 3, "work_base_ref": "feature/v0.2.0"}),
    (409, "merge_target_preview_unavailable", {"work_base_ref": "feature/v0.2.0"}),
    (409, "merge_target_busy", {"merge_id": 7, "blocking_group_id": "flowgate.v02.0091",
                                "route": "/api/v1/groups/x/git/merge/7", "project_id": "flowgate"}),
    (409, "merge_target_owner_mismatch", {"reason": "owner_marker_mismatch"}),
    (409, "base_checkout_branch_mismatch", {"base_branch": "main", "checked_out_branch": None,
                                            "base_root": "C:/secret/storage/src/flowgate/main"}),
    (409, "base_dirty", {"files": ["a.txt"]}),
    (409, "merge_conflict_open", {"merge_id": 3}),
])
def test_a_refused_target_keeps_its_code_and_shows_no_path_or_diagnostic(env, monkeypatch, status, code, details):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.git.credentials import GitServiceError

    def judge(group, branch):
        assert (group["group_id"], group["project_id"]) == (FB, "flowgate")      # the authenticated flow's own
        raise GitServiceError(status, code, "fatal: C:/secret/storage/src", details={"target_branch": branch, **details},
                              diagnostic="fatal: C:/secret/storage/src")

    monkeypatch.setattr(legacy, "judge_merge_target", judge)
    g = _gate_key(env)
    body = setting(env, FB, g, "merge_target_branch", "feature/x", expect=status)
    error = body["error"]
    assert (error["code"], error["params"]["reason"]) == (code, code)
    assert (error["params"]["field"], error["params"]["target_branch"]) == ("merge_target_branch", "feature/x")
    for key in ("incoming_work_base_commits", "merge_id", "connected_group_id", "base_branch"):
        if key in details:
            assert error["params"][key] == details[key]
    assert "secret" not in json.dumps(body) and "route" not in error["params"]
    assert stored_rows(env, FB) == {} and get_settings(env, FB, g)["settings_version"] == 0


def test_a_git_failure_of_the_save_is_git_unavailable_never_invalid_request(env, monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.git.credentials import GitServiceError

    g = _gate_key(env)
    for raised, reason in ((GitServiceError(409, "gate_git_unavailable", "x", details={"reason": "ledger_unavailable"}),
                            "ledger_unavailable"),
                           (GitServiceError(500, "git_error", "fatal: C:/secret"), "git_failed")):
        monkeypatch.setattr(legacy, "judge_merge_target", lambda group, branch, exc=raised: (_ for _ in ()).throw(exc))
        body = setting(env, FB, g, "merge_target_branch", "main", expect=409)
        assert (body["error"]["code"], body["error"]["params"]["reason"]) == ("gate_git_unavailable", reason)
        assert "secret" not in json.dumps(body)
    assert stored_rows(env, FB) == {}


def _git_cmd(args, cwd):
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture()
def git_flow(env, tmp_path, monkeypatch):
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
    bare, seed = tmp_path / "origin.git", tmp_path / "seed"
    _git_cmd(["init", "--bare", "-b", "main", str(bare)], tmp_path)
    _git_cmd(["init", "-b", "main", str(seed)], tmp_path)
    for n in (1, 2):
        (seed / f"f{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        _git_cmd(["add", "-A"], seed)
        _git_cmd(["commit", "-m", f"c{n}"], seed)
    _git_cmd(["remote", "add", "origin", str(bare)], seed)
    _git_cmd(["push", "origin", "main", "main~1:refs/heads/old", "main:refs/heads/remote-only"], seed)
    _git_cmd(["checkout", "-b", "feature/v0.2.0"], seed)
    (seed / "v02.txt").write_text("v02\n", encoding="utf-8")
    _git_cmd(["add", "-A"], seed)
    _git_cmd(["commit", "-m", "v02"], seed)
    _git_cmd(["push", "origin", "feature/v0.2.0"], seed)
    svc.save_config("flowgate", {"repo_url": bare.as_uri(), "provider": "generic", "base_branch": "main",
                                 "default_finalize_action": "merge", "enabled": True,
                                 "author_name": "T", "author_email": "t@t"})
    assert svc.ensure_worktree("flowgate", "v02", FB) == "ok"
    assert svc.ensure_worktree("flowgate", "v02", FA) == "ok"
    base = src_root("flowgate", "main")
    _git_cmd(["branch", "feature/v0.2.0", "origin/feature/v0.2.0"], base)
    _git_cmd(["branch", "old", "origin/old"], base)
    _git_cmd(["branch", "release/local", "main"], base)
    mt.invalidate_check_targets()
    yield {"base": base, "own": db_git.get_state(FB)["branch"], "other": db_git.get_state(FA)["branch"]}
    mt.invalidate_check_targets()
    svc.delete_config("flowgate")


def test_the_merge_target_branch_is_saved_only_for_a_target_the_judgement_allows(env, git_flow):
    from modules.flow_gate.db import groups as db_groups

    work_base_before = db_groups.get_by_id(FB).get("work_base_ref")
    g = _gate_key(env)
    body = get_settings(env, FB, g)
    # only the names the judgement passes: not the groups' own slots, not a remote, not a branch lacking history
    assert body["merge_target_options"] == ["feature/v0.2.0", "main", "release/local"], body["merge_target_state"]
    assert body["default_target_branch"] == "main"
    state = body["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("ok", None, None)
    assert state["current"] == {"branch": "main", "inherited": True, "checked": True, "error": None,
                                "unmerge_supported": True, "warnings": []}
    assert state["default"]["branch"] == "main" and state["default"]["error"] is None
    for name, status, code in (("nope", 404, "merge_target_not_found"),
                               ("remote-only", 409, "merge_target_remote_only"),
                               (git_flow["other"], 409, "merge_target_internal_slot"),
                               (git_flow["own"], 409, "merge_target_same_branch"),
                               ("a..b", 422, "merge_target_invalid"), ("-x", 422, "merge_target_invalid"),
                               ("HEAD", 422, "merge_target_invalid"),
                               ("old", 409, "merge_target_incoming_work_base")):
        refused = setting(env, FB, g, "merge_target_branch", name, expect=status)
        assert refused["error"]["code"] == code, (name, refused["error"])
        assert "\\" not in json.dumps(refused["error"]["params"])                # no worktree path
    assert stored_rows(env, FB, g) == {} and get_settings(env, FB, g)["settings_version"] == 0
    # every candidate is a target the save allows (one judgement), whatever the merge mode
    setting(env, FB, g, "merge_mode", "commit")
    for name in reversed(body["merge_target_options"]):
        saved = setting(env, FB, g, "merge_target_branch", name)
        assert saved["fields"]["merge_target_branch"] == {"value": name, "stored": True, "default": None,
                                                          "choices": None}
        assert (saved["merge_target_state"]["current"]["branch"], saved["merge_target_state"]["current"]["inherited"]) \
            == (name, False)
    picked = get_settings(env, FB, g)["merge_target_state"]["current"]
    assert (picked["error"], picked["unmerge_supported"]) == (None, False)          # a non-base target: no unmerge
    assert picked["warnings"] == ["target_differs_from_work_base", "unmerge_unsupported_for_non_base_target"]
    # saving the target touched neither push nor the remote; the group's work base never moves
    assert {field for (_key, field) in stored_rows(env, FB, g)} == {"merge_mode", "merge_target_branch"}
    assert db_groups.get_by_id(FB).get("work_base_ref") == work_base_before
    # the stored target disappears: its name stays, it is no candidate and the state says why (the refresh read
    # option skips the at most 5 s old cache); the save judges the name again, never from the cache
    for name, status, code in (("release/local", 404, "merge_target_not_found"),
                               ("feature/v0.2.0", 409, "merge_target_remote_only")):   # its origin copy is left
        setting(env, FB, g, "merge_target_branch", name)
        _git_cmd(["branch", "-D", name], git_flow["base"])
        gone = _get_refreshed(env, FB, g)
        assert gone["fields"]["merge_target_branch"]["value"] == name
        assert name not in gone["merge_target_options"] and "main" in gone["merge_target_options"]
        assert gone["merge_target_state"]["current"]["error"] == code
        assert setting(env, FB, g, "merge_target_branch", name, expect=status)["error"]["code"] == code
    cleared = setting(env, FB, g, "merge_target_branch", None)                   # null: back to the work base
    assert cleared["merge_target_state"]["current"]["inherited"] is True
    assert cleared["merge_target_state"]["current"]["branch"] == "main"


def test_a_group_without_its_worktree_yet_lists_and_saves_real_branch_names(env, git_flow):
    """flowgate.v02.0098 T0012: a check card is set up before its group's work starts (no worktree yet). Its candidates
    are the real local branch names judged from where its branch will fork, each saved by the same judgement; an
    internal Graph attempt branch is never one. The confirm and the run still need the group's worktree."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.credentials import GitServiceError

    fc = "flowgate.v02.0093"
    db_groups.create({"group_id": fc, "project_id": "flowgate", "module": "v02", "title": fc, "status": "OPEN"})
    assert db_git.get_state(fc) is None
    _git_cmd(["branch", "graph_attempt_att_x", "main"], git_flow["base"])
    g = put_draft(env, fc, 0, [node("tmp_g", None, "gate")])["key_map"]["tmp_g"]
    body = get_settings(env, fc, g)
    assert body["merge_target_options"] == ["feature/v0.2.0", "main", "release/local"], body["merge_target_state"]
    assert body["default_target_branch"] == "main"
    state = body["merge_target_state"]
    assert (state["status"], state["code"]) == ("ok", None)
    assert (state["current"]["branch"], state["current"]["inherited"], state["current"]["error"]) == ("main", True, None)
    for name in body["merge_target_options"]:
        assert setting(env, fc, g, "merge_target_branch", name)["fields"]["merge_target_branch"]["value"] == name
    for name, code in (("old", "merge_target_incoming_work_base"), (git_flow["own"], "merge_target_internal_slot"),
                       ("graph_attempt_att_x", "merge_target_internal_slot")):
        assert setting(env, fc, g, "merge_target_branch", name, expect=409)["error"]["code"] == code, name
    with pytest.raises(GitServiceError) as strict:
        mt.judge_check_target("flowgate", fc, "main")
    assert (strict.value.code, strict.value.details["reason"]) == ("gate_git_unavailable", "group_slot_missing")
    assert db_git.get_state(fc) is None                                          # nothing provisioned or recorded
