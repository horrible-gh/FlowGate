"""(CH) — a Flow never gets stuck on its draft.

1. A card the run opened is put back into the draft (``flowgate.graph_run.draft_restore``): a draft saved while the
   card was pending and without it no longer refuses every later save.
2. A bug / requirement Flow has its start card from the registration of its root on
   (``graph_workflow.services.root_start``): a real draft card holding the root, made at registration, at startup for
   an older Flow and by the chat card API whatever the draft holds. No edge is ever made.

On the ``test_fg_card_settings`` harness (real SQLite DB, Graph routes, card document ports).
"""
from __future__ import annotations

import inspect

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, FA, FB, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")


def _draft(flow=FA):
    from modules.flow_gate.graph_workflow.store import definition_store
    return definition_store.draft(flow)


def _group(flow=FA):
    from modules.flow_gate.graph_workflow.services import canvas_service
    return canvas_service.load_flow(flow)


# ── 1. draft_restore (pure) ──────────────────────────────────────────────────

def _n(key, doc_type="T", label=None):
    return {"node_key": key, "node_type": "document", "doc_type": doc_type, "palette_item_id": None,
            "label": label or key, "assignee_kind": None, "assignee_user_id": None, "assignee_provider_id": None}


def _e(edge_id, source, target):
    return {"edge_id": edge_id, "source": source, "target": target, "condition": None}


@pytest.fixture()
def revision(monkeypatch):
    """Execution revision R -> T -> G and a run whose node rows the test sets."""
    from flowgate.graph_run import draft_restore
    from modules.flow_gate.graph_workflow.store import definition_store, run_store

    state = {"rows": [], "revision": {"nodes": [_n("gn_r", "R"), _n("gn_t"), _n("gn_g", "T", "G")],
                                      "edges": [_e("ge_1", "gn_r", "gn_t"), _e("ge_2", "gn_t", "gn_g")]}}
    monkeypatch.setattr(run_store, "node_runs", lambda run_id: state["rows"])
    monkeypatch.setattr(definition_store, "revision", lambda flow_id, no: state["revision"])
    monkeypatch.setattr(draft_restore, "_unresolved_gates", lambda flow_id, old, rows: set())
    state["run"] = {"run_id": "run_1", "revision_no": 1}
    return state


def test_a_card_the_run_opened_comes_back_with_its_input_edge_and_the_rest_stays(revision):
    from flowgate.graph_run import draft_restore

    revision["rows"] = [{"node_key": "gn_r", "status": "done"}, {"node_key": "gn_t", "status": "open"},
                        {"node_key": "gn_g", "status": "pending"}]
    # the draft lost gn_t (and its edges) while it was pending, and has a new card of the person
    nodes = [_n("gn_r", "R"), _n("gn_g", "T", "G"), _n("gn_new")]
    edges = [_e("ge_9", "gn_g", "gn_new")]
    out_nodes, out_edges, added = draft_restore.restored("f", nodes, edges, revision["run"])
    assert added == ["gn_t"]
    assert [n["node_key"] for n in out_nodes] == ["gn_r", "gn_g", "gn_new", "gn_t"]
    assert [e["edge_id"] for e in out_edges] == ["ge_9", "ge_1"]       # gn_t's input edge; ge_2 was the person's to drop


def test_a_protected_card_changed_in_the_draft_is_put_back_as_it_runs(revision):
    from flowgate.graph_run import draft_restore

    revision["rows"] = [{"node_key": "gn_r", "status": "done"}, {"node_key": "gn_t", "status": "review"}]
    nodes = [_n("gn_r", "R"), _n("gn_t", label="renamed"), _n("gn_x")]
    edges = [_e("ge_1", "gn_r", "gn_t"), _e("ge_8", "gn_x", "gn_t")]
    out_nodes, out_edges, added = draft_restore.restored("f", nodes, edges, revision["run"])
    assert added == [] and out_nodes[1]["label"] == "gn_t"
    assert [e["edge_id"] for e in out_edges] == ["ge_1"]


def test_nothing_changes_without_a_run_or_a_protected_card(revision, monkeypatch):
    from flowgate.graph_run import draft_restore
    from modules.flow_gate.graph_workflow.store import run_store

    nodes, edges = [_n("gn_r", "R")], []
    revision["rows"] = [{"node_key": "gn_t", "status": "pending"}]
    assert draft_restore.restored("f", nodes, edges, revision["run"]) == (nodes, edges, [])
    monkeypatch.setattr(run_store, "current_run", lambda flow_id: None)
    assert draft_restore.restored("f", nodes, edges) == (nodes, edges, [])


def test_a_missing_source_card_of_a_restored_edge_comes_back_too(revision):
    from flowgate.graph_run import draft_restore

    revision["rows"] = [{"node_key": "gn_t", "status": "open"}]
    out_nodes, out_edges, added = draft_restore.restored("f", [], [], revision["run"])
    assert added == ["gn_t", "gn_r"] and [e["edge_id"] for e in out_edges] == ["ge_1"]


# ── 1. draft_restore on the Graph ────────────────────────────────────────────

def test_a_draft_without_the_card_the_run_opens_gets_it_back_and_saves_again(env, decided):
    """the draft (R only) was saved before the run opened T; T opens -> the draft gets T back
    (draft version + 1, its key no longer retired) and a save that places a new card on it goes through."""
    from modules.flow_gate.graph_workflow.store import layout_store, run_store

    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T")], [edge("tmp_e", "tmp_r", "tmp_t")])
    r, t = first["key_map"]["tmp_r"], first["key_map"]["tmp_t"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    put_draft(env, FA, 0, [node(r, "R"), node(t, "T")])                 # draft from revision 1 (edge kept out)
    put_draft(env, FA, 1, [node(r, "R")])                               # then T left out: retired draft_removed
    assert t in layout_store.retired_keys(FA)
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    run = run_store.current_run(FA)
    rows = {row["node_key"]: row["status"] for row in run_store.node_runs(run["run_id"])}
    assert rows[t] in ("open", "review", "done"), rows
    draft = _draft()
    assert {n["node_key"] for n in draft["nodes"]} == {r, t}
    assert [(e["source"], e["target"]) for e in draft["edges"]] == [(r, t)]
    assert t not in layout_store.retired_keys(FA)
    assert any(ev.payload.get("reason") == "draft" and ev.payload.get("draft_version") == draft["draft_version"]
               for ev in env["events"] if getattr(ev, "payload", None))
    version = draft["draft_version"]
    saved = put_draft(env, FA, version, [node(r, "R"), node(t, "T"), node("tmp_new", "T")],
                      [{"edge_id": draft["edges"][0]["edge_id"], "client_key": None, "source": r, "target": t,
                        "condition": None}])
    assert saved["draft_version"] == version + 1 and "tmp_new" in saved["key_map"]


# ── 2. root_start ────────────────────────────────────────────────────────────

def _ensure(flow=FA):
    from modules.flow_gate.graph_workflow.services import root_start
    return root_start.ensure_in_guard(flow, "u_kim", "ko")


def test_registration_makes_the_root_the_flow_s_start_card_once(env):
    from flowgate.card_documents import repository as lines

    key = _ensure()
    draft = _draft()
    assert [(n["node_key"], n["node_type"], n["doc_type"], n["palette_item_id"], n["label"]) for n in draft["nodes"]] == [
        (key, "document", "R", "requirement", "요구사항")]
    assert draft["edges"] == []
    assert [r["doc_id"] for r in lines.rows(FA, [key]) if r["slot"] == "requirement"] == [f"{FA}.0001-R"]
    assert _ensure() is None and len(_draft()["nodes"]) == 1           # never a second start card


def test_an_older_flow_keeps_its_cards_and_gets_its_start_card_without_any_edge(env):
    """a draft with a card and no start card."""
    first = put_draft(env, FA, 0, [node("tmp_n", "N")])
    n = first["key_map"]["tmp_n"]
    key = _ensure()
    draft = _draft()
    assert [x["node_key"] for x in draft["nodes"]] == [n, key] and draft["edges"] == []
    assert draft["draft_version"] == 2


def test_no_start_card_for_no_root_two_roots_or_one_already_there(env):
    from modules.flow_gate.graph_workflow.services import root_start

    assert _ensure(FB) is None and _draft(FB) is None                  # no documents
    put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T")], [edge("tmp_e", "tmp_t", "tmp_r")])
    assert _ensure() is None                                            # an R card is there (even with an input edge)
    _doc(FB, 1, "R")
    _doc(FB, 2, "B")
    assert root_start.start_root(FB) is None and _ensure(FB) is None   # ambiguous


def test_startup_places_the_missing_start_cards(env):
    from modules.flow_gate.graph_workflow.services import root_start

    assert root_start.ensure_all() == [FA]
    assert root_start.ensure_all() == []


def test_the_root_registration_and_startup_call_it():
    import startup
    from modules.flow_gate import process_service

    assert "root_start.ensure_in_guard" in inspect.getsource(process_service.create_workflow_root)
    assert "repair_graph_drafts()" in inspect.getsource(startup.run_all)
    source = inspect.getsource(startup._repair_graph_drafts_in_background)
    assert "draft_restore.restore_all()" in source and "root_start.ensure_all()" in source


def test_the_chat_card_api_places_the_start_card_whatever_the_draft_holds(env, decided):
    """bug 3: a draft with a card and no start card. The start card comes first; the chat then
    names where its cards go (two cards without a successor: ambiguous, as for any Flow)."""
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    n = put_draft(env, FA, 0, [node("tmp_n", "N")])["key_map"]["tmp_n"]
    with pytest.raises(GraphApiError) as caught:
        service.place(_group(), "u_kim", {"cards": [{"label": "수정"}]}, "ko")
    assert caught.value.code == "chat_cards_anchor_ambiguous"
    start = next(x["node_key"] for x in _draft()["nodes"] if x["doc_type"] == "R")
    assert sorted(caught.value.params["candidates"]) == sorted([n, start])
    placed = service.place(_group(), "u_kim", {"cards": [{"label": "수정"}], "after": start}, "ko")
    assert placed["after"] == start and placed["root"] is None          # already there: not made again
    card, gate = placed["cards"][0]["node_key"], placed["gate"]["node_key"]
    assert sorted((e["source"], e["target"]) for e in _draft()["edges"]) == sorted([(start, card), (card, gate)])
    assert sum(1 for x in _draft()["nodes"] if x["doc_type"] == "R") == 1

@pytest.mark.parametrize("doc_type,label,palette", [("R", "요구사항", "requirement"), ("B", "버그", "bug")])
def test_new_root_names_persist_and_existing_labels_stay(env, doc_type, label, palette):
    # The document's own title is deliberately unrelated: identity is its type.
    _doc(FB, 1, doc_type)
    key = _ensure(FB)
    saved = _draft(FB)
    root = next(n for n in saved["nodes"] if n["node_key"] == key)
    assert (root["doc_type"], root["label"], root["palette_item_id"]) == (doc_type, label, palette)
    assert _ensure(FB) is None
    assert _draft(FB) == saved
    # An existing person's title is never reset by another ensure/read.
    root["label"] = "직접 정한 제목"
    from modules.flow_gate.graph_workflow.services import root_start, graph_service
    from modules.flow_gate.graph_workflow.domain import draft_request as request
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    nodes = [root_start._node_input(i, n) for i, n in enumerate(saved["nodes"])]
    with_flow_guard(FB, lambda: graph_service.write_draft(_group(FB), "u_kim", request.DraftRequest(
        draft_version=saved["draft_version"], nodes=nodes, edges=[]), None, {"R", "B", "T"}, "ko"))
    assert _ensure(FB) is None
    assert _draft(FB)["nodes"][0]["label"] == "직접 정한 제목"


def test_first_run_default_classification_preserves_custom_titles():
    from modules.flow_gate.graph_workflow.services.flow_default_names import default_title, is_default_title
    assert default_title("flow") == "새 플로우"
    assert default_title("chat") == "새 대화"
    for target, doc_type, text in [("flow", None, "새 플로우"), ("chat", None, "새 대화"),
                                  ("root", "R", "요구사항"), ("root", "B", "버그")]:
        assert is_default_title(text, target, doc_type=doc_type)
        for custom in (None, "", "직접 정한 제목", text + " "):
            assert not is_default_title(custom, target, doc_type=doc_type)
    assert not is_default_title("버그", "root", doc_type="R")
    assert not is_default_title("요구사항", "root", doc_type="B")



@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_default_titles_match_client_i18n_contract(locale):
    """Both runtimes may retain native resources, but cannot silently drift."""
    import re
    from pathlib import Path
    from modules.flow_gate.graph_workflow.services.flow_default_names import default_title

    root = Path(__file__).resolve().parents[3]
    source = (root / "client_gui" / "src" / "app" / "i18n" / f"{locale}.ts").read_text(encoding="utf-8")
    for target, key in (("flow", "flow_new_title"), ("chat", "new_title")):
        matches = re.findall(r"^\s*" + key + r":\s*'([^']*)',?\s*$", source, re.MULTILINE)
        assert matches == [default_title(target, locale)], (locale, target, matches)
