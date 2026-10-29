"""0111 T#4 / NR §4: hide only owned documents and this view's live card lines.

All storage is an in-memory line table. Documents/definitions are dictionaries; body/review
reads are stubbed. Real repository.rebind, CardView, supersede resolution, lines_for,
_Definition.view and canvas_service._project are exercised without touching application data.
"""
from __future__ import annotations

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from flowgate.card_documents import lines, repository
from modules.flow_gate.graph_workflow.projection.flow_snapshot import FlowSnapshot
from modules.flow_gate.graph_workflow.projection.graph_projector import GraphView, project_graph
from modules.flow_gate.graph_workflow.services import canvas_service as canvas

FLOW = "test2.default.0084"
KEY = "gn_xjk6ezy8ss4g"
ROUTES = [("graph", "draft"), ("graph", "revision"), ("conflict", "draft"),
          ("conflict", "revision"), ("unassigned", "draft")]


def doc(seq, typ, **extra):
    return {"doc_id": f"{FLOW}.{seq:04d}-{typ}", "group_id": FLOW, "type_code": typ,
            "seq": seq, "title": f"{typ} {seq}", "status": "open",
            "doc_review_status": "pending_review", **extra}


def node(key=KEY, typ="T"):
    return {"node_key": key, "node_type": "document", "doc_type": typ, "label": key}


class LineStore:
    """Only graph_node_documents lives here; unexpected persistence access fails."""

    def __init__(self, connection):
        self.connection = connection
        self.writes = 0

    def table_exists(self, table):
        assert table == repository.NODE_DOCUMENT_TABLE
        return True

    def _fetch_all(self, sql, params):
        assert sql.lstrip().startswith("SELECT ") and "graph_node_documents" in sql
        return self.connection.execute(sql, params).fetchall()

    def _execute(self, sql, params):
        self.writes += 1
        return self.connection.execute(sql, params)

    def _execute_affected(self, sql, params):
        return self._execute(sql, params).rowcount

    def snapshot(self):
        return [tuple(r) for r in self.connection.execute("SELECT * FROM graph_node_documents ORDER BY node_key, ordinal")]


@pytest.fixture()
def scenario(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE graph_node_documents (group_id TEXT, node_key TEXT, slot TEXT, "
                       "doc_type TEXT, doc_id TEXT, owned INTEGER, ordinal INTEGER, created_at TEXT, created_by TEXT)")
    store = LineStore(connection)
    s = SimpleNamespace(store=store, documents=[], draft=[node()], revision=[node()], binding=None)
    monkeypatch.setattr(repository, "get_store", lambda: store)
    monkeypatch.setattr(lines.legacy, "review_counts", lambda _ids: {})
    monkeypatch.setattr(lines.legacy, "rejection_count", lambda _doc: 0)
    monkeypatch.setattr(lines.legacy, "line_body_facts", lambda d: (bool(d), "written" if d else None))
    monkeypatch.setattr(canvas.definition_store, "draft", lambda _flow: {"nodes": s.draft, "edges": []})
    monkeypatch.setattr(canvas.definition_store, "latest_revision_no", lambda _flow: 1)

    def revision(_flow, number):
        assert number == 1
        return {"nodes": s.revision, "edges": []}

    monkeypatch.setattr(canvas.definition_store, "revision", revision)
    monkeypatch.setattr(canvas.assignment_store, "root_binding", lambda _flow: s.binding)
    monkeypatch.setattr(canvas.title_store, "states", lambda _flow: {})
    monkeypatch.setattr(canvas.card_document_ports, "card_view", lines.CardView)

    def add_line(document, key=KEY, slot="instruction", owned=False, ordinal=0):
        repository.insert(FLOW, key, slot, document["type_code"], document["doc_id"], owned, ordinal, None)

    def render(state, mode, run=None, lookup=lambda _ids: {}):
        before = copy.deepcopy(s.documents), store.snapshot(), store.writes
        snap = FlowSnapshot(FLOW, documents=s.documents)
        definition = canvas._Definition(FLOW, snap.docs_by_id)
        view = definition.view(mode)
        out = canvas._project(FLOW, state, snap, {}, set(), definition, mode, lookup=lookup, run=run)
        assert (s.documents, store.snapshot(), store.writes) == before
        keys = {n["node_key"] for n in out.nodes}
        assert all(e["source"] in keys and e["target"] in keys for e in out.edges)
        assert set(out.free_docs) == {n["node_key"] for n in out.nodes if n["identity_source"] == "document"}
        return out, view, definition.cards

    s.add_line, s.render = add_line, render
    try:
        yield s
    finally:
        connection.close()


def by_key(out):
    return {n["node_key"]: n for n in out.nodes}


def instruction(out):
    return next(line for line in by_key(out)[KEY]["card_documents"] if line["slot"] == "instruction")


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_1_0084_owned_zero_instruction_rebind(scenario, state, mode):
    s = scenario
    root, old, report, new = doc(1, "R"), doc(2, "T"), doc(3, "TR"), doc(4, "T")
    old["superseded_by"] = new["doc_id"]
    new["triggered_by"] = root["doc_id"]
    new["title"] = "TaskTable status filter"
    s.documents = [root, old, report, new]
    s.draft = s.revision = [node("gn_root", "R"), node()]
    s.binding = {"node_key": "gn_root", "doc_id": root["doc_id"]}
    s.add_line(root, "gn_root", "requirement")
    s.add_line(old, owned=True)
    s.add_line(report, slot="report", owned=True, ordinal=1)
    assert repository.rebind(FLOW, KEY, "instruction", "T", new["doc_id"]) == 1
    assert next(r for r in repository.rows(FLOW) if r["doc_id"] == new["doc_id"])["owned"] == 0
    out, view, cards = s.render(state, mode)
    assert new["doc_id"] not in cards.hidden_doc_ids  # owned-only policy is unchanged
    assert f"doc:{new['doc_id']}" not in by_key(out)
    assert instruction(out)["doc_id"] == new["doc_id"]
    assert by_key(out)[KEY]["document"]["doc_id"] == new["doc_id"]
    assert f"doc:{old['doc_id']}" not in by_key(out)
    assert any(e["relation"] == "triggered_by" and e["source"] == "gn_root" and e["target"] == KEY
               for e in out.edges)


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_2_supersede_live_successor_is_only_a_card_line(scenario, state, mode):
    s = scenario
    old, new = doc(2, "T"), doc(4, "T")
    old["superseded_by"] = new["doc_id"]
    s.documents = [old, new]
    s.add_line(old)
    out, view, cards = s.render(state, mode)
    assert cards.hidden_doc_ids == frozenset()
    assert instruction(out)["doc_id"] == new["doc_id"]
    assert out.free_docs == []
    assert f"doc:{old['doc_id']}" not in by_key(out)


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_3_non_owned_line_of_card_removed_from_draft(scenario, state, mode):
    s = scenario
    d = doc(4, "T")
    s.documents, s.draft = [d], []
    s.add_line(d)
    out, view, cards = s.render(state, mode)
    if mode == "draft":
        assert out.free_docs == [f"doc:{d['doc_id']}"]
        assert KEY not in by_key(out)
    else:
        assert instruction(out)["doc_id"] == d["doc_id"]
        assert out.free_docs == []


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_4_old_kind_slot_stays_free_in_retyped_view(scenario, state, mode):
    s = scenario
    d = doc(4, "T")
    s.documents, s.draft = [d], [node(typ="TS")]
    s.add_line(d)
    out, view, cards = s.render(state, mode)
    if mode == "draft":
        assert out.free_docs == [f"doc:{d['doc_id']}"]
        assert by_key(out)[KEY]["card_documents"] == []
    else:
        assert instruction(out)["doc_id"] == d["doc_id"]
        assert out.free_docs == []


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_5_independent_document_and_references_use_drawn_endpoints(scenario, state, mode):
    s = scenario
    d, free = doc(4, "T"), doc(5, "M")
    d["triggered_by"] = free["doc_id"]
    free["target_id"] = d["doc_id"]
    s.documents = [d, free]
    s.add_line(d)
    out, view, cards = s.render(state, mode)
    free_key = f"doc:{free['doc_id']}"
    assert out.free_docs == [free_key]
    assert {(e["relation"], e["source"], e["target"]) for e in out.edges} == {
        ("triggered_by", free_key, KEY), ("target", KEY, free_key)}
    assert out.external_relations == []


@pytest.mark.parametrize("state,mode", ROUTES)
def test_nr4_6_owned_document_of_absent_card_is_never_free(scenario, state, mode):
    s = scenario
    owned, free = doc(3, "TR"), doc(5, "M")
    s.documents, s.draft, s.revision = [owned, free], [], []
    s.add_line(owned, key="gn_past", slot="report", owned=True)
    out, view, cards = s.render(state, mode)
    assert cards.hidden_doc_ids == frozenset({owned["doc_id"]})
    assert out.free_docs == [f"doc:{free['doc_id']}"]


@pytest.mark.parametrize("mode", ["draft", "revision"])
def test_direct_projector_uses_only_current_nodes_lines(mode):
    d, unrelated = doc(4, "T"), doc(5, "M")
    view = GraphView(mode, nodes=[node()], card_docs={
        KEY: [{"doc_id": d["doc_id"]}, {"doc_id": None}],
        "gn_absent": [{"doc_id": unrelated["doc_id"]}],
    })
    out = project_graph(FLOW, "graph", [d, unrelated], view)
    assert out.free_docs == [f"doc:{unrelated['doc_id']}"]
    assert by_key(out)[KEY]["card_documents"][0]["doc_id"] == d["doc_id"]


@pytest.mark.parametrize("mode", ["draft", "revision"])
@pytest.mark.parametrize("line_first", [True, False])
def test_shared_root_line_and_bound_card_emit_each_reference_once(mode, line_first):
    root, free = doc(1, "R"), doc(5, "M")
    root["triggered_by"] = free["doc_id"]
    free["target_id"] = root["doc_id"]
    rows = [node("gn_other", "R"), node("gn_root", "R")]
    view = GraphView(mode, nodes=rows if line_first else list(reversed(rows)),
                     bound_node_key="gn_root", bound_doc_id=root["doc_id"],
                     card_docs={"gn_other": [{"slot": "requirement", "doc_id": root["doc_id"]}]})
    out = project_graph(FLOW, "graph", [root, free], view)
    assert out.free_docs == [f"doc:{free['doc_id']}"]
    assert len(out.edges) == len({e["edge_id"] for e in out.edges}) == 2
    assert {(e["relation"], e["source"], e["target"]) for e in out.edges} == {
        ("triggered_by", f"doc:{free['doc_id']}", "gn_root"),
        ("target", "gn_root", f"doc:{free['doc_id']}")}


@pytest.mark.parametrize("mode", ["draft", "revision"])
def test_instruction_line_is_absorbed_even_when_report_is_primary(scenario, mode):
    s = scenario
    d, report, free = doc(4, "T"), doc(3, "TR"), doc(5, "M")
    free["target_id"] = d["doc_id"]
    s.documents = [d, report, free]
    s.add_line(d)
    s.add_line(report, slot="report", owned=True, ordinal=1)
    run = {"revision_no": 1, "node_runs": [{"node_key": KEY, "status": "review", "result_doc_id": report["doc_id"]}]}
    out, view, cards = s.render("graph", mode, run=run)
    assert out.free_docs == [f"doc:{free['doc_id']}"]
    if mode == "revision":
        assert by_key(out)[KEY]["document"]["doc_id"] == report["doc_id"]
    assert instruction(out)["doc_id"] == d["doc_id"]
    assert [(e["source"], e["target"]) for e in out.edges] == [(KEY, f"doc:{free['doc_id']}")]


@pytest.mark.parametrize("state,mode", ROUTES)
def test_reference_rebuild_keeps_external_and_superseded_document_metadata(scenario, state, mode):
    s = scenario
    old, successor, free = doc(2, "T"), doc(4, "T"), doc(5, "M")
    foreign = {**doc(6, "M"), "doc_id": "other.default.0001.0006-M", "group_id": "other.default.0001"}
    old["superseded_by"] = successor["doc_id"]
    free["triggered_by"], free["target_id"] = old["doc_id"], foreign["doc_id"]
    s.documents = [old, successor, free]
    s.add_line(old)
    out, view, cards = s.render(state, mode, lookup=lambda ids: {
        foreign["doc_id"]: foreign} if foreign["doc_id"] in ids else {})
    assert out.free_docs == [f"doc:{free['doc_id']}"]
    assert out.edges == []
    assert {(r["relation"], r["node_key"], r["other_doc_id"], r["other_flow_id"], r["other_title"])
            for r in out.external_relations} == {
        ("triggered_by", f"doc:{free['doc_id']}", old["doc_id"], FLOW, old["title"]),
        ("target", f"doc:{free['doc_id']}", foreign["doc_id"], foreign["group_id"], foreign["title"]),
    }
