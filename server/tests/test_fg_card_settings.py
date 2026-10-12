"""flowgate.v02.0053 T#2 — card (Node) run settings: stored on the card, read and changed one value at a time.

The feature lives in the v0.2 package ``flowgate.card_settings`` (D0006 §2.1); the Graph workflow reaches it
only through ``card_settings_ports`` (node dispatch), registered here as ``startup`` does. Flat ``test_fg_``
placement (D0006 §2.4).

On a real SQLite DB (all migrations, TestClient over the Graph routes and the card settings routes): a card
placed in a draft and a card whose run is done both read and write their settings; every value is saved by
its own PATCH; the items the v0.1 sequence row never stored (instruction use / omit, after review, restart,
time limit, review basis) are stored; values outside a setting are refused; a running card is locked; and an
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FA = "flowgate.v02.0091"      # unassigned, live root R (approved)
FB = "flowgate.v02.0092"      # unassigned, no documents
TEST_VALUE, TEST_VERSION = "card_settings_test", "1"
RUN_FIELDS = ["instruction", "after_review", "run_ai", "restart", "review_ai", "review_count", "time_limit",
              "review_basis"]


@pytest.fixture()
def db_path():
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
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
    from modules.flow_gate.services import token_service

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
    monkeypatch.setattr(token_service, "_scratch_dir", lambda project, token_id: tmp_path / "work" / token_id)
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "settingstest")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_settingstest", "card-settings-test-pepper")
    old_starter = node_dispatch.set_ai_starter(lambda job: state["jobs"].append(job))
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
    assert f.card_kind("document", "WP") is None and f.card_kind("document", "TSR") is None
    names = {kind: [x.name for x in f.fields_of(kind)] for kind in f.CARD_KINDS}
    assert names == {
        f.RUN: RUN_FIELDS,
        f.TEST: ["instruction", "after_review", "run_ai", "review_ai", "review_count"],
        f.REQUIREMENT: ["delegation", "scope", "team", "limit_max_nodes", "limit_total_reviews",
                        "limit_retries_per_card", "limit_replans"],
        f.CHECK: ["usage", "merge_mode", "merge_target", "push", "keep_branch"],
    }


def test_the_run_plan_hands_on_only_what_the_card_stores():
    from flowgate.card_settings import fields as f

    assert f.run_plan(f.RUN, {}) == {"settings": {}}
    plan = f.run_plan(f.RUN, {"run_ai": "aip_card", "time_limit": 120, "restart": 0, "after_review": "follow"})
    assert plan == {"settings": {"run_ai": "aip_card", "time_limit": 120, "restart": 0, "after_review": "follow"},
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
    assert shown(body) == {"instruction": "use", "after_review": "human", "run_ai": None, "restart": 0,
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
    for field, value in (("instruction", "omit"), ("after_review", "follow"), ("restart", 1),
                         ("time_limit", 120), ("review_basis", "last")):
        setting(env, FB, key, field, value)
    body = get_settings(env, FB, key)
    assert {k: v for k, v in shown(body).items() if body["fields"][k]["stored"]} == {
        "instruction": "omit", "after_review": "follow", "restart": 1, "time_limit": 120, "review_basis": "last"}
    assert stored_rows(env, FB, key) == {(key, "after_review"): '"follow"', (key, "instruction"): '"omit"',
                                         (key, "restart"): "1", (key, "review_basis"): '"last"',
                                         (key, "time_limit"): "120"}


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
    for field, value in (("usage", "end"), ("merge_mode", "merge"), ("merge_target", "release/v0.2"),
                         ("push", True), ("keep_branch", False)):
        gate = setting(env, FB, km["tmp_g"], field, value)
    assert gate["card_kind"] == "check" and shown(gate) == {
        "usage": "end", "merge_mode": "merge", "merge_target": "release/v0.2", "push": True, "keep_branch": False}
    plan = get_settings(env, FB, km["tmp_wp"])
    assert (plan["card_kind"], plan["fields"]) == (None, {})
    refused = setting(env, FB, km["tmp_wp"], "restart", 1, expect=422)
    assert refused["error"]["params"]["errors"][0]["reason"] == "not_a_setting_of_this_card"
    assert get_settings(env, FB, km["tmp_x"])["card_kind"] == "run"       # a palette [+] card: like work


@pytest.mark.parametrize("field, value, reason", [
    ("restart", 99, "not_a_choice"), ("restart", "1", "not_a_choice"), ("restart", True, "not_a_choice"),
    ("review_count", -2, "not_a_choice"), ("time_limit", 61, "not_a_choice"), ("time_limit", 60.0, "not_a_choice"),
    ("instruction", "skip", "not_a_choice"), ("after_review", None, None), ("run_ai", "", "not_a_text"),
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
    for bad in ("a..b", "-x", "main.lock", "x/", "has space"):
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
    for field, value in (("run_ai", "aip_card"), ("time_limit", 120), ("restart", 2), ("after_review", "follow"),
                         ("review_basis", "last"), ("instruction", "omit")):
        setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["step_timeout_sec"], job["restart_max"]) == (
        key, "aip_card", 7200, 2)
    assert job["card_settings"] == {"instruction": "omit", "after_review": "follow", "run_ai": "aip_card",
                                    "restart": 2, "time_limit": 120, "review_basis": "last"}
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
    assert callers == ["services/node_dispatch.py", "services/result_binder.py"]
    tree = ast.parse((graph / "services" / "card_settings_ports.py").read_text(encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "threading", "typing"}
