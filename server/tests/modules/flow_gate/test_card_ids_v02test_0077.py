"""Flow and card display ids (F92, F92-3): numbering, stability and lookup.

On the real SQLite DB with every migration, the Graph routes and card documents (the ``test_fg_card_settings``
harness): a card placed in a draft takes its Flow's next number in the same save; a number never changes, is never
handed out again after its card is deleted, and a copied card (a new node key) takes a new one; cards made before
card numbers are numbered once, in the order they were made, by the first Canvas read; every stored id (group id,
document ids, node keys) is unchanged. The typed forms of a display id resolve to the card / Flow through the
document search, the Flow information read and the document read; stored ids resolve as before.
"""
from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 -- its import-time env export happens at collection, not in a test

import _flowgate_test_imports  # noqa: F401
from test_fg_card_settings import (  # noqa: E402,F401 -- fixtures used by name
    FA, FB, _h, _sql, db_path, env, legacy_cards, node, put_draft,
)

from modules.flow_gate.services import card_ids, card_numbers  # noqa: E402

pytestmark = pytest.mark.usefixtures("legacy_cards")

FC = "flowgate.v03.0092"          # the same group number in another module


# ── helpers ──────────────────────────────────────────────────────────────────

def canvas(env, flow, view=None):
    res = env["client"].get(f"/api/v1/flows/{flow}/canvas" + (f"?view={view}" if view else ""), headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def numbers_shown(env, flow, view=None):
    return {n["node_key"]: (n["card_ref"] or {}).get("card_no") for n in canvas(env, flow, view)["nodes"]}


def draft_nodes(env, flow):
    res = env["client"].get(f"/api/v1/flows/{flow}/graph", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["draft"]


def keep(key, doc_type="T"):
    return node(key, doc_type)


def stored(env, flow):
    rows = _sql(env["db"], "SELECT node_key, card_no FROM graph_card_numbers WHERE group_id = ? ORDER BY card_no",
                (flow,))
    return [(r["node_key"], r["card_no"]) for r in rows]


@pytest.fixture()
def routes(env, monkeypatch):
    """The list and document routes on the harness app, with the harness's user bearer."""
    from fastapi.responses import JSONResponse

    from modules.flow_gate.api.v1 import document_routes, list_routes
    from modules.flow_gate.services import auth_outbound

    def verify(request):
        raw = request.headers.get("Authorization", "")
        if raw.startswith("Bearer user:"):
            return {"_is_user_jwt": True, "issued_to": raw[len("Bearer user:"):]}
        return JSONResponse(status_code=401, content={"ok": False})

    for module in (list_routes, document_routes):
        monkeypatch.setattr(module, "verify_bearer", verify)
        monkeypatch.setattr(module, "can_read_project", lambda auth, project: True)
    monkeypatch.setattr(list_routes, "readable_projects", lambda auth: ["flowgate"])
    monkeypatch.setattr(auth_outbound, "readable_projects", lambda auth: ["flowgate"])
    app = env["client"].app
    app.include_router(list_routes.router)
    app.include_router(document_routes.router)
    return env["client"]


# ── pure forms ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, flow_no, card_no, project, module", [
    ("flowgate.v02test.F77-12", 77, 12, "flowgate", "v02test"),
    ("v02test.F77-12", 77, 12, None, "v02test"),
    ("F77-12", 77, 12, None, None),
    ("f77-12", 77, 12, None, None),
    ("F77-012", 77, 12, None, None),
    ("F0077-12", 77, 12, None, None),
    ("flowgate.v02test.F77", 77, None, "flowgate", "v02test"),
    ("F77", 77, None, None, None),
    ("f0077", 77, None, None, None),
    ("77", 77, None, None, None),
])
def test_every_typed_form_names_its_flow_and_card(text, flow_no, card_no, project, module):
    parsed = card_ids.parse(text)
    assert (parsed.flow_no, parsed.card_no, parsed.project, parsed.module) == (flow_no, card_no, project, module)


@pytest.mark.parametrize("text", ["flowgate.v02test.0077", "flowgate.v02test.0077.0003-T", "F0", "F77-0",
                                  "c_abc", "gn_0123456789ab", "", "F77-12-3", "a.b.c.F7"])
def test_stored_ids_and_other_text_are_no_display_id(text):
    assert card_ids.parse(text) is None


def test_display_forms_follow_the_group_id():
    assert card_ids.flow_label("flowgate.v02test.0077") == "F77"
    assert card_ids.flow_label("flowgate.v02test.10000") == "F10000"
    assert card_ids.flow_ref("flowgate.v02test.0077") == {
        "flow_no": 77, "label": "F77", "module_ref": "v02test.F77", "full": "flowgate.v02test.F77"}
    assert card_ids.card_ref("flowgate.v02test.0077", 12) == {
        "card_no": 12, "local": "#12", "short": "F77-12", "module_ref": "v02test.F77-12",
        "full": "flowgate.v02test.F77-12"}
    assert card_ids.card_ref("flowgate.v02test.0077", None) is None
    assert card_ids.parse("#12").card_no == 12 and card_ids.parse("#12", allow_local=False) is None


# ── numbering ────────────────────────────────────────────────────────────────

def test_new_cards_are_numbered_in_order_and_keep_their_numbers(env):
    first = put_draft(env, FB, 0, [node("tmp_a"), node("tmp_b"), node("tmp_c", "D")])
    a, b, c = (first["key_map"][k] for k in ("tmp_a", "tmp_b", "tmp_c"))
    assert stored(env, FB) == [(a, 1), (b, 2), (c, 3)]
    body = canvas(env, FB)
    assert body["flow_ref"] == {"flow_no": 92, "label": "F92", "module_ref": "v02.F92", "full": "flowgate.v02.F92"}
    shown = {n["node_key"]: n["card_ref"] for n in body["nodes"]}
    assert shown[b] == {"card_no": 2, "local": "#2", "short": "F92-2", "module_ref": "v02.F92-2",
                        "full": "flowgate.v02.F92-2"}
    assert numbers_shown(env, FB) == {a: 1, b: 2, c: 3}          # read again: unchanged

    # c deleted, d added: d takes 4; then e added: 5 (the deleted card's 3 is never handed out again).
    second = put_draft(env, FB, 1, [keep(a), keep(b), node("tmp_d")])
    d = second["key_map"]["tmp_d"]
    third = put_draft(env, FB, 2, [keep(a), keep(b), keep(d), node("tmp_e")])
    e = third["key_map"]["tmp_e"]
    assert numbers_shown(env, FB) == {a: 1, b: 2, d: 4, e: 5}
    assert (c, 3) in stored(env, FB)                               # the row stays; the number is spent

    # A copy of b in the same Flow is a new card: a new key and the next number, b keeps its own.
    copy = put_draft(env, FB, 3, [keep(a), keep(b), keep(d), keep(e), node("tmp_copy_b")])["key_map"]["tmp_copy_b"]
    assert copy != b and numbers_shown(env, FB)[copy] == 6 and numbers_shown(env, FB)[b] == 2


def test_a_card_copied_to_another_flow_takes_that_flows_next_number(env):
    from modules.flow_gate.db import groups as db_groups

    db_groups.create({"group_id": FC, "project_id": "flowgate", "module": "v03", "title": FC, "status": "OPEN"})
    put_draft(env, FC, 0, [node("tmp_x"), node("tmp_y")])
    source = put_draft(env, FB, 0, [node("tmp_a")])["key_map"]["tmp_a"]
    pasted = put_draft(env, FC, 1, [keep(k) for k, _ in stored(env, FC)] + [node("tmp_pasted")])
    key = pasted["key_map"]["tmp_pasted"]
    assert key != source
    assert numbers_shown(env, FC)[key] == 3 and numbers_shown(env, FB)[source] == 1


def test_cards_made_before_numbers_are_numbered_once_in_creation_order(env):
    made = put_draft(env, FB, 0, [node("tmp_1"), node("tmp_2"), node("tmp_3")])["key_map"]
    keys = [made[k] for k in ("tmp_1", "tmp_2", "tmp_3")]
    lines_before = _sql(env["db"], "SELECT * FROM graph_node_documents WHERE group_id = ? ORDER BY node_key", (FB,))
    # A Flow from before card numbers: no number rows, no counter.
    _sql(env["db"], "DELETE FROM graph_card_numbers WHERE group_id = ?", (FB,))
    _sql(env["db"], "DELETE FROM graph_card_counters WHERE group_id = ?", (FB,))
    assert numbers_shown(env, FB) == dict(zip(keys, (1, 2, 3)))    # numbered by the first read, in draft order
    assert numbers_shown(env, FB) == dict(zip(keys, (1, 2, 3)))    # and only once
    # Only numbers were added: node keys, the draft and the card document lines are as they were.
    assert [n["node_key"] for n in draft_nodes(env, FB)["nodes"]] == keys
    assert _sql(env["db"], "SELECT * FROM graph_node_documents WHERE group_id = ? ORDER BY node_key",
                (FB,)) == lines_before
    assert all(k.startswith("gn_") for k in keys)
    groups = _sql(env["db"], "SELECT group_id FROM groups ORDER BY group_id")
    assert {g["group_id"] for g in groups} >= {FA, FB}


def test_a_pre_feature_flow_numbers_its_old_cards_before_a_new_one(env):
    made = put_draft(env, FB, 0, [node("tmp_1"), node("tmp_2")])["key_map"]
    _sql(env["db"], "DELETE FROM graph_card_numbers WHERE group_id = ?", (FB,))
    _sql(env["db"], "DELETE FROM graph_card_counters WHERE group_id = ?", (FB,))
    later = put_draft(env, FB, 1, [keep(made["tmp_1"]), keep(made["tmp_2"]), node("tmp_3")])["key_map"]["tmp_3"]
    assert stored(env, FB) == [(made["tmp_1"], 1), (made["tmp_2"], 2), (later, 3)]


def test_concurrent_assignments_never_share_a_number(env):
    errors = []

    def work(prefix):
        try:
            card_numbers.assign(FB, [f"{prefix}_{i}" for i in range(5)])
        except Exception as exc:      # noqa: BLE001 -- reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"k{t}",)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    numbers = [no for _, no in stored(env, FB)]
    assert len(numbers) == 20 and len(set(numbers)) == 20
    assert card_numbers.assign(FB, ["k0_0"])["k0_0"] == dict(stored(env, FB))["k0_0"]   # never renumbered


# ── lookup ───────────────────────────────────────────────────────────────────

def _cards(env):
    from modules.flow_gate.db import groups as db_groups

    made = put_draft(env, FB, 0, [node("tmp_a"), node("tmp_b")])["key_map"]
    db_groups.create({"group_id": FC, "project_id": "flowgate", "module": "v03", "title": FC, "status": "OPEN"})
    other = put_draft(env, FC, 0, [node("tmp_z")])["key_map"]["tmp_z"]
    return made["tmp_a"], made["tmp_b"], other


@pytest.mark.parametrize("text", ["flowgate.v02.F92-2", "v02.F92-2", "FLOWGATE.V02.f92-2", "v02.f0092-002"])
def test_card_forms_resolve_to_the_card_and_its_instruction(env, text):
    _a, b, _other = _cards(env)
    [found] = card_numbers.resolve(text)
    assert (found["kind"], found["group_id"], found["node_key"]) == ("card", FB, b)
    assert found["card_ref"]["full"] == "flowgate.v02.F92-2"
    lines = _sql(env["db"], "SELECT slot, doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ?",
                 (FB, b))
    assert found["doc_id"] == next(r["doc_id"] for r in lines if r["slot"] == "instruction")


def test_a_module_less_id_is_read_in_its_context_else_in_every_module(env):
    a, _b, other = _cards(env)
    assert {(r["group_id"], r["node_key"]) for r in card_numbers.resolve("F92-1")} == {(FB, a), (FC, other)}
    assert [(r["group_id"], r["node_key"]) for r in card_numbers.resolve("F92-1", context_flow_id=FB)] == [(FB, a)]
    assert [r["node_key"] for r in card_numbers.resolve("#1", context_flow_id=FC)] == [other]
    assert {r["group_id"] for r in card_numbers.resolve("f0092")} == {FB, FC}
    assert [r["group_id"] for r in card_numbers.resolve("flowgate.v02.F92")] == [FB]
    assert [r["group_id"] for r in card_numbers.resolve("92", context_flow_id=FB)] == [FB]
    assert card_numbers.resolve("F92-9") == [] and card_numbers.resolve(FB) == []
    assert card_numbers.resolve("F92-1", allowed_projects=["other"]) == []


def test_search_puts_the_exact_card_first_and_tells_how_to_open_it(env, routes):
    _a, b, _other = _cards(env)
    res = routes.get("/api/v1/search/documents", params={"q": "f92-2", "flow": FB}, headers=_h())
    assert res.status_code == 200, res.text
    first = res.json()["items"][0]
    assert first["match"] == "card" and first["group_id"] == FB
    assert first["card"]["node_key"] == b and first["card"]["card_ref"]["short"] == "F92-2"
    stored_id = first["doc_id"]
    # A stored doc_id is searched as before.
    res = routes.get("/api/v1/search/documents", params={"q": stored_id}, headers=_h())
    assert res.status_code == 200 and [i["doc_id"] for i in res.json()["items"]] == [stored_id]
    assert "match" not in res.json()["items"][0]


def test_flow_information_and_document_reads_take_display_ids(env, routes):
    _a, b, _other = _cards(env)
    res = routes.get("/api/v1/list/groups/flowgate.v02.F92", headers=_h())
    assert res.status_code == 200, res.text
    assert res.json()["group_id"] == FB and res.json()["flow_ref"]["label"] == "F92"
    title = res.json()["title"]
    assert res.json()["flow_name"] == (f"F92({title})" if title else "F92")     # how the AI names it
    listed = routes.get("/api/v1/list/projects/flowgate/groups", headers=_h())
    assert listed.status_code == 200, listed.text
    item = next(i for i in listed.json()["items"] if i["group_id"] == FB)
    assert item["flow_label"] == "F92" and item["flow_name"] == res.json()["flow_name"]
    res = routes.get(f"/api/v1/list/groups/{FB}", headers=_h())                       # stored id, as before
    assert res.status_code == 200 and res.json()["group_id"] == FB and "requested_id" not in res.json()
    res = routes.get("/api/v1/list/groups/flowgate.v02.F92-2", headers=_h())
    assert res.json()["group_id"] == FB and res.json()["card"]["node_key"] == b

    [found] = card_numbers.resolve("flowgate.v02.F92-2")
    res = routes.get("/api/v1/document/flowgate.v02.F92-2", headers=_h())
    assert res.status_code == 200, res.text
    assert res.json()["doc_id"] == found["doc_id"] and res.json()["requested_id"] == "flowgate.v02.F92-2"
    assert res.json()["card_ref"]["short"] == "F92-2"
    res = routes.get(f"/api/v1/document/{found['doc_id']}", headers=_h())             # stored id, as before
    assert res.status_code == 200 and res.json()["doc_id"] == found["doc_id"] and "requested_id" not in res.json()
    assert routes.get("/api/v1/document/flowgate.v02.F92-9", headers=_h()).status_code == 404
