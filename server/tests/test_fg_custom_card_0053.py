"""flowgate.v02.0053 T#8 — a Custom card (palette [+], Graph ``custom`` node) is a plain document card.

Whatever Palette category it was added to, a Custom card works like 조사 · 작업: the Graph validator does
not report it as ``rule_undecided/custom_node``, a run needs for it what a work card needs (``report_pair``,
never ``custom_node``), the run hands it out like a document card as a work instruction (T) and binds that
result to its 지시서 line, and the Canvas titles it with the name a person gave it. Placing it makes its
지시서 · 레포트 documents (T#1). On the same real SQLite DB and fixtures as ``test_fg_card_documents``.
"""
from __future__ import annotations

from test_fg_card_documents import (  # noqa: F401 — pytest fixtures and helpers of the card documents tests
    FA, _doc, _h, _sql, canvas, db_path, decided, doc, edge, env, line_rows, lines, node, put_draft,
)

NAME = "API 오류 코드 설계"
LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}


def _graph(nodes, edges=()):
    from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode

    return Graph(nodes=tuple(GraphNode(key=k, node_type=t, doc_type=d, label=k, palette_item_id=p) for k, t, d, p in nodes),
                 edges=tuple(GraphEdge(f"ge_{s}_{t}", s, t) for s, t in edges))


# ── domain ───────────────────────────────────────────────────────────────────

def test_a_custom_card_is_checked_like_a_document_card_not_as_an_undecided_rule():
    from modules.flow_gate.graph_workflow.domain import policies
    from modules.flow_gate.graph_workflow.domain.graph_validator import validate_graph

    custom = _graph([("gn_r", "document", "R", None), ("gn_c", "custom", None, "custom.1")], [("gn_r", "gn_c")])
    work = _graph([("gn_r", "document", "R", None), ("gn_c", "document", "T", "task")], [("gn_r", "gn_c")])
    assert validate_graph(custom, policies.current()) == [] == validate_graph(work, policies.current())
    # a gate still waits on Q-11; the Custom card next to it is not reported
    mixed = _graph([("gn_r", "document", "R", None), ("gn_c", "custom", None, "custom.2"), ("gn_g", "gate", None, "confirm")],
                   [("gn_r", "gn_c"), ("gn_c", "gn_g")])
    issues = validate_graph(mixed, policies.current())
    assert [(i["code"], i["structure"], i["node_keys"]) for i in issues] == [("rule_undecided", "gate_node", ["gn_g"])]


def test_a_custom_card_needs_what_a_work_card_needs():
    from modules.flow_gate.graph_workflow.domain import run_policies as rp

    custom = _graph([("gn_r", "document", "R", None), ("gn_c", "custom", None, "custom.1")], [("gn_r", "gn_c")])
    work = _graph([("gn_r", "document", "R", None), ("gn_c", "document", "T", "task")], [("gn_r", "gn_c")])
    assert rp.required_policies(custom, "gn_r", "approved") == rp.required_policies(work, "gn_r", "approved") == {
        "start_node", "completion", "rejection_path", "report_pair"}


def test_the_run_type_of_a_custom_card_is_its_work_instruction():
    from modules.flow_gate.graph_workflow.domain.graph_model import CUSTOM_RUN_DOC_TYPE, run_doc_type

    from flowgate.card_documents.slots import CUSTOM_DOC_TYPES

    assert CUSTOM_RUN_DOC_TYPE == CUSTOM_DOC_TYPES[0] == "T"
    assert (run_doc_type("custom", None), run_doc_type("document", "N"), run_doc_type("gate", None)) == ("T", "N", None)


# ── placed, confirmed and run on a real DB ───────────────────────────────────

def _place(env, assignee=LEE, palette="custom.1"):
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", None, NAME, node_type="custom", palette=palette,
                                                              assignee=assignee)],
                     [edge("tmp_e", "tmp_r", "tmp_c")])
    return body, body["key_map"]["tmp_c"]


def test_a_placed_custom_card_has_its_documents_and_no_validation_issue(env):
    body, key = _place(env, palette="custom.3")                       # added to any category: the same card
    assert lines(body, key) == [("instruction", "T"), ("report", "TR")]
    instruction, report = (line["doc_id"] for line in body["card_documents"][key])
    assert doc(instruction)["type_code"] == "T" and doc(report)["type_code"] == "TR"
    checked = env["client"].post(f"/api/v1/flows/{FA}/graph/draft/validate", json={"draft_version": 1}, headers=_h())
    assert checked.status_code == 200, checked.text
    assert checked.json()["confirmable"] is True and checked.json()["issues"] == []
    shown = {n["node_key"]: n for n in canvas(env, FA)["nodes"]}[key]
    assert shown["title"] == NAME and shown["graph_node"]["node_type"] == "custom"
    assert shown["document"]["doc_id"] == instruction                  # Q&A and attachments have their document


def test_a_custom_card_runs_and_binds_its_result_like_a_work_card(env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact

    body, key = _place(env)
    instruction, _report = (line["doc_id"] for line in body["card_documents"][key])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    run = {n["node_key"]: n for n in started.json()["run"]["node_runs"]}
    assert run[key]["status"] == "open"
    assert run[key]["dispatch"] == {"token_issued": True, "mention_id": None, "issued_to": "u_lee"}
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    v01_contact.after_inbox_registered(FA, _doc(FA, 90, "TR"), "TR", token)    # another type: not its result
    assert _sql(env["db"], "SELECT status, result_doc_id FROM graph_node_runs WHERE node_key = ?", (key,)) == [
        {"status": "open", "result_doc_id": None}]
    result = _doc(FA, 91, "T")
    v01_contact.after_inbox_registered(FA, result, "T", token)
    assert _sql(env["db"], "SELECT status, result_doc_id FROM graph_node_runs WHERE node_key = ?", (key,)) == [
        {"status": "review", "result_doc_id": result}]
    assert [(r["slot"], r["doc_id"]) for r in line_rows(env, FA, key)][0] == ("instruction", result)
    assert doc(instruction)["superseded_by"] == result               # the placed draft leads to the result
    shown = {n["node_key"]: n for n in canvas(env, FA)["nodes"]}[key]
    assert shown["document"]["doc_id"] == result and shown["title"] == NAME   # the name, not the result's title


def test_an_ai_custom_card_starts_its_ai_on_the_work_instruction(env, decided):
    from modules.flow_gate.graph_workflow.services import node_dispatch

    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES ('claude-code', 'T8 test provider', 'cli', 'claude', 't', 't')")
    jobs = []
    node_dispatch.set_ai_starter(jobs.append)
    _body, key = _place(env, assignee={"kind": "ai", "user_id": None, "provider_id": "claude-code"})
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    dispatch = {n["node_key"]: n for n in started.json()["run"]["node_runs"]}[key]["dispatch"]
    assert dispatch == {"token_issued": True, "mention_id": None, "issued_to": "claude-code"}
    assert [(j["node_key"], j["doc_type"], j["result_doc_type"], j["provider_id"]) for j in jobs] == [
        (key, "T", "T", "claude-code")]
