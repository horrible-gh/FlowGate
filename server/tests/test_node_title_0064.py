"""0064 T-B4b — the card (Node) title, server base (0062 D0005 set 1, L0006 2.1 / 2.2 / 2.7 / 2.8 / 4.3, 0064 L0005
2.17-1, P0006 §4.2.1~§4.2.4, DB0007 §2.1 / §3.2 / §4 Q1~Q4 / §5).

On the real SQLite DB, routes and fixtures of ``test_fg_card_settings`` (every migration, the Graph routes, card
documents and card settings wired as ``startup`` does, a Graph run whose AI start is recorded): a person saves a
card's title with ``PUT /flows/{flow_id}/graph/nodes/{node_key}/title``; it is normalized, kept in the card's title
state only (no draft, label, document title, revision or draft version changes), shown first by the Canvas and the
Graph reads (``title_state``, version 0 without a row, null on free documents, slots and v0.1 Flows), told once
after the commit (``graph_canvas_changed`` ``title``), refused with nothing written by every lock the design names
(running card, disposal, ownership, card kind, missing card), and never refused by version.

Verification ids: P0006 V-P-19 ~ 22, V-P-25 (server), 0062 L0006 V-13 / V-16 / V-17 / V-18 (person saves) /
V-24 (server) / V-26 / V-27 (person save), 0062 D0005 set 1, DB0007 §5. The R↔B conversion part of L0005 V-B4b-1
is ``test_node_title_conversion_0064``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

import pytest

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 — its import-time env export happens at collection, not in a test

from test_fg_card_settings import (  # noqa: E402,F401 — fixtures used by name
    AI, FA, FB, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, get_settings, node, placed,
    put_draft, setting,
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
HUMAN = {"kind": "human", "user_id": "u_lee", "provider_id": None}
NONE_STATE = {"title": None, "title_source": None, "version": 0}


# ── helpers ──────────────────────────────────────────────────────────────────

def put_title(env, flow, key, body, expect=200, headers=None):
    res = env["client"].put(f"/api/v1/flows/{flow}/graph/nodes/{key}/title", json=body, headers=headers or _h())
    assert res.status_code == expect, res.text
    return res.json()


def title_rows(env, flow=None):
    sql = "SELECT * FROM graph_node_title_states" + (" WHERE group_id = ?" if flow else "") + " ORDER BY node_key"
    return _sql(env["db"], sql, (flow,) if flow else ())


def title_events(env):
    return [e.payload for e in env["events"]
            if e.event_type == "graph_canvas_changed" and e.payload["reason"] == "title"]


def canvas(env, flow, view=None):
    res = env["client"].get(f"/api/v1/flows/{flow}/canvas" + (f"?view={view}" if view else ""), headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def canvas_node(env, flow, key, view=None):
    return next(n for n in canvas(env, flow, view)["nodes"] if n["node_key"] == key)


def graph(env, flow):
    res = env["client"].get(f"/api/v1/flows/{flow}/graph", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def graph_node(body, part, key):
    return next(n for n in body[part]["nodes"] if n["node_key"] == key)


def unchanged_world(env, flow):
    """Everything a title save must leave alone: the draft, its nodes, revisions, documents and card lines."""
    return {
        "drafts": _sql(env["db"], "SELECT * FROM graph_drafts WHERE group_id = ?", (flow,)),
        "draft_nodes": _sql(env["db"], "SELECT * FROM graph_draft_nodes WHERE group_id = ? ORDER BY node_key", (flow,)),
        "revisions": _sql(env["db"], "SELECT * FROM graph_revisions WHERE group_id = ?", (flow,)),
        "revision_nodes": _sql(env["db"], "SELECT * FROM graph_revision_nodes WHERE group_id = ? ORDER BY node_key",
                               (flow,)),
        "documents": _sql(env["db"], "SELECT * FROM documents WHERE group_id = ? ORDER BY doc_id", (flow,)),
        "lines": _sql(env["db"], "SELECT * FROM graph_node_documents WHERE group_id = ? ORDER BY node_key, ordinal",
                      (flow,)),
    }


def _token_of(env, key):
    return _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]


# ── a person's save (V-P-20, V-P-22, D0005 set 1) ────────────────────────────

def test_a_person_saves_a_normalized_title_and_nothing_else_changes(env):
    key = placed(env, FB, "T")
    before = unchanged_world(env, FB)
    env["events"].clear()

    saved = put_title(env, FB, key, {"title": "  로그인   개선 요구 정리 ", "version": 99})   # a version is ignored

    assert saved["flow_id"] == FB and saved["node_key"] == key
    assert saved["title_state"] == {"title": "로그인 개선 요구 정리", "title_source": "human", "version": 1}
    [row] = title_rows(env, FB)
    assert (row["node_key"], row["title"], row["title_source"], row["human_edited"], row["opportunity_state"],
            row["close_reason"], row["version"], row["updated_by"]) == (
        key, "로그인 개선 요구 정리", "human", 1, "closed", "human_edited", 1, "u_kim")
    assert row["closed_at"] and row["claimed_token_id"] is None
    assert unchanged_world(env, FB) == before                  # draft_version, label, documents, revisions
    assert title_events(env) == [{"flow_id": FB, "reason": "title", "actor_user_id": "u_kim",
                                  "origin_client_id": "tab_1", "draft_version": None, "revision_no": None,
                                  "node_keys": [key]}]
    assert [e.payload["reason"] for e in env["events"] if e.event_type == "graph_canvas_changed"] == ["title"]

    shown = canvas_node(env, FB, key)                           # the Canvas and the Graph read show it
    assert (shown["title"], shown["title_state"], shown["graph_node"]["label"]) == (
        "로그인 개선 요구 정리", saved["title_state"], "T")
    read = graph(env, FB)
    assert read["draft"]["draft_version"] == 1
    assert graph_node(read, "draft", key)["title_state"] == saved["title_state"]
    assert graph_node(read, "draft", key)["label"] == "T"
    assert canvas_node(env, FB, key)["title"] == "로그인 개선 요구 정리"     # a refresh keeps it


def test_the_same_text_a_default_name_and_a_b_a_are_saves_too(env):
    key = placed(env, FB, "T")
    versions = []
    for text in ("A 제목", "A 제목", "작업", "B 제목", "A 제목"):
        body = put_title(env, FB, key, {"title": text})
        assert (body["title_state"]["title"], body["title_state"]["title_source"]) == (text, "human")
        versions.append(body["title_state"]["version"])
    assert versions == [1, 2, 3, 4, 5]
    [row] = title_rows(env, FB)
    assert (row["human_edited"], row["close_reason"], row["version"]) == (1, "human_edited", 5)
    assert len(title_events(env)) == 5
    assert graph(env, FB)["draft"]["draft_version"] == 1


def test_a_custom_card_takes_a_title_and_keeps_its_name(env):
    """V-P-25 (server): a Custom card saves like a document card; its label stays the name it was made with."""
    first = put_draft(env, FB, 0, [{**node("tmp_x", None, "custom"), "label": "외부 승인"}])
    key = first["key_map"]["tmp_x"]
    assert canvas_node(env, FB, key)["title_state"] == NONE_STATE
    before = unchanged_world(env, FB)

    body = put_title(env, FB, key, {"title": "외부 승인 대기 현황"})

    assert body["title_state"] == {"title": "외부 승인 대기 현황", "title_source": "human", "version": 1}
    assert unchanged_world(env, FB) == before
    shown = canvas_node(env, FB, key)
    assert (shown["title"], shown["graph_node"]["label"]) == ("외부 승인 대기 현황", "외부 승인")
    assert graph_node(graph(env, FB), "draft", key)["label"] == "외부 승인"
    assert title_rows(env, FB)[0]["opportunity_state"] == "closed"


def test_two_tabs_the_later_commit_wins_and_the_version_only_grows(env):
    """V-P-20 / L0006 V-18 / V-24 (server part): both saves are 200 — a version never refuses a person."""
    key = placed(env, FB, "T")
    tab2 = {**_h(), "x-canvas-client-id": "tab_2"}
    a = put_title(env, FB, key, {"title": "A 제목", "version": 0})
    b = put_title(env, FB, key, {"title": "B 제목", "version": 0}, headers=tab2)
    assert (a["title_state"]["version"], b["title_state"]["version"]) == (1, 2)
    assert canvas_node(env, FB, key)["title_state"] == b["title_state"]
    assert [p["origin_client_id"] for p in title_events(env)] == ["tab_1", "tab_2"]

    results, errors = [], []

    def save(text):
        try:
            results.append(put_title(env, FB, key, {"title": text})["title_state"])
        except Exception as exc:  # noqa: BLE001 — reported below
            errors.append(exc)

    workers = [threading.Thread(target=save, args=(f"동시 {i}",)) for i in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    assert errors == [] and len(results) == 4
    assert sorted(r["version"] for r in results) == [3, 4, 5, 6]          # one write each, in commit order
    last = max(results, key=lambda r: r["version"])
    [row] = title_rows(env, FB)
    assert (row["title"], row["version"]) == (last["title"], 6)


# ── the state bundle on every read (V-P-19) ──────────────────────────────────

def _read_log(monkeypatch):
    """Each definition / title state read from now on, with the transaction it ran in (None: outside any)."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.graph_workflow.store import definition_store, title_store

    log = []
    for module, name in ((definition_store, "draft"), (definition_store, "latest_revision_no"),
                         (definition_store, "revision"), (title_store, "states")):
        def logged(*args, _real=getattr(module, name), _name=name):
            txn = getattr(conn_mod._tx_local, "txn", None)
            log.append((_name, None if txn is None else id(txn)))
            return _real(*args)

        monkeypatch.setattr(module, name, logged)
    return log


def test_every_graph_read_carries_the_state_and_free_documents_carry_null(env, decided, monkeypatch):
    loose = put_draft(env, FB, 0, [node("tmp_g", None, "gate"), {**node("tmp_x", None, "custom"), "label": "점검"}])
    assert "title_state" not in json.dumps(loose)                         # a draft save never answers it
    g, x = loose["key_map"]["tmp_g"], loose["key_map"]["tmp_x"]
    put_title(env, FB, x, {"title": "분기 점검"})
    fb = {n["node_key"]: n for n in canvas(env, FB)["nodes"]}
    assert fb[g]["title_state"] == NONE_STATE and fb[g]["title"] == "gate"
    assert (fb[x]["title"], fb[x]["title_state"]["version"]) == ("분기 점검", 1)
    assert graph_node(graph(env, FB), "draft", g)["title_state"] == NONE_STATE

    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN)],
                      [edge("tmp_e", "tmp_r", "tmp_t")])
    km = first["key_map"]
    assert "title_state" not in json.dumps(first)
    draft = graph(env, FA)
    assert all(n["title_state"] == NONE_STATE for n in draft["draft"]["nodes"])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    _doc(FA, 50, "M")                                                     # a free document node

    put_title(env, FA, km["tmp_t"], {"title": "로그인 작업"})
    put_title(env, FA, km["tmp_r"], {"title": "루트 요구"})

    log = _read_log(monkeypatch)
    body = canvas(env, FA)
    assert [name for name, _txn in log].count("states") == 1              # one statement per read
    nodes = {n["node_key"]: n for n in body["nodes"]}
    assert nodes[km["tmp_t"]]["title_state"] == {"title": "로그인 작업", "title_source": "human", "version": 1}
    assert nodes[km["tmp_t"]]["title"] == "로그인 작업"
    assert nodes[km["tmp_r"]]["title"] == "루트 요구"                      # over the bound root document's title
    assert nodes[km["tmp_r"]]["graph_node"]["root_bound"] is True
    free = [n for n in body["nodes"] if n["graph_node"] is None]
    assert free and all(n["title_state"] is None for n in free)

    read = graph(env, FA)
    assert read["draft"] is None
    assert graph_node(read, "latest_revision", km["tmp_t"])["title_state"]["title"] == "로그인 작업"
    past = env["client"].get(f"/api/v1/flows/{FA}/graph/revisions/1", headers=_h()).json()
    by_key = {n["node_key"]: n for n in past["revision"]["nodes"]}
    assert by_key[km["tmp_t"]]["title_state"]["title"] == "로그인 작업"   # a past revision shows the current one
    assert by_key[km["tmp_r"]]["title_state"]["version"] == 1


def test_each_read_takes_its_definition_and_title_states_in_one_transaction(env, decided, monkeypatch):
    """V-P-19 / DB0007 Q1: the Canvas, Graph and past revision reads take the draft / revision and the title states
    inside one transaction of their own — not each statement on its own connection."""
    from modules.flow_gate.db import dialect
    from modules.flow_gate.db.connection import get_store, in_transaction
    from modules.flow_gate.graph_workflow.services import flow_guard

    placed(env, FB, "T")
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN)],
                      [edge("tmp_e", "tmp_r", "tmp_t")])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    put_title(env, FA, first["key_map"]["tmp_t"], {"title": "로그인 작업"})
    canvas(env, FA)                                                        # identities recorded beforehand
    canvas(env, FB)
    log = _read_log(monkeypatch)
    reads = [
        ("canvas", FA, lambda: canvas(env, FA)),
        ("graph", FA, lambda: graph(env, FA)),
        ("revision 1", FA, lambda: env["client"].get(f"/api/v1/flows/{FA}/graph/revisions/1", headers=_h()).json()),
        ("canvas", FB, lambda: canvas(env, FB)),
        ("graph", FB, lambda: graph(env, FB)),
    ]
    for name, flow, read in reads:
        log.clear()
        assert read()["flow_id"] == flow, name
        called = [what for what, _txn in log]
        assert called.count("states") == 1 and {"draft", "revision"} & set(called), (name, log)
        assert len({txn for _what, txn in log}) == 1 and log[0][1] is not None, (name, log)

    assert set(flow_guard.READ_SNAPSHOT_STATEMENTS) == {dialect.SQLITE, dialect.POSTGRESQL, dialect.MYSQL}
    seen = []
    with get_store().transaction():                                        # inside a guard / transaction: joined
        flow_guard.with_read_snapshot(lambda: seen.append(in_transaction()))
    assert seen == [True]


@pytest.mark.parametrize("read", ["canvas", "graph"])
def test_a_save_committed_during_a_read_is_not_mixed_into_it(env, monkeypatch, read):
    """V-P-19 / DB0007 Q1, a concurrent change: another tab saves the draft (a new label) and then the card's title
    while a read stands between its draft and its title states. The read answers one moment — before both — never the
    old draft with the new title; the next read shows both."""
    from modules.flow_gate.graph_workflow.store import definition_store

    key = placed(env, FB, "T")
    put_title(env, FB, key, {"title": "처음 제목"})
    canvas(env, FB)
    tab2 = {**_h(), "x-canvas-client-id": "tab_2"}
    errors, armed = [], threading.Event()

    def other_tab():
        try:
            put_draft(env, FB, 1, [{**node(key, "T"), "label": "바뀐 라벨"}])
            put_title(env, FB, key, {"title": "나중 제목"}, headers=tab2)
        except Exception as exc:  # noqa: BLE001 — reported below
            errors.append(exc)

    writer = threading.Thread(target=other_tab)
    real = definition_store.draft

    def draft_then_the_other_tab(flow_id):
        row = real(flow_id)
        if armed.is_set():
            armed.clear()
            writer.start()
            writer.join(2)                         # the read's snapshot holds: the other tab waits or is not seen
        return row

    monkeypatch.setattr(definition_store, "draft", draft_then_the_other_tab)
    armed.set()
    if read == "canvas":
        body = canvas(env, FB)
        shown = next(n for n in body["nodes"] if n["node_key"] == key)
        seen = (body["view"]["draft_version"], shown["graph_node"]["label"], shown["title_state"]["title"],
                shown["title_state"]["version"], shown["title"])
    else:
        body = graph(env, FB)
        shown = graph_node(body, "draft", key)
        seen = (body["draft"]["draft_version"], shown["label"], shown["title_state"]["title"],
                shown["title_state"]["version"], shown["title_state"]["title"])
    writer.join(60)
    assert errors == [] and not writer.is_alive() and not armed.is_set()
    assert seen == (1, "T", "처음 제목", 1, "처음 제목")
    after = canvas_node(env, FB, key)
    assert (after["graph_node"]["label"], after["title"], after["title_state"]["version"]) == (
        "바뀐 라벨", "나중 제목", 2)
    assert graph(env, FB)["draft"]["draft_version"] == 2


def test_a_v01_flow_carries_null(env, monkeypatch):
    from modules.flow_gate.graph_workflow.services import engine_router, graph_service

    key = placed(env, FB, "T")
    real = engine_router.read_ownership

    def v01(flow_id):
        own = real(flow_id)
        return engine_router.Ownership("v01", {"doc_id": f"{FB}.0001-R"}, own.assignment)

    monkeypatch.setattr(graph_service, "read_ownership", v01)
    assert graph_node(graph(env, FB), "draft", key)["title_state"] is None


# ── refusals: nothing written, nothing sent (V-P-21, L0006 4.3) ──────────────

def test_text_refusals_and_the_thirty_character_limit(env):
    key = placed(env, FB, "T")
    env["events"].clear()
    thirty = "가" * 30
    refusals = [
        ({"title": "   "}, 422, "title_empty", {}),
        ({"title": "\t​\n"}, 422, "title_empty", {}),
        ({"title": "나" * 33}, 422, "title_too_long", {"max": 30, "length": 33}),
        ({"title": thirty + "a"}, 422, "title_too_long", {"max": 30, "length": 31}),
        ({"title": "다" * 5 + " " * 196}, 422, "title_too_long", {"max": 30, "length": 201}),   # raw over 200
        ({}, 422, "invalid_request", {"errors": [{"field": "title", "reason": "required"}]}),
        ({"title": 123}, 422, "invalid_request", {"errors": [{"field": "title", "reason": "not_a_string"}]}),
        ([], 422, "invalid_request", {"errors": [{"field": "body", "reason": "not_an_object"}]}),
    ]
    for body, status, code, params in refusals:
        err = put_title(env, FB, key, body, expect=status)["error"]
        assert (err["code"], err["params"]) == (code, params), body
    assert put_title(env, FB, key, {"title": "가" * 5 + " " * 195})["title_state"]["title"] == "가" * 5
    assert title_rows(env, FB)[0]["version"] == 1 and len(title_events(env)) == 1
    assert put_title(env, FB, key, {"title": thirty})["title_state"]["title"] == thirty
    nfd = unicodedata.normalize("NFD", "한글 제목")
    assert put_title(env, FB, key, {"title": nfd})["title_state"]["title"] == unicodedata.normalize("NFC", nfd)


def test_card_ownership_kind_and_flow_refusals_change_nothing(env, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    from modules.flow_gate.graph_workflow.services import engine_router, node_title
    from modules.flow_gate.graph_workflow.store import title_store

    key = placed(env, FB, "T")
    put_title(env, FB, key, {"title": "처음 제목"})
    _doc(FB, 40, "M")
    free_key = next(n["node_key"] for n in canvas(env, FB)["nodes"] if n["graph_node"] is None)
    before = title_rows(env)
    env["events"].clear()

    def refuse(flow, k, status, code, params=None):
        err = put_title(env, flow, k, {"title": "바꾼 제목"}, expect=status)["error"]
        assert err["code"] == code, err
        if params is not None:
            assert err["params"] == params
        assert err["message"]

    refuse(FB, "gn_nothing", 404, "node_not_found", {"node_key": "gn_nothing"})
    refuse(FB, free_key, 422, "node_title_not_supported", {"node_key": free_key})
    refuse(FA, "gn_x", 409, "node_title_not_editable")                   # unassigned, no draft
    refuse("flowgate.v02.0999", key, 404, "flow_not_found")
    real = engine_router.read_ownership
    for state in ("v01", "conflict"):
        monkeypatch.setattr(node_title, "read_ownership",
                            lambda flow, s=state: engine_router.Ownership(s, {"doc_id": "x"}, real(flow).assignment))
        refuse(FB, key, 409, "node_title_not_editable", {"node_key": key})
    monkeypatch.setattr(node_title, "read_ownership", real)
    with monkeypatch.context() as m:
        m.setattr(title_store, "available", lambda: False)
        refuse(FB, key, 409, "feature_not_enabled")

    worker = {"Authorization": "Bearer worker:1", "x-canvas-client-id": "tab_1"}
    monkeypatch.setattr(envelope, "verify_bearer", lambda request: (
        {"_is_user_jwt": False, "token_id": "t"} if "worker" in request.headers.get("Authorization", "")
        else {"_is_user_jwt": True, "issued_to": "u_kim"}))
    assert put_title(env, FB, key, {"title": "토큰"}, expect=403, headers=worker)["error"]["code"] == \
        "token_kind_not_allowed"
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: perm != envelope.WRITE_PERMISSION)
    refuse(FB, key, 403, "flow_access_denied")

    assert title_rows(env) == before and title_events(env) == []


def test_a_card_only_a_run_s_pinned_revision_draws_is_not_found(env, decided, monkeypatch):
    """L0006 2.7 step 2 / 5, P0006 §4.2.3: a run holds revision 1; a card it has not run yet is dropped and revision 2
    confirmed. The Canvas still draws the card (the run's pinned revision), but it is in neither the draft nor the
    latest revision: 404 ``node_not_found``, not the free document / slot refusal; the state, version and SSE stay."""
    from modules.flow_gate.graph_workflow.services import canvas_service
    from modules.flow_gate.graph_workflow.services import run_state_ports as rsp

    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN),
                                   node("tmp_p", "T", assignee=HUMAN)],
                      [edge("tmp_e", "tmp_r", "tmp_t"), edge("tmp_f", "tmp_t", "tmp_p")])
    km = first["key_map"]
    pending = km["tmp_p"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    put_title(env, FA, pending, {"title": "고정 개정 카드"})
    _start(env)                                                            # the run pins revision 1
    # 0061 lets a run's not yet run card go; the run engine's own edit rules are not what this test is about.
    monkeypatch.setitem(rsp._handlers, rsp.REQUIRE_EDITABLE, lambda flow: None)
    monkeypatch.setitem(rsp._handlers, rsp.PROTECT_DEFINITION, lambda flow, nodes, edges: None)
    put_draft(env, FA, 0, [node(km["tmp_r"], "R"), node(km["tmp_t"], "T", assignee=HUMAN)],
              [edge("tmp_e2", km["tmp_r"], km["tmp_t"])])
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions",
                                   json={"draft_version": 1, "base_revision_no": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    assert _sql(env["db"], "SELECT revision_no FROM graph_runs WHERE active_group_id = ?", (FA,)) == [{"revision_no": 1}]
    assert pending in canvas_service.current_node_keys(FA)                # still drawn: the pinned revision
    assert pending not in canvas_service.free_node_keys(FA)
    shown = canvas_node(env, FA, pending)
    assert shown["graph_node"] is not None and shown["title_state"]["title"] == "고정 개정 카드"
    before = title_rows(env)
    env["events"].clear()

    err = put_title(env, FA, pending, {"title": "바꾼 제목"}, expect=404)["error"]

    assert (err["code"], err["params"], err["message"]) == ("node_not_found", {"node_key": pending}, "카드를 찾을 수 없습니다.")
    assert title_rows(env) == before and title_events(env) == []
    assert before[0]["version"] == 1
    _doc(FA, 60, "M")                                                      # a real free document stays 422
    free_key = next(n["node_key"] for n in canvas(env, FA)["nodes"] if n["graph_node"] is None)
    assert free_key in canvas_service.free_node_keys(FA)
    assert put_title(env, FA, free_key, {"title": "바꾼 제목"}, expect=422)["error"]["code"] == \
        "node_title_not_supported"
    assert title_rows(env) == before and title_events(env) == []


def test_messages_of_the_new_codes_in_three_languages():
    from modules.flow_gate.graph_workflow.api import envelope

    expected = {
        "title_empty": ("제목을 입력하세요.", "Enter a title.", "タイトルを入力してください。"),
        "title_too_long": ("제목은 30자 이하여야 합니다.", "A title must be 30 characters or fewer.",
                           "タイトルは30文字以内にしてください。"),
        "node_not_found": ("카드를 찾을 수 없습니다.", "Card not found.", "カードが見つかりません。"),
        "node_title_not_supported": ("이 카드는 제목을 따로 정할 수 없습니다.", "This card cannot have a separate title.",
                                     "このカードはタイトルを個別に設定できません。"),
        "node_title_not_editable": ("지금은 이 카드의 제목을 바꿀 수 없습니다.",
                                    "The title of this card cannot be changed now.",
                                    "現在このカードのタイトルは変更できません。"),
        "node_title_locked": ("실행 중인 카드의 제목은 바꿀 수 없습니다.", "The title of a running card cannot be changed.",
                              "実行中のカードのタイトルは変更できません。"),
    }
    for code, (ko, en, ja) in expected.items():
        assert envelope._MESSAGES[code] == {"ko": ko, "en": en, "ja": ja}, code


def test_a_disposal_after_the_pre_check_is_judged_again_in_the_guard(env, monkeypatch):
    """L0006 V-26: the pre-check passed, the DC discard committed, then the guard: 409, nothing changes."""
    from modules.flow_gate.graph_workflow.services import node_title

    key = placed(env, FB, "T")
    put_title(env, FB, key, {"title": "처분 전"})
    before = title_rows(env, FB)
    env["events"].clear()
    real = node_title.require_writable

    def pre_check_then_dispose(flow_id):
        real(flow_id)
        _doc(FB, 77, "DC")                                               # committed before the guard

    monkeypatch.setattr(node_title, "require_writable", pre_check_then_dispose)
    err = put_title(env, FB, key, {"title": "처분 직후"}, expect=409)["error"]
    assert (err["code"], err["params"], err["message"]) == ("flow_disposed", {"flow_id": FB},
                                                           "폐기된 Flow는 변경할 수 없습니다.")
    assert title_rows(env, FB) == before and title_events(env) == []
    monkeypatch.setattr(node_title, "require_writable", real)
    assert put_title(env, FB, key, {"title": "또"}, expect=409)["error"]["code"] == "flow_disposed"   # pre-check


def test_a_failing_disposal_read_fails_the_save_instead_of_opening_it(env, monkeypatch):
    """L0006 V-27 (person save): the guard's disposal read does not fail open."""
    from modules.flow_gate.graph_workflow.services import node_title

    key = placed(env, FB, "T")
    before = title_rows(env, FB)

    class Broken:
        def _fetch_one(self, *_a, **_k):
            raise RuntimeError("documents unreadable")

    monkeypatch.setattr(node_title, "get_store", lambda: Broken())
    assert put_title(env, FB, key, {"title": "열리지 않음"}, expect=500)["error"]["code"] == "internal_error"
    assert title_rows(env, FB) == before and title_events(env) == []


# ── the running card lock (L0006 V-16, D0005 set 1) ─────────────────────────

def test_a_running_card_refuses_its_title_and_a_done_card_takes_one(env, decided):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    from flowgate.card_settings import legacy_adapter

    key = _run_one_card(env, HUMAN)
    opened = put_title(env, FA, key, {"title": "입력 연 제목"})            # the edit was opened before the start
    _start(env)
    assert legacy_adapter.node_running(FA, key) is True
    env["events"].clear()
    err = put_title(env, FA, key, {"title": "실행 중 저장"}, expect=409)["error"]
    assert (err["code"], err["params"]) == ("node_title_locked", {"node_key": key})
    [row] = title_rows(env, FA)
    assert (row["title"], row["version"]) == ("입력 연 제목", opened["title_state"]["version"])
    assert title_events(env) == []
    assert canvas_node(env, FA, key)["title"] == "입력 연 제목"
    # the card settings keep their own lock (no regression: still shown locked and still refused)
    assert get_settings(env, FA, key)["locked"] is True
    setting(env, FA, key, "restart", 1, expect=409)

    result = _doc(FA, 90, "T")
    v01_contact.after_inbox_registered(FA, result, "T", _token_of(env, key))
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (key,))[0]["status"] == "done"
    done = put_title(env, FA, key, {"title": "끝난 카드"})               # a revision-only card of a Graph Flow
    assert done["title_state"]["version"] == opened["title_state"]["version"] + 1
    shown = canvas_node(env, FA, key)                                      # the result is bound; the title stays
    assert shown["document"]["doc_id"] == result and shown["title"] == "끝난 카드"
    assert canvas_node(env, FA, key)["title"] == "끝난 카드"


def test_a_card_whose_ai_review_works_is_locked_until_it_ends(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.api import v01_contact

    from flowgate.card_settings import legacy_adapter

    key = _run_one_card(env, HUMAN)
    _start(env)
    result = _doc(FA, 91, "T")
    v01_contact.after_inbox_registered(FA, result, "T", _token_of(env, key))
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (key,))[0]["status"] == "review"
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: doc_id == result)
    assert put_title(env, FA, key, {"title": "검수 중"}, expect=409)["error"]["code"] == "node_title_locked"
    assert title_rows(env, FA) == []
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: False)
    assert put_title(env, FA, key, {"title": "검수 대기"})["title_state"]["title"] == "검수 대기"


def test_without_the_card_settings_port_no_card_is_running():
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    saved = ports._handler(ports.NODE_RUNNING)
    ports.unregister(ports.NODE_RUNNING)
    try:
        assert ports.node_running("flowgate.v02.0091", "gn_any") is False
        ports.register(ports.NODE_RUNNING, lambda flow, key: key == "gn_busy")
        assert (ports.node_running("f", "gn_busy"), ports.node_running("f", "gn_idle")) == (True, False)
    finally:
        ports.unregister(ports.NODE_RUNNING)
        if saved is not None:
            ports.register(ports.NODE_RUNNING, saved)


def test_startup_wires_the_card_settings_lock_into_the_title_port(env):
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    from flowgate.card_settings import legacy_adapter

    assert ports._handler(ports.NODE_RUNNING) is legacy_adapter.node_running


# ── text rules and default names (L0006 2.1 / 2.2 / V-13 / V-17, L0005 2.17-1) ─

def test_normalization_and_validation():
    from modules.flow_gate.graph_workflow.services import node_title_names as n

    assert n.normalize_title(" a\t\tb\n​c﻿  　d  ") == "a b c d"
    assert n.normalize_title(unicodedata.normalize("NFD", "가각")) == "가각"
    assert n.validate_title("  " + "글" * 30 + " ", 200) == n.TitleCheck("글" * 30, None, 30)
    assert n.validate_title("글" * 31, 200) == n.TitleCheck(None, "too_long", 31)
    assert n.validate_title(" ​ ", 200) == n.TitleCheck(None, "empty", 0)
    assert n.validate_title(None, 200).reason == "not_text" and n.validate_title(5, 200).reason == "not_text"
    assert n.validate_title("x" * 201, 200) == n.TitleCheck(None, "too_long", 201)
    assert (n.TITLE_MAX_CHARS, n.TITLE_MIN_CHARS, n.HUMAN_RAW_MAX_CHARS, n.CANDIDATE_RAW_MAX_CHARS) == (30, 1, 200, 200)


def test_default_names_of_each_card():
    from modules.flow_gate.graph_workflow.services import node_title_names as n

    def doc(doc_type, palette=None):
        return {"node_key": "k", "node_type": "document", "doc_type": doc_type, "palette_item_id": palette, "label": ""}

    assert n.default_names(doc("T", "task")) == {"작업", "作業", "task"}
    assert n.default_names(doc("N")) == {"조사", "調査", "research"}
    assert n.default_names(doc("D")) >= {"기본 설계", "基本設計", "basic design", "설계", "設計", "design"}
    assert n.default_names(doc("B", "bug")) == {"버그", "バグ", "bug", "요구사항", "要件", "requirement"}
    assert n.default_names(doc("R", "requirement")) == {"요구사항", "要件", "requirement"}
    assert n.default_names({"node_key": "k", "node_type": "custom", "doc_type": None,
                            "palette_item_id": "custom.1", "label": "x"}) == set()
    assert n.is_default_name("  Task ", doc("T")) and n.is_default_name("ＢＵＧ".lower(), doc("B")) is False
    assert n.is_default_name("버그", doc("B")) and n.is_default_name("Bug", doc("B"))
    assert not n.is_default_name("작업 2", doc("T")) and not n.is_default_name("", doc("T"))
    assert not n.is_default_name("버그", {"node_key": "k", "node_type": "custom", "doc_type": "B",
                                        "palette_item_id": None, "label": ""})


def _ts_block(text, name):
    found = re.search(name + r":\s*\{([^}]*)\}", text)
    assert found, name
    return dict(re.findall(r"(\w+):\s*'([^']*)'", found.group(1)))


def test_the_default_name_catalog_matches_the_client_i18n():
    from modules.flow_gate.graph_workflow.services import node_title_names as n

    i18n = _SERVER_DIR.parent / "client_gui" / "src" / "app" / "i18n"
    for locale in n.DEFAULT_NAME_LOCALES:
        text = (i18n / f"{locale}.ts").read_text(encoding="utf-8")
        palette = text[text.index("palette: {"):]
        items = _ts_block(palette, "items")
        kinds = _ts_block(text, "node_kinds")
        assert {k: v[locale] for k, v in n.PALETTE_NAMES.items()} == {k: v for k, v in items.items() if k != "confirm"}
        assert {k: v[locale] for k, v in n.NODE_KIND_NAMES.items()} == kinds


# ── the table (DB0007 §2.1 / §3.2 / §5) ──────────────────────────────────────

_COLUMNS = ["group_id", "node_key", "title", "title_source", "human_edited", "opportunity_state", "close_reason",
            "claimed_token_id", "claimed_ai_run_id", "claimed_at", "closed_at", "version", "created_at", "updated_at",
            "updated_by"]


def test_the_migration_is_the_same_table_in_three_dialects():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_MIGRATIONS / dialect / "155_graph_node_title_states.sql").read_text(encoding="utf-8")
        assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql) == ["graph_node_title_states"], dialect
        body = sql[sql.index("("):]
        assert [c for c in _COLUMNS if re.search(rf"^\s+{c}\s", body, re.M)] == _COLUMNS, dialect
        for name in ("ck_gnts_title_len", "ck_gnts_source", "ck_gnts_opportunity"):
            assert f"CONSTRAINT {name} CHECK" in sql, (dialect, name)
        code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        assert "PRIMARY KEY (group_id, node_key)" in code and "ON DELETE CASCADE" in code
        assert code.count("REFERENCES") == 1 and "REFERENCES groups(group_id)" in code   # node_key has no FK
        assert ("CREATE TRIGGER IF NOT EXISTS trg_graph_node_title_states_group_deleted" in code) == \
            (dialect == "sqlite"), dialect
        assert not re.search(r"\b(ALTER|DROP|UPDATE)\b", code), dialect                  # additive only
        assert "close_reason IN" not in code                               # an open key


def _insert(db, **values):
    row = {"group_id": FB, "node_key": "gn_a", "title": None, "title_source": "none", "human_edited": 0,
           "opportunity_state": "unused", "close_reason": None, "claimed_token_id": None, "closed_at": None,
           "version": 1, "created_at": "t", "updated_at": "t", **values}
    cols = ", ".join(row)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"INSERT INTO graph_node_title_states ({cols}) VALUES ({', '.join('?' * len(row))})",
                     list(row.values()))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("values", [
    {"title": "가" * 31, "title_source": "human", "human_edited": 1, "opportunity_state": "closed",
     "close_reason": "human_edited", "closed_at": "t"},
    {"title": "", "title_source": "human", "human_edited": 1, "opportunity_state": "closed",
     "close_reason": "human_edited", "closed_at": "t"},
    {"title": "제목"},                                                      # a title with source none
    {"title_source": "ai"},                                                # ai without a title
    {"title": "제목", "title_source": "human", "human_edited": 0},         # human without the mark
    {"title": "제목", "title_source": "ai", "human_edited": 1},            # ai with the mark
    {"title": "제목", "title_source": "robot"},
    {"opportunity_state": "closed", "closed_at": "t"},                     # closed without a reason
    {"opportunity_state": "closed", "close_reason": "applied"},            # closed without a time
    {"opportunity_state": "claimed"},                                      # claimed without a token
    {"opportunity_state": "claimed", "claimed_token_id": "tok", "close_reason": "applied"},
    {"close_reason": "applied"},                                           # unused with a reason
    {"claimed_token_id": "tok"},                                           # unused with a token
    {"opportunity_state": "spent"},
    {"human_edited": 2},
    {"version": 0},
    {"group_id": "flowgate.v02.0999"},                                     # no such Flow (FK)
])
def test_the_checks_refuse_contradicting_rows(env, values):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(env["db"], **values)
    assert title_rows(env) == []


def test_valid_rows_the_primary_key_the_open_reason_and_the_flow_cleanup(env):
    _insert(env["db"], title="가" * 30, title_source="human", human_edited=1, opportunity_state="closed",
            close_reason="human_edited", closed_at="t")
    _insert(env["db"], node_key="gn_b", opportunity_state="claimed", claimed_token_id="tok")
    _insert(env["db"], node_key="gn_c", opportunity_state="closed", close_reason="a_reason_added_later",
            closed_at="t", claimed_token_id="tok")
    _insert(env["db"], node_key="gn_d", title="AI 제목", title_source="ai")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(env["db"])                                                 # (group_id, node_key) once
    _insert(env["db"], group_id=FA)
    assert len(title_rows(env, FB)) == 4

    conn = sqlite3.connect(env["db"])
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FB,))     # the FK cascade
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM documents WHERE group_id = ?", (FA,))
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FA,))     # a hard delete: the trigger
        conn.commit()
    finally:
        conn.close()
    assert title_rows(env) == []


def test_the_store_writes_follow_the_state_rules(env):
    """DB0007 §5 / L0006 3.2 on the store writes the AI naming set will call (conditional, version + 1)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import title_store as ts

    def write(call):
        with get_store().transaction():
            return call()

    def row(key):
        return ts.state(FB, key)

    assert ts.available() and ts.states(FB) == {} and ts.state(FB, "gn_a") is None
    assert write(lambda: ts.claim(FB, "gn_a", "tok_1", "air_1", None)) == 1                 # U -> C, version 1
    assert (row("gn_a")["opportunity_state"], row("gn_a")["version"], row("gn_a")["claimed_ai_run_id"]) == (
        "claimed", 1, "air_1")
    assert write(lambda: ts.claim(FB, "gn_a", "tok_2", None, 1)) == 0                       # not unused any more
    assert write(lambda: ts.apply_ai(FB, "gn_a", "AI 제목", "tok_other", 1)) == 0           # not the claim's token
    assert write(lambda: ts.apply_ai(FB, "gn_a", "AI 제목", "tok_1", 0)) == 0               # a stale version
    assert write(lambda: ts.apply_ai(FB, "gn_a", "AI 제목", "tok_1", 1)) == 1
    a = row("gn_a")
    assert (a["title"], a["title_source"], a["opportunity_state"], a["close_reason"], a["version"]) == (
        "AI 제목", "ai", "closed", "applied", 2)
    assert write(lambda: ts.close(FB, "gn_a", "no_candidate", 2)) == 0                      # X stays X
    write(lambda: ts.save_human(FB, "gn_a", "사람 제목", "u_kim"))                          # X -> X, reason kept
    a = row("gn_a")
    assert (a["title"], a["title_source"], a["human_edited"], a["close_reason"], a["version"], a["claimed_token_id"]
            ) == ("사람 제목", "human", 1, "applied", 3, "tok_1")
    assert write(lambda: ts.apply_ai(FB, "gn_a", "다시", "tok_1", 3)) == 0                  # human -> ai never

    write(lambda: ts.claim(FB, "gn_b", "tok_b", None, None))
    write(lambda: ts.save_human(FB, "gn_b", "먼저 저장", None))                              # C -> X(human_edited)
    b = row("gn_b")
    assert (b["opportunity_state"], b["close_reason"], b["claimed_token_id"], b["version"]) == (
        "closed", "human_edited", "tok_b", 2)
    assert write(lambda: ts.close(FB, "gn_c", "not_target_display", None)) == 1             # U -> X without a row
    assert (row("gn_c")["title_source"], row("gn_c")["close_reason"], row("gn_c")["version"]) == (
        "none", "not_target_display", 1)
    with pytest.raises(ValueError):
        ts.close(FB, "gn_c", "made_up", 1)
    assert ts.states(FB) == {"gn_a": {"title": "사람 제목", "title_source": "human", "version": 3},
                             "gn_b": {"title": "먼저 저장", "title_source": "human", "version": 2},
                             "gn_c": {"title": None, "title_source": "none", "version": 1}}
    assert len(ts.CLOSE_REASONS) == 13


def test_a_row_closed_without_a_title_reads_as_no_source(env):
    """``title_state.title_source`` is "human" / "ai" / null: a closed row with source none reads as null."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import title_store as ts

    key = placed(env, FB, "T")
    with get_store().transaction():
        ts.close(FB, key, "not_target_display", None)
    shown = canvas_node(env, FB, key)
    assert (shown["title"], shown["title_state"]) == ("T", {"title": None, "title_source": None, "version": 1})
