"""flowgate.v02.0053 T#1 — card documents: placing a card creates its documents and binds them.

The feature lives in the v0.2 package ``flowgate.card_documents`` (D0006 §2.1); the Graph workflow
reaches it only through ``card_document_ports``, registered here as ``startup`` does. Flat
``test_fg_`` placement (D0006 §2.4).

On a real SQLite DB (all migrations, TestClient over the Graph routes): a draft save creates each
card's document lines (요구사항 · 작업계획/전개 · 지시서/레포트 · 테스트 지시서/시나리오/레포트), a re-save
keeps them, a removed draft-only card takes its draft documents with it, a node of a confirmed
revision keeps them (also when the draft retypes the node), and a run result bound to a node takes
the line of its type. The cards of one save see the root an earlier card made or removed, whatever
the order of the request's nodes, and the R↔B conversion of a card's root (the real v0.1 documents
route) moves the card's line with it. Auth, RBAC and SSE delivery are stubbed; document files go to
a temp storage root.
"""
from __future__ import annotations

import json
import os
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

FA = "flowgate.v02.0081"      # unassigned, live root R (approved)
FB = "flowgate.v02.0082"      # unassigned, no documents
TEST_VALUE, TEST_VERSION = "card_docs_test", "1"


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
    from modules.flow_gate.services import token_service

    from flowgate.card_documents import integration

    storage = tmp_path / "storage"
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage))
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    state = {"events": [], "db": db_path, "storage": storage}

    def fake_verify(request):
        raw = request.headers.get("Authorization", "")
        if raw.startswith("Bearer user:"):
            return {"_is_user_jwt": True, "issued_to": raw[len("Bearer user:"):]}
        return JSONResponse(status_code=401, content={"ok": False})

    monkeypatch.setattr(envelope, "verify_bearer", fake_verify)
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: True)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(run_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    # Use the real token scratch layout inside the fixture storage root.
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "cardtest")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_cardtest", "card-test-pepper")
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
    old_starter = node_dispatch.set_ai_starter(None)
    integration.register()
    app = FastAPI()
    for module in (canvas_routes, graph_routes, run_routes):
        app.include_router(module.router)
    state["client"] = TestClient(app)
    _seed(db_path)
    yield state
    integration.unregister()
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


def _doc(flow, seq, typ, review=None, status="open", **extra):
    from modules.flow_gate.db import documents as db_documents

    doc_id = f"{flow}.{seq:04d}-{typ}"
    db_documents.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": flow,
                         "type_code": typ, "seq": seq, "title": f"{typ} {seq}", "status": status, **extra})
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
    for gid in (FA, FB):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
    _doc(FA, 1, "R", "approved")


def _h():
    return {"Authorization": "Bearer user:u_kim", "x-canvas-client-id": "tab_1"}


def node(ref, doc_type="D", label=None, node_type="document", palette=None, assignee=None):
    key = {"node_key": None, "client_key": ref} if ref.startswith("tmp_") else {"node_key": ref, "client_key": None}
    return {**key, "node_type": node_type, "doc_type": doc_type if node_type == "document" else None,
            "palette_item_id": palette, "label": label or (doc_type or node_type), "assignee": assignee}


def edge(ref, source, target):
    key = {"edge_id": None, "client_key": ref} if ref.startswith("tmp_") else {"edge_id": ref, "client_key": None}
    return {**key, "source": source, "target": target, "condition": None}


def put_draft(env, flow, version, nodes, edges=()):
    res = env["client"].put(f"/api/v1/flows/{flow}/graph/draft",
                            json={"draft_version": version, "nodes": nodes, "edges": list(edges)}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def canvas(env, flow, query=""):
    res = env["client"].get(f"/api/v1/flows/{flow}/canvas{query}", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def lines(body, key):
    return [(line["slot"], line["doc_type"]) for line in body["card_documents"].get(key, [])]


def doc(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def line_rows(env, flow, key=None):
    sql = "SELECT node_key, slot, doc_type, doc_id, owned FROM graph_node_documents WHERE group_id = ?"
    params = [flow]
    if key:
        sql += " AND node_key = ?"
        params.append(key)
    return _sql(env["db"], sql + " ORDER BY node_key, ordinal", params)


def doc_events(env, kind):
    return _sql(env["db"], "SELECT * FROM workflow_events WHERE event_type = ?", (kind,))


ALL_CARDS = [node("tmp_r", "R", "요구"), node("tmp_wp", "WP", "계획"), node("tmp_d", "D", "기본설계"),
             node("tmp_p", "P", "프로토콜"), node("tmp_n", "N", "조사"), node("tmp_t", "T", "구현"),
             node("tmp_ts", "TS", "시험"), node("tmp_c", None, "사용자 카드", node_type="custom", palette="custom_x"),
             node("tmp_g", None, "확인", node_type="gate")]


# ── domain: the line table ───────────────────────────────────────────────────

def test_card_slot_table_follows_the_0053_agreement():
    from flowgate.card_documents.slots import card_slots, is_disposable_draft

    def table(node_type, doc_type, **kw):
        return [(s.slot, s.doc_type, s.author, s.shares) for s in card_slots(node_type, doc_type, **kw)]

    assert table("document", "R") == [("requirement", "R", "human", None)]
    assert table("document", "b", ai_delegation=True) == [("requirement", "B", "human", None),
                                                          ("delegation", "M", "run", None)]
    assert table("document", "WP") == [("plan", "WP", "human", None), ("deploy", "M", "run", None)]
    # flowgate.v02.0056 T#2: an instruction the card's instruction AI writes is "instruction AI or a person"
    ai_or_human = "instruction_ai_or_human"
    for design in ("D", "P", "L", "DB"):
        assert table("document", design) == [("instruction", "DS", ai_or_human, None), ("report", design, "run", None)]
    assert table("document", "N") == [("instruction", "N", ai_or_human, None), ("report", "NR", "run", None)]
    assert table("document", "T") == [("instruction", "T", ai_or_human, None), ("report", "TR", "run", None)]
    assert table("document", "T2") == [("instruction", "T2", ai_or_human, None), ("report", "TR2", "run", None)]
    assert table("document", "TS") == [("instruction", "TS", "human", None), ("scenario", "TS", "run", "instruction"),
                                       ("report", "TSR", "run", None)]       # a test card has no instruction AI
    assert table("custom", None) == [("instruction", "T", ai_or_human, None), ("report", "TR", "run", None)]
    assert table("gate", None) == [] and table("document", "M") == []
    assert is_disposable_draft({"status": "draft"}) is True
    assert not is_disposable_draft({"status": "draft", "doc_review_status": "pending_review"})
    assert not is_disposable_draft({"status": "open"}) and not is_disposable_draft(None)


# ── placement ────────────────────────────────────────────────────────────────

def test_placing_cards_creates_their_document_lines(env):
    body = put_draft(env, FB, 0, ALL_CARDS)
    km = body["key_map"]
    assert lines(body, km["tmp_r"]) == [("requirement", "R")]
    assert lines(body, km["tmp_wp"]) == [("plan", "WP"), ("deploy", "M")]
    assert lines(body, km["tmp_d"]) == [("instruction", "DS"), ("report", "D")]
    assert lines(body, km["tmp_p"]) == [("instruction", "DS"), ("report", "P")]
    assert lines(body, km["tmp_n"]) == [("instruction", "N"), ("report", "NR")]
    assert lines(body, km["tmp_t"]) == [("instruction", "T"), ("report", "TR")]
    assert lines(body, km["tmp_ts"]) == [("instruction", "TS"), ("scenario", "TS"), ("report", "TSR")]
    assert lines(body, km["tmp_c"]) == [("instruction", "T"), ("report", "TR")]
    assert lines(body, km["tmp_g"]) == []
    ts = body["card_documents"][km["tmp_ts"]]
    assert ts[0]["doc_id"] == ts[1]["doc_id"] and ts[0]["author"] == "human" and ts[1]["author"] == "run"
    made = {line["doc_id"] for key in km.values() for line in body["card_documents"].get(key, [])}
    assert len(made) == 15                                       # the shared scenario line adds no document
    for key, card in zip(("tmp_r", "tmp_t"), ("요구", "구현")):
        first = body["card_documents"][km[key]][0]
        assert first["title"].startswith(card) and first["status"] == "draft"
    for doc_id in made:
        d = doc(doc_id)
        assert d["group_id"] == FB and d["status"] == "draft" and d["doc_review_status"] is None
        assert d["owner_id"] == "u_kim" and json.loads(d["meta"])["graph_card"]["node_key"] in km.values()
        assert (env["storage"] / d["file_path"]).is_file(), d["file_path"]
    wp = doc(body["card_documents"][km["tmp_wp"]][0]["doc_id"])
    plan = json.loads((env["storage"] / wp["file_path"]).read_text(encoding="utf-8"))
    assert plan["binding"] == "advisory" and wp["title"]
    t_doc = doc(body["card_documents"][km["tmp_t"]][0]["doc_id"])
    head = (env["storage"] / t_doc["file_path"]).read_text(encoding="utf-8")
    assert head.startswith("---\n") and f"group_id: {FB}" in head and "type: T" in head
    assert len(doc_events(env, "doc_created")) == 15
    assert {r["owned"] for r in line_rows(env, FB)} == {0, 1}    # scenario lines share, all others own


def test_a_requirement_card_shows_the_existing_root_instead_of_a_second_one(env):
    body = put_draft(env, FA, 0, [node("tmp_r", "R", "요구"), node("tmp_r2", "R", "요구 둘")])
    km = body["key_map"]
    assert [line["doc_id"] for line in body["card_documents"][km["tmp_r"]]] == [f"{FA}.0001-R"]
    assert km["tmp_r2"] not in body["card_documents"]           # no second root: the line stays empty
    assert line_rows(env, FA) == [{"node_key": km["tmp_r"], "slot": "requirement", "doc_type": "R",
                                   "doc_id": f"{FA}.0001-R", "owned": 0}]
    assert len(_sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ?", (FA,))) == 1


def test_canvas_shows_card_lines_and_keeps_card_documents_inside_the_card(env):
    body = put_draft(env, FB, 0, ALL_CARDS)
    km = body["key_map"]
    view = canvas(env, FB)
    assert view["projection"] == "documents" and view["view"]["mode"] == "draft"
    assert {n["origin"] for n in view["nodes"]} == {"draft_node"}  # no card document drawn on its own
    by_key = {n["node_key"]: n for n in view["nodes"]}
    for ref, key in km.items():
        if not ref.startswith("tmp_") or key.startswith("ge_"):
            continue
        n = by_key[key]
        assert n["card_documents"] == body["card_documents"].get(key, []), ref
        assert n["state"] == "draft" and n["title"] == n["graph_node"]["label"]
    assert by_key[km["tmp_t"]]["document"]["doc_id"] == body["card_documents"][km["tmp_t"]][0]["doc_id"]
    assert by_key[km["tmp_ts"]]["document"]["type_code"] == "TS"
    assert by_key[km["tmp_g"]]["document"] is None and by_key[km["tmp_g"]]["card_documents"] == []
    other = canvas(env, FA)                                        # a Flow without a draft: unchanged
    assert [n["node_key"] for n in other["nodes"]] == [f"doc:{FA}.0001-R"]
    put_draft(env, FA, 0, [node("tmp_r", "R", "요구")])
    fa = {n["node_key"]: n for n in canvas(env, FA)["nodes"]}
    assert f"doc:{FA}.0001-R" in fa                                # the shown root stays a document node
    assert [n["document"]["doc_id"] for n in fa.values() if n["origin"] == "draft_node"] == [f"{FA}.0001-R"]


# ── re-save, retype, removal ─────────────────────────────────────────────────

def test_resaving_keeps_the_lines_and_a_new_card_gets_its_own(env):
    first = put_draft(env, FB, 0, [node("tmp_n", "N", "조사"), node("tmp_t", "T", "구현")])
    km = first["key_map"]
    count = len(doc_events(env, "doc_created"))
    again = put_draft(env, FB, 1, [node(km["tmp_n"], "N", "조사 (이름 변경)"), node(km["tmp_t"], "T", "구현")])
    assert again["card_documents"] == first["card_documents"]
    assert len(doc_events(env, "doc_created")) == count
    third = put_draft(env, FB, 2, [node(km["tmp_n"], "N"), node(km["tmp_t"], "T"), node("tmp_ts", "TS", "시험")])
    assert lines(third, third["key_map"]["tmp_ts"]) == [("instruction", "TS"), ("scenario", "TS"), ("report", "TSR")]
    assert len(doc_events(env, "doc_created")) == count + 2


def test_retyping_a_card_replaces_only_the_line_whose_type_changed(env):
    first = put_draft(env, FB, 0, [node("tmp_d", "D", "설계")])
    key = first["key_map"]["tmp_d"]
    ds, d = (line["doc_id"] for line in first["card_documents"][key])
    again = put_draft(env, FB, 1, [node(key, "P", "설계")])
    instruction, report = again["card_documents"][key]
    assert instruction["doc_id"] == ds and report["doc_type"] == "P" and report["doc_id"] != d
    assert doc(d) is None and doc(report["doc_id"])["type_code"] == "P"


def test_removing_a_card_deletes_its_drafts_and_keeps_what_went_further(env):
    from modules.flow_gate.db import documents as db_documents

    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_n", "N"), node("tmp_t", "T")],
                      [edge("tmp_e1", "tmp_r", "tmp_n"), edge("tmp_e2", "tmp_n", "tmp_t")])
    km = first["key_map"]
    n_instruction, n_report = (line["doc_id"] for line in first["card_documents"][km["tmp_n"]])
    t_docs = [line["doc_id"] for line in first["card_documents"][km["tmp_t"]]]
    db_documents.update(n_instruction, {"doc_review_status": "pending_review"})      # submitted: not a draft
    again = put_draft(env, FA, 1, [])
    assert set(again["removed_node_keys"]) == set(km[k] for k in ("tmp_r", "tmp_n", "tmp_t"))
    assert again["card_documents"] == {} and line_rows(env, FA) == []
    assert all(doc(d) is None for d in t_docs + [n_report])
    assert doc(n_instruction)["doc_review_status"] == "pending_review"
    assert doc(f"{FA}.0001-R") is not None                          # the root was never the card's
    assert len(doc_events(env, "doc_deleted")) == 3
    free = {n["node_key"] for n in canvas(env, FA)["nodes"]}
    assert f"doc:{n_instruction}" in free                            # unlinked: a free document node again


def test_a_node_of_a_confirmed_revision_keeps_its_documents(env):
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T")], [edge("tmp_e", "tmp_r", "tmp_t")])
    km = first["key_map"]
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    t_docs = [line["doc_id"] for line in first["card_documents"][km["tmp_t"]]]
    put_draft(env, FA, 0, [node(km["tmp_r"], "R")])
    assert all(doc(d) is not None for d in t_docs)
    assert [r["doc_id"] for r in line_rows(env, FA, km["tmp_t"])] == t_docs
    revision = {n["node_key"]: n for n in canvas(env, FA, "?view=revision")["nodes"]}
    assert [line["doc_id"] for line in revision[km["tmp_t"]]["card_documents"]] == t_docs
    assert revision[km["tmp_t"]]["document"]["doc_id"] == t_docs[0]


def test_retyping_a_node_of_a_confirmed_revision_keeps_the_revision_lines(env):
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_d", "D", "설계"), node("tmp_ts", "TS", "시험")],
                      [edge("tmp_e1", "tmp_r", "tmp_d"), edge("tmp_e2", "tmp_d", "tmp_ts")])
    km = first["key_map"]
    d_key, ts_key = km["tmp_d"], km["tmp_ts"]
    d_docs = [line["doc_id"] for line in first["card_documents"][d_key]]
    ts_docs = [line["doc_id"] for line in first["card_documents"][ts_key]]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    deleted = len(doc_events(env, "doc_deleted"))
    retyped = put_draft(env, FA, 0, [node(km["tmp_r"], "R"), node(d_key, "P", "설계"), node(ts_key, "T", "시험")])
    assert lines(retyped, d_key) == [("instruction", "DS"), ("report", "P")]
    assert lines(retyped, ts_key) == [("instruction", "T"), ("report", "TR")]
    p_report = retyped["card_documents"][d_key][1]["doc_id"]
    assert retyped["card_documents"][d_key][0]["doc_id"] == d_docs[0] and p_report not in d_docs
    assert all(doc(d)["status"] == "draft" for d in d_docs + ts_docs)          # the revision's drafts stay
    assert len(doc_events(env, "doc_deleted")) == deleted
    assert {(r["slot"], r["doc_type"], r["doc_id"]) for r in line_rows(env, FA, d_key)} == {
        ("instruction", "DS", d_docs[0]), ("report", "D", d_docs[1]), ("report", "P", p_report)}
    revision = {n["node_key"]: n for n in canvas(env, FA, "?view=revision")["nodes"]}
    assert [line["doc_id"] for line in revision[d_key]["card_documents"]] == d_docs
    assert [line["doc_type"] for line in revision[ts_key]["card_documents"]] == ["TS", "TS", "TSR"]
    assert [line["doc_id"] for line in revision[ts_key]["card_documents"]] == ts_docs
    draft = {n["node_key"]: n for n in canvas(env, FA, "?view=draft")["nodes"]}
    assert draft[d_key]["card_documents"] == retyped["card_documents"][d_key]
    assert draft[ts_key]["card_documents"] == retyped["card_documents"][ts_key]
    t_docs = [line["doc_id"] for line in retyped["card_documents"][ts_key]]
    back = put_draft(env, FA, 1, [node(km["tmp_r"], "R"), node(d_key, "D", "설계"), node(ts_key, "TS", "시험")])
    assert [line["doc_id"] for line in back["card_documents"][d_key]] == d_docs
    assert [line["doc_id"] for line in back["card_documents"][ts_key]] == ts_docs
    assert all(doc(d) is None for d in t_docs + [p_report])                  # draft-only kinds go
    assert len(doc_events(env, "doc_deleted")) == deleted + 3
    put_draft(env, FA, 2, [node(km["tmp_r"], "R")])                          # removed from the draft
    assert [r["doc_id"] for r in line_rows(env, FA, d_key)] == d_docs
    assert all(doc(d) is not None for d in d_docs + ts_docs)


def test_one_save_of_requirement_cards_on_an_empty_flow_makes_one_root(env):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.domain.canvas_mapping import is_alive
    from modules.flow_gate.graph_workflow.domain.root_binding import resolve_root

    body = put_draft(env, FB, 0, [node("tmp_r", "R", "요구"), node("tmp_r2", "R", "요구 둘"), node("tmp_b", "B", "버그")])
    km = body["key_map"]
    roots = _sql(env["db"], "SELECT doc_id, type_code FROM documents WHERE group_id = ?", (FB,))
    assert [r["type_code"] for r in roots] == ["R"]
    assert [line["doc_id"] for line in body["card_documents"][km["tmp_r"]]] == [roots[0]["doc_id"]]
    assert km["tmp_r2"] not in body["card_documents"] and km["tmp_b"] not in body["card_documents"]
    alive = [d for d in db_documents.get_documents_by_group_id(FB) if is_alive(d, FB)]
    assert resolve_root(alive)[0] == "one"                                   # the confirmation can bind it


def test_retyping_a_draft_only_requirement_card_makes_the_new_root(env):
    first = put_draft(env, FB, 0, [node("tmp_r", "R", "요구")])
    key = first["key_map"]["tmp_r"]
    r_doc = first["card_documents"][key][0]["doc_id"]
    again = put_draft(env, FB, 1, [node(key, "B", "요구")])
    assert doc(r_doc) is None
    assert lines(again, key) == [("requirement", "B")]
    b_doc = again["card_documents"][key][0]["doc_id"]
    assert doc(b_doc)["type_code"] == "B" and doc(b_doc)["status"] == "draft"
    assert line_rows(env, FB, key) == [{"node_key": key, "slot": "requirement", "doc_type": "B",
                                        "doc_id": b_doc, "owned": 1}]


@pytest.mark.parametrize("order", ["retyped_first", "new_first"])
def test_one_save_that_retypes_the_root_card_and_adds_a_requirement_card_makes_its_root(env, order):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.domain.canvas_mapping import is_alive
    from modules.flow_gate.graph_workflow.domain.root_binding import resolve_root

    first = put_draft(env, FB, 0, [node("tmp_a", "R", "요구")])
    a_key = first["key_map"]["tmp_a"]
    old_root = first["card_documents"][a_key][0]["doc_id"]
    cards = [node(a_key, "T", "구현"), node("tmp_b", "R", "새 요구")]
    again = put_draft(env, FB, 1, cards if order == "retyped_first" else cards[::-1])
    b_key = again["key_map"]["tmp_b"]
    assert doc(old_root) is None                                             # the released draft root went first
    assert lines(again, a_key) == [("instruction", "T"), ("report", "TR")]
    assert lines(again, b_key) == [("requirement", "R")]
    new_root = again["card_documents"][b_key][0]["doc_id"]
    assert doc(new_root)["type_code"] == "R" and doc(new_root)["status"] == "draft"
    assert line_rows(env, FB, b_key) == [{"node_key": b_key, "slot": "requirement", "doc_type": "R",
                                          "doc_id": new_root, "owned": 1}]
    alive = [d for d in db_documents.get_documents_by_group_id(FB) if is_alive(d, FB)]
    assert [d["type_code"] for d in alive if d["type_code"] in ("R", "B")] == ["R"]
    assert resolve_root(alive)[0] == "one"


@pytest.mark.parametrize("order", ["retyped_first", "new_first"])
def test_a_confirmed_root_card_retyped_with_a_new_requirement_card_keeps_one_root(env, order):
    first = put_draft(env, FA, 0, [node("tmp_a", "R", "요구"), node("tmp_t", "T")], [edge("tmp_e", "tmp_a", "tmp_t")])
    km = first["key_map"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    created = len(doc_events(env, "doc_created"))
    cards = [node(km["tmp_a"], "T", "구현"), node("tmp_b", "R", "새 요구"), node(km["tmp_t"], "T")]
    again = put_draft(env, FA, 0, cards if order == "retyped_first" else cards[::-1])
    b_key = again["key_map"]["tmp_b"]
    assert {"node_key": km["tmp_a"], "slot": "requirement", "doc_type": "R", "doc_id": f"{FA}.0001-R",
            "owned": 0} in line_rows(env, FA, km["tmp_a"])                # the revision's root line stays
    assert line_rows(env, FA, b_key) == [] and b_key not in again["card_documents"]   # no second root
    assert len(doc_events(env, "doc_created")) == created + 2                # only the retyped card's T · TR
    assert [d["doc_id"] for d in _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ? "
                                                 "AND type_code IN ('R', 'B')", (FA,))] == [f"{FA}.0001-R"]


@pytest.fixture()
def convert_root(env):
    """``PATCH /documents/{doc_id}/root-type`` on the real v0.1 documents router."""
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import documents as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u_kim", "is_admin": True}
    client = TestClient(app)

    def run(doc_id, new_type):
        res = client.patch(f"/api/v1/documents/{doc_id}/root-type", json={"new_type": new_type})
        assert res.status_code == 200, res.text
        return res.json()["data"]["doc_id"]
    return run


def _draft_card(env, flow, key):
    nodes = canvas(env, flow, "?view=draft")["nodes"]
    return {n["node_key"]: n for n in nodes}[key], {n["node_key"] for n in nodes}


def test_converting_a_card_root_moves_its_line_and_the_card_still_owns_it(env, convert_root):
    first = put_draft(env, FB, 0, [node("tmp_r", "R", "요구")])
    key = first["key_map"]["tmp_r"]
    r_doc = first["card_documents"][key][0]["doc_id"]
    b_doc = convert_root(r_doc, "B")                                         # R -> B
    assert doc(r_doc) is None and doc(b_doc)["type_code"] == "B"
    assert line_rows(env, FB) == [{"node_key": key, "slot": "requirement", "doc_type": "B", "doc_id": b_doc, "owned": 1}]
    shown, keys = _draft_card(env, FB, key)
    assert [(line["doc_type"], line["doc_id"]) for line in shown["card_documents"]] == [("B", b_doc)]
    assert shown["document"]["doc_id"] == b_doc and f"doc:{b_doc}" not in keys
    created, deleted = len(doc_events(env, "doc_created")), len(doc_events(env, "doc_deleted"))
    same = put_draft(env, FB, 1, [node(key, "R", "요구")])                     # re-save, the card unchanged
    assert [line["doc_id"] for line in same["card_documents"][key]] == [b_doc]
    assert line_rows(env, FB)[0]["doc_id"] == b_doc and doc(b_doc) is not None
    assert (len(doc_events(env, "doc_created")), len(doc_events(env, "doc_deleted"))) == (created, deleted)
    retyped = put_draft(env, FB, 2, [node(key, "B", "요구")])                  # the card follows the document
    assert lines(retyped, key) == [("requirement", "B")] and retyped["card_documents"][key][0]["doc_id"] == b_doc
    r_again = convert_root(b_doc, "R")                                       # B -> R
    assert line_rows(env, FB) == [{"node_key": key, "slot": "requirement", "doc_type": "R", "doc_id": r_again, "owned": 1}]
    shown, keys = _draft_card(env, FB, key)
    assert [line["doc_id"] for line in shown["card_documents"]] == [r_again] and f"doc:{r_again}" not in keys
    put_draft(env, FB, 3, [node(key, "B", "요구")])
    assert line_rows(env, FB)[0]["doc_id"] == r_again and doc(r_again) is not None
    gone = put_draft(env, FB, 4, [])                                         # removing the card deletes its draft
    assert gone["removed_node_keys"] == [key] and line_rows(env, FB) == []
    assert doc(r_again) is None and len(doc_events(env, "doc_deleted")) == deleted + 1
    assert _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ?", (FB,)) == []


def test_converting_a_card_root_then_removing_the_card_deletes_the_converted_draft(env, convert_root):
    first = put_draft(env, FB, 0, [node("tmp_b", "B", "버그")])
    key = first["key_map"]["tmp_b"]
    b_doc = first["card_documents"][key][0]["doc_id"]
    r_doc = convert_root(b_doc, "R")                                         # B -> R, then the card goes
    shown, _keys = _draft_card(env, FB, key)
    assert [line["doc_id"] for line in shown["card_documents"]] == [r_doc]
    put_draft(env, FB, 1, [])
    assert doc(r_doc) is None and doc(b_doc) is None and line_rows(env, FB) == []
    assert canvas(env, FB, "?view=draft")["nodes"] == []                     # nothing left as a free document


# ── a run fills the line ─────────────────────────────────────────────────────

def test_a_run_result_takes_the_line_of_its_type(env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact

    lee = {"kind": "human", "user_id": "u_lee", "provider_id": None}
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_d", "D", "설계", assignee=lee)],
                      [edge("tmp_e", "tmp_r", "tmp_d")])
    key = first["key_map"]["tmp_d"]
    ds, placeholder = (line["doc_id"] for line in first["card_documents"][key])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    result = _doc(FA, 90, "D")
    v01_contact.after_inbox_registered(FA, result, "D", token)
    assert [(r["slot"], r["doc_id"], r["owned"]) for r in line_rows(env, FA, key)] == [
        ("instruction", ds, 1), ("report", result, 0)]
    assert doc(placeholder)["superseded_by"] == result                # the empty draft leads to the result
    shown = {n["node_key"]: n for n in canvas(env, FA)["nodes"]}[key]
    assert [line["doc_id"] for line in shown["card_documents"]] == [ds, result]
    assert shown["document"]["doc_id"] == result and shown["state"] == "review"
    assert f"doc:{placeholder}" not in {n["node_key"] for n in canvas(env, FA)["nodes"]}


def test_a_run_result_line_stays_when_the_next_draft_retypes_the_node(env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact

    lee = {"kind": "human", "user_id": "u_lee", "provider_id": None}
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_d", "D", "설계", assignee=lee)],
                      [edge("tmp_e", "tmp_r", "tmp_d")])
    km = first["key_map"]
    key = km["tmp_d"]
    ds, placeholder = (line["doc_id"] for line in first["card_documents"][key])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    assert env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h()).status_code == 201
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    result = _doc(FA, 90, "D")
    v01_contact.after_inbox_registered(FA, result, "D", token)
    retyped = put_draft(env, FA, 0, [node(km["tmp_r"], "R"), node(key, "P", "설계")])
    assert lines(retyped, key) == [("instruction", "DS"), ("report", "P")]
    assert retyped["card_documents"][key][0]["doc_id"] == ds
    assert ("report", "D", result, 0) in {(r["slot"], r["doc_type"], r["doc_id"], r["owned"])
                                          for r in line_rows(env, FA, key)}
    assert doc(result) is not None and doc(placeholder)["superseded_by"] == result
    shown = {n["node_key"]: n for n in canvas(env, FA, "?view=revision")["nodes"]}[key]
    assert [line["doc_id"] for line in shown["card_documents"]] == [ds, result]
    assert shown["document"]["doc_id"] == result and shown["node_run"]["result_doc_id"] == result


# ── migration 145 ────────────────────────────────────────────────────────────

def test_migration_145_table_trigger_and_flow_delete_cleanup(env):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert "graph_node_documents" in tables and "trg_graph_node_documents_group_deleted" in triggers
    put_draft(env, FB, 0, [node("tmp_t", "T")])
    put_draft(env, FA, 0, [node("tmp_n", "N")])
    conn = sqlite3.connect(env["db"])
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FB,))
        conn.commit()
    finally:
        conn.close()
    assert line_rows(env, FB) == [] and len(line_rows(env, FA)) == 2


def test_migration_145_has_the_same_table_in_three_dialects():
    import re

    found = {}
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "145_graph_node_documents.sql").read_text(encoding="utf-8")
        found[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), dialect
        assert not re.search(r"\b(ALTER|DROP|UPDATE)\b", sql), dialect
        assert "PRIMARY KEY (group_id, node_key, slot, doc_type)" in sql, dialect
    assert found["sqlite"] == found["postgres"] == found["mysql"] == ["graph_node_documents"]


# ── v0.2 placement: the Graph reaches the package only through its ports (D0006 §2.1 / §4.1) ──

def test_without_the_port_handlers_a_draft_save_makes_no_documents(env):
    from flowgate.card_documents import integration

    integration.unregister()
    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_r", "R")])
    assert body["card_documents"] == {} and line_rows(env, FB) == [] and doc_events(env, "doc_created") == []
    shown = canvas(env, FB)["nodes"]
    assert all(n.get("card_documents", []) == [] for n in shown)


def test_startup_registers_the_card_document_ports():
    import startup
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    from flowgate.card_documents import integration

    integration.unregister()
    try:
        assert not any(ports.registered(name) for name in ports.PORT_NAMES)
        startup.start_card_document_integration()
        assert all(ports.registered(name) for name in ports.PORT_NAMES)
        assert "start_card_document_integration()" in (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    finally:
        integration.unregister()


def test_the_feature_code_is_in_the_v02_package_and_the_graph_holds_only_ports():
    pkg = _SERVER_DIR / "flowgate" / "card_documents"
    assert {p.stem for p in pkg.glob("*.py")} == {
        "__init__", "slots", "lines", "repository", "legacy_adapter", "integration",
        "compose", "routes"}                     # flowgate.v02.0056 T#3: a person's line write and its route
    graph = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow"
    for gone in ("domain/card_slots.py", "store/node_document_store.py", "services/card_documents.py"):
        assert not (graph / gone).exists(), gone
    callers = sorted(p.relative_to(graph).as_posix() for p in graph.rglob("*.py")
                     if any(how in p.read_text(encoding="utf-8")
                            for how in ("import card_document_ports", "from .card_document_ports"))
                     and p.name != "card_document_ports.py")
    assert callers == ["services/canvas_service.py", "services/graph_service.py",
                       "services/run_apply.py", "services/v01_contact.py"]
