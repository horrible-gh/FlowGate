"""0064 T-B4b — an R↔B conversion keeps the card title states (L0005 2.17-2 / 2.17-3, V-B4b-1 conversion part).

On the R↔B conversion suite's real SQLite DB and temp storage (``test_root_conversion_number_swap_0064`` /
``test_root_conversion_succession_0064`` fixtures and helpers): a title state is keyed by (Flow, node key), and
neither a number swap nor a succession changes a node key, so every title state row — title, source, person mark,
opportunity, reason, version, times — stays byte for byte while the draft card takes the new type. After it the
default names of the card are the new type's (``버그``), and a person's title still shows first.
"""
from __future__ import annotations

import os

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 — its import-time env export happens at collection, not in a test

from test_root_conversion_number_swap_0064 import (  # noqa: E402,F401 — fixtures (env) and helpers
    FLOW, ROOT, ROOT_B, USER, _sql, convert, env, make_doc,
)
from test_root_conversion_succession_0064 import with_children  # noqa: E402

CARDS = [
    {"node_key": "n_req", "node_type": "document", "doc_type": "R", "palette_item_id": "requirement",
     "label": "요구사항"},
    {"node_key": "n_named", "node_type": "document", "doc_type": "R", "palette_item_id": "requirement",
     "label": "로그인 요구"},
]


def _draft_with_titles():
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import definition_store, title_store

    with get_store().transaction():
        definition_store.create_draft(FLOW, None, USER)
        definition_store.replace_draft_content(FLOW, CARDS, [])

    def titles():
        title_store.save_human(FLOW, "n_req", "로그인 개선 요구", USER)
        title_store.save_human(FLOW, "n_req", "로그인 개선 요구 정리", USER)     # version 2
        title_store.claim(FLOW, "n_named", "tok_claim", "air_1", None)        # an opportunity in flight

    with_flow_guard(FLOW, titles)
    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda flow, old, new: ["n_req", "n_named"])


def _title_rows(env):
    return _sql(env["db"], "SELECT * FROM graph_node_title_states WHERE group_id = ? ORDER BY node_key", (FLOW,))


def _draft_cards(env):
    return {r["node_key"]: (r["doc_type"], r["palette_item_id"], r["label"])
            for r in _sql(env["db"], "SELECT * FROM graph_draft_nodes WHERE group_id = ?", (FLOW,))}


def _check_kept(env, before):
    from modules.flow_gate.graph_workflow.projection.graph_projector import GraphView, add_draft
    from modules.flow_gate.graph_workflow.projection.legacy_projector import Projection
    from modules.flow_gate.graph_workflow.services import node_title_names as names
    from modules.flow_gate.graph_workflow.store import definition_store, title_store

    assert _title_rows(env) == before                                     # every column, every row
    cards = _draft_cards(env)
    assert cards == {"n_req": ("B", "bug", "버그"), "n_named": ("B", "bug", "로그인 요구")}
    rows = {n["node_key"]: n for n in definition_store.draft(FLOW)["nodes"]}
    assert names.is_default_name("버그", rows["n_req"]) and names.is_default_name("Bug", rows["n_req"])
    assert not names.is_default_name("로그인 요구", rows["n_named"])
    view = GraphView(mode="draft", nodes=list(rows.values()), title_states=title_store.states(FLOW))
    shown = {n["node_key"]: n for n in add_draft(Projection(projection="documents"), "unassigned", [], view).nodes}
    assert shown["n_req"]["title"] == "로그인 개선 요구 정리"
    assert shown["n_req"]["title_state"] == {"title": "로그인 개선 요구 정리", "title_source": "human", "version": 2}
    assert (shown["n_named"]["title"], shown["n_named"]["title_state"]["version"]) == ("로그인 요구", 1)


def test_a_number_swap_keeps_every_title_state(env):
    make_doc(env)
    _draft_with_titles()
    before = _title_rows(env)
    assert [(r["node_key"], r["version"], r["opportunity_state"]) for r in before] == [
        ("n_named", 1, "claimed"), ("n_req", 2, "closed")]

    result = convert()

    assert (result["conversion"]["mode"], result["conversion"]["new_doc_id"]) == ("number_swap", ROOT_B)
    _check_kept(env, before)


def test_a_succession_keeps_every_title_state(env):
    with_children(env)
    _draft_with_titles()
    before = _title_rows(env)

    result = convert()

    assert result["conversion"]["mode"] == "succession"
    assert _sql(env["db"], "SELECT superseded_by FROM documents WHERE doc_id = ?", (ROOT,))[0]["superseded_by"] == \
        ROOT_B
    _check_kept(env, before)
