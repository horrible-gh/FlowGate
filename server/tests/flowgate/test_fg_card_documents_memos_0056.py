"""flowgate.v02.0056 T#3 — the card document and memo API (0002-CH turn 5 / 6 §4 §5 §8 / 10 O3 / 13).

On the same real SQLite DB, routes and fixtures as ``test_fg_card_settings`` (card documents, card settings and the
card approval port registered as ``startup`` registers them; the card memo port and the two new routers added):

- a card line read (draft save, Canvas) tells whether its document is written (``has_body``: the one body judgement
  of the instruction stage, ``card_documents.lines.has_body``) and counts its document's review history as the v0.1
  document read shows it (AI reviews, rejections, both);
- a person writes a line that has no document: the card's own creation path, linked to the line. While the node
  runs and the line is of its result's type, the document is registered with the node's dispatch token as the inbox
  registers one, so the result binder binds it, and it waits for the card approval (never approved by itself); the
  result path is refused during the instruction stage, while the card's AI works, without text or a token; it is
  decided, made, its token claimed and its line taken under one Flow guard, so of two writes at once only one
  registers (the other leaves no document and no consume), and a line with no stored row gets one. A person's
  result and the card's AI start exclude each other under the Flow guard: an AI start that holds the group lease
  but has not mapped its token yet is refused at its mapping once the result is registered, and the run's end
  reviews and approves only a result the run's own tokens registered;
- the per-document AI invoke (``/ai-invoke/start`` edit, single, no review loop) hands the person's comment to the
  AI as next_step_message does; its run's end starts no card review and approves nothing; a group run in progress
  refuses it with the admission's own reason;
- the v0.2 card memos: a table of their own (migration 149), CRUD under the document permissions, never a
  document (no R / B closes, no AI is called); the memos turned on for AI reading open the mention of every AI run
  of the card — its instruction and result stages, its review and rework hops, the per-document AI invoke and a
  Q&A answer on its documents — as one "User memos" section, capped in length (the whole section, its one
  truncation note included); a body at its maximum of four-byte characters round-trips, and the MySQL body column
  holds it (MEDIUMTEXT in 149, and 150 widens a 149 built with TEXT).

The v0.1 AI invoke admission is replaced by recorders (what it is asked to start, the mention it is given); the
documents, approvals, tokens, the Graph contacts and the run-end port are the real ones.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

# The AI invoke route reads the server settings when it builds a mention (token_routes -> config).
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from test_fg_card_approval_0056 import LEE, _approve, _chain, _events, _lease, _node_run
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, FB, _doc, _h, _sql, _start, db_path, decided, edge, env, node, put_draft, setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, REVIEW_RUN, WORK_RUN, _admin_and_reviewer, _node, _recorded_start_run, _result, _review_status,
    _run_ended, _start_ai, _work_run_ended, _write_written,
)

_SERVER_DIR = Path(__file__).resolve().parents[2]
ROOT = f"{FA}.0001-R"
MEMO_ON, MEMO_OFF = "Use the v2 error table", "Private note"


@pytest.fixture(autouse=True)
def o3(env, permissions):
    """The card approval port, the card memo port and the two T#3 routers, as ``startup`` / ``main`` wire them."""
    from flowgate.card_approval import integration as approval
    from flowgate.card_documents import routes as document_routes
    from flowgate.card_memos import integration as memos
    from flowgate.card_memos import routes as memo_routes

    approval.register()
    memos.register()
    for module in (document_routes, memo_routes):
        env["client"].app.include_router(module.router)
    yield
    memos.unregister()
    approval.unregister()


@pytest.fixture()
def permissions(monkeypatch):
    """The RBAC answer of the T#3 routes: every permission unless ``denied`` names it."""
    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_memos import legacy_adapter as memos

    denied: set[str] = set()
    for module in (documents, memos):
        monkeypatch.setattr(module, "has_permission", lambda u, p, perm: perm not in denied)
    return denied


# ── helpers ──────────────────────────────────────────────────────────────────

def _lines(body, key):
    return {line["slot"]: line for line in body["card_documents"][key]}


def _canvas_lines(env, flow, key):
    res = env["client"].get(f"/api/v1/flows/{flow}/canvas", headers=_h())
    assert res.status_code == 200, res.text
    shown = next(n for n in res.json()["nodes"] if n["node_key"] == key)
    return {line["slot"]: line for line in shown["card_documents"]}


def _line_doc(env, flow, key, slot):
    return _sql(env["db"], "SELECT doc_id, owned FROM graph_node_documents WHERE group_id = ? AND node_key = ? "
                           "AND slot = ?", (flow, key, slot))[0]


def _doc_row(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def _body_of(env, doc_id):
    """The document's file as the v0.1 approval reads it (``_require_document_body_for_approval``)."""
    from modules.flow_gate.storage import paths as storage_paths

    doc = _doc_row(doc_id)
    path = storage_paths.resolve_storage_path(doc["file_path"], doc["project_id"], branch=doc.get("branch") or "main")
    return path.read_text(encoding="utf-8")


def _doc_created(env):
    return len(_sql(env["db"], "SELECT 1 FROM workflow_events WHERE event_type = 'doc_created'"))


def _write(env, flow, key, slot, content=None, expect=201, body=None):
    payload = body if body is not None else ({"content": content} if content is not None else None)
    res = env["client"].post(f"/api/v1/flows/{flow}/nodes/{key}/slots/{slot}/document", json=payload, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _memo(env, flow, key, body, expect=201):
    res = env["client"].post(f"/api/v1/flows/{flow}/nodes/{key}/memos", json=body, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _memos(env, flow, key, expect=200):
    res = env["client"].get(f"/api/v1/flows/{flow}/nodes/{key}/memos", headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _change(env, flow, key, memo_id, body, expect=200):
    res = env["client"].patch(f"/api/v1/flows/{flow}/nodes/{key}/memos/{memo_id}", json=body, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _delete(env, flow, key, memo_id, expect=200):
    res = env["client"].delete(f"/api/v1/flows/{flow}/nodes/{key}/memos/{memo_id}", headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _two_memos(env, key, flow=FA):
    """One memo the AI reads and one it does not."""
    _memo(env, flow, key, {"title": MEMO_ON, "body": "Every error code comes from table v2.", "ai_ref": True})
    _memo(env, flow, key, {"title": MEMO_OFF, "body": "only for me"})


def _assert_memos_lead(mention):
    assert mention.startswith("## User memos\n---\n### " + MEMO_ON + "\nEvery error code comes from table v2.")
    assert MEMO_OFF not in mention and "only for me" not in mention


def _ai_card(env, settings=()):
    """A confirmed R -> AI T card (FA) with ``settings``; returns the card's node key."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", "T", assignee=AI)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    for field, value in settings:
        setting(env, FA, key, field, value)
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return key


def _dispatch_token(env, key):
    return _sql(env["db"], "SELECT m.token_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
                           "WHERE m.node_key = ? AND t.ai_run_id IS NULL", (key,))[0]["token_id"]


def _ai_registers(result, typ, token):
    """The AI's inbox registration of ``result`` with its token, in the inbox's order: the Graph contact, then the
    token's consume (``token_consumed``, what the run's end reads to know the result is the run's own)."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    v01_contact.after_inbox_registered(FA, result, typ, token)
    token_service.consume(token, "flowgate", result)


def _recorded_approvals(monkeypatch):
    """The card's automatic approval (``card_settings.legacy_adapter.approve``), recorded instead of made."""
    from flowgate.card_settings import legacy_adapter as card_settings

    calls = []
    monkeypatch.setattr(card_settings, "approve", lambda *args, **kw: calls.append(args) or True)
    return calls


def _consumed(env):
    return [json.loads(r["metadata"]) for r in _sql(
        env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'token_consumed' ORDER BY id")]


# ── §1 the card line read: has_body and the review history counts ──────────

def test_a_line_tells_whether_its_document_is_written(env):
    from modules.flow_gate.db import documents as db_documents

    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_r", "R")])
    key = body["key_map"]["tmp_t"]
    placed = _lines(body, key)
    assert [(s, line["has_body"]) for s, line in placed.items()] == [("instruction", False), ("report", False)]
    instruction, report = placed["instruction"]["doc_id"], placed["report"]["doc_id"]
    assert _body_of(env, instruction).startswith("---\n")                 # the card's front matter only
    _write_written(report, "\n  \n\t\n")                                   # blanks after the front matter
    _write_written(instruction, "## Work\n\nwritten\n")
    shown = _canvas_lines(env, FB, key)
    assert (shown["instruction"]["has_body"], shown["report"]["has_body"]) == (True, False)
    again = _lines(put_draft(env, FB, 1, [node(key, "T"), node(body["key_map"]["tmp_r"], "R")]), key)
    assert (again["instruction"]["has_body"], again["report"]["has_body"]) == (True, False)
    db_documents.update(report, {"status": "cancelled"})                   # the line keeps no live document
    gone = _canvas_lines(env, FB, key)["report"]
    assert (gone["doc_id"], gone["has_body"], gone["history_count"]) == (None, False, 0)


def test_the_body_judgement_is_the_one_of_the_instruction_stage():
    """No second judgement: the line read asks the same function the instruction stage and the card approval ask."""
    lines_src = (_SERVER_DIR / "flowgate" / "card_documents" / "lines.py").read_text(encoding="utf-8")
    # 0064 T#2 (L0005 2.1.1): the line reads its file once for both facts; ``written`` is still the approval's own
    # judgement (``approvable_text``, the text rule ``_require_document_body_for_approval`` applies).
    assert "written, body_state = legacy.line_body_facts(shown)" in lines_src and '"has_body": written' in lines_src
    adapter_src = (_SERVER_DIR / "flowgate" / "card_documents" / "legacy_adapter.py").read_text(encoding="utf-8")
    assert "return pipeline_service.approvable_text(doc, text), state" in adapter_src
    pipeline_src = (_SERVER_DIR / "modules" / "flow_gate" / "workflow" / "pipeline_service.py").read_text(encoding="utf-8")
    assert "if not approvable_text(doc, content):" in pipeline_src
    assert lines_src.count("def has_body(") == 1 and "approvable_body" in lines_src
    for package in ("card_documents", "card_memos"):
        for path in (_SERVER_DIR / "flowgate" / package).glob("*.py"):
            if path.name != "legacy_adapter.py":
                assert "_strip_frontmatter" not in path.read_text(encoding="utf-8"), path


def test_a_line_counts_its_documents_review_history(env):
    from modules.flow_gate.db import document_reviews
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.documents.routers import documents as documents_route

    body = put_draft(env, FB, 0, [node("tmp_t", "T")])
    key = body["key_map"]["tmp_t"]
    assert {(s, line["review_count"], line["rejection_count"], line["history_count"])
            for s, line in _lines(body, key).items()} == {("instruction", 0, 0, 0), ("report", 0, 0, 0)}
    instruction = _lines(body, key)["instruction"]["doc_id"]
    for verdict in ("issues", "pass"):
        document_reviews.insert_review(instruction, 0, "u_kim", verdict, "[]", None, "2026-10-12T00:00:00+00:00")
    history = [{"rejection_id": "rej_1", "reason": "Name every error code.", "rejected_at": "t", "rejected_by": "u_kim"},
               {"rejection_id": "rej_2", "reason": "  ", "rejected_at": "t"},                 # no reason: not shown
               {"rejection_id": "rej_3", "reason": "Too long.", "rejected_at": "t", "review_id": 1}]
    db_documents.update(instruction, {"rejection_history": json.dumps(history)})
    shown = _canvas_lines(env, FB, key)
    assert (shown["instruction"]["review_count"], shown["instruction"]["rejection_count"],
            shown["instruction"]["history_count"]) == (2, 2, 4)
    assert shown["report"]["history_count"] == 0
    # the same history the v0.1 document read (GET /documents/{id}, F1's history window) shows
    _latest, reviews = documents_route._load_ai_reviews(instruction)
    rejections = documents_route._parse_doc_workflow(_doc_row(instruction))["rejection_history"]
    assert len(reviews) == 2 and len([r for r in rejections if (r.get("reason") or "").strip()]) == 2


# ── §2 a line with no document: the card's creation path, the result through the node token ─

def test_a_person_writes_a_line_that_has_no_document(env):
    from modules.flow_gate.db import documents as db_documents

    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_s", "TS")])
    key, ts = body["key_map"]["tmp_t"], body["key_map"]["tmp_s"]
    old = _lines(body, key)["instruction"]["doc_id"]
    db_documents.update(old, {"status": "cancelled"})                     # the line keeps no live document
    created_before = _doc_created(env)
    made = _write(env, FB, key, "instruction", "## Work\n\nwritten by a person\n")
    doc_id = made["doc_id"]
    assert (made["slot"], made["doc_type"], made["bound_as_result"]) == ("instruction", "T", False)
    assert (made["line"]["doc_id"], made["line"]["has_body"], made["line"]["author"]) == (
        doc_id, True, "instruction_ai_or_human")
    row = _doc_row(doc_id)
    assert (row["type_code"], row["status"], row["doc_review_status"], row["owner_id"]) == ("T", "draft", None, "u_kim")
    assert json.loads(row["meta"])["graph_card"] == {"node_key": key, "slot": "instruction", "created_empty": False}
    text = _body_of(env, doc_id)
    assert text.startswith("---\n") and f"group_id: {FB}" in text and text.endswith("## Work\n\nwritten by a person\n")
    assert _line_doc(env, FB, key, "instruction") == {"doc_id": doc_id, "owned": 1}
    assert _doc_created(env) == created_before + 1                       # logged as the card's own creations are
    assert _canvas_lines(env, FB, key)["instruction"]["doc_id"] == doc_id
    # a line with a document (written, or the card's empty draft) is edited, not written again
    refused = _write(env, FB, key, "instruction", "again", expect=409)
    assert (refused["error"]["code"], refused["error"]["params"]["doc_id"]) == ("slot_has_document", doc_id)
    draft = _lines(body, key)["report"]["doc_id"]
    assert _write(env, FB, key, "report", "x", expect=409)["error"]["params"]["doc_id"] == draft
    # no text: an empty draft of the line's type, like a placed card's
    db_documents.update(draft, {"status": "cancelled"})
    empty = _write(env, FB, key, "report")
    assert empty["line"]["has_body"] is False and _doc_row(empty["doc_id"])["type_code"] == "TR"


def test_a_line_write_is_refused_outside_the_card_s_lines(env, permissions):
    body = put_draft(env, FB, 0, [node("tmp_s", "TS"), node("tmp_w", "WP"), node("tmp_t", "T")])
    km = body["key_map"]

    def reason(key, slot, payload=None, expect=422):
        refused = _write(env, FB, key, slot, body=payload or {}, expect=expect)
        return refused["error"]["code"], refused["error"]["params"]

    code, params = reason(km["tmp_s"], "scenario")
    assert params["errors"] == [{"field": "slot", "reason": "slot_shares_a_document", "shares": "instruction"}]
    code, params = reason(km["tmp_t"], "plan")
    assert params["errors"] == [{"field": "slot", "reason": "not_a_slot_of_this_card",
                                 "slots": ["instruction", "report"]}]
    code, params = reason(km["tmp_w"], "plan", {"content": "x"})
    assert params["errors"] == [{"field": "content", "reason": "not_for_this_slot"}]
    code, params = reason(km["tmp_t"], "report", {"content": 3, "extra": 1})
    assert params["errors"] == [{"field": "extra", "reason": "unknown_field"},
                                {"field": "content", "reason": "not_a_text"}]
    assert reason("gn_nobody", "report", expect=404)[0] == "node_not_found"
    permissions.add("perm_document_create")
    code, params = reason(km["tmp_t"], "report", expect=403)
    assert (code, params["required"]) == ("flow_access_denied", "create")


def test_a_person_s_result_on_a_running_card_binds_through_its_dispatch_token_and_awaits_approval(env, decided):
    """A person's card (the result is its own type, T): the line holds the card's empty draft; the person writes the
    result there — registered with the node's dispatch token, the result binder binds it (the draft is superseded
    by it, as by any run result), and it waits for the card approval."""
    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    token = _dispatch_token(env, key)
    placeholder = _line_doc(env, FA, key, "instruction")["doc_id"]
    assert _write(env, FA, key, "instruction", "  ", expect=422)["error"]["params"]["errors"] == [
        {"field": "content", "reason": "empty_body"}]
    made = _write(env, FA, key, "instruction", "## Work\n\nthe person's result\n")
    result = made["doc_id"]
    assert made["bound_as_result"] is True and made["line"]["doc_id"] == result
    assert _node(env, key) == ("review", result) and _events(env, key) == ["opened", "result_submitted"]
    row = _doc_row(result)
    assert (row["status"], row["doc_review_status"]) == ("open", "pending_review")      # submitted, not approved
    assert _doc_row(placeholder)["superseded_by"] == result
    assert _line_doc(env, FA, key, "instruction") == {"doc_id": result, "owned": 0}       # a run result's line
    consumed = _sql(env["db"], "SELECT consumed_at FROM tokens WHERE token_id = ?", (token,))[0]["consumed_at"]
    assert consumed is not None
    assert [json.loads(r["metadata"])["doc_id"] for r in _sql(
        env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'token_consumed'")] == [result]
    assert _node_run(env, key)["awaiting_approval"] is True                              # a person approves the card
    assert _review_status(result) == "pending_review"
    approved = _approve(env, key, body={"result_doc_id": result})
    assert approved["approval"]["approved_doc_ids"] == [result] and _node(env, key) == ("done", result)
    # the node is done: its lines are a person's to edit, the report line keeps the card's draft
    assert _write(env, FA, key, "report", "x", expect=409)["error"]["code"] == "slot_has_document"


def test_a_person_s_result_on_an_ai_card_whose_ai_failed_awaits_approval(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch

    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"),))
    _start(env)
    [job] = env["jobs"]
    node_dispatch.record_ai_failure(job)                                   # the AI start failed (L0009 E-23)
    assert _node_run(env, key)["dispatch"]["error_code"] == "ai_invoke_failed"
    calls = _recorded_start_run(monkeypatch)
    made = _write(env, FA, key, "report", "## Report\n\nwritten by a person\n")
    assert made["bound_as_result"] is True and _node(env, key) == ("review", made["doc_id"])
    assert _node_run(env, key)["awaiting_approval"] is True and calls == []      # no AI review, no approval
    assert _review_status(made["doc_id"]) == "pending_review"


def test_the_result_path_is_refused_while_the_card_is_not_the_person_s_to_write(env, decided, monkeypatch):
    from modules.flow_gate.services import token_service

    _admin_and_reviewer(env)
    key = _ai_card(env)                                                    # instruction used
    _start(env)
    assert _row_stage(env, key) == "instruction"
    refused = _write(env, FA, key, "report", "x", expect=409)
    assert refused["error"]["code"] == "node_in_instruction_stage"
    _sql(env["db"], "UPDATE graph_node_runs SET stage = 'result' WHERE node_key = ?", (key,))
    [job] = env["jobs"]
    _heads, _ai_token = _start_ai(monkeypatch, {**job, "stage": "result", "action_scope": "new",
                                                "result_doc_type": "TR", "target_doc_id": None})
    _lease(monkeypatch, WORK_RUN)                                          # the card's AI works on it
    assert _write(env, FA, key, "report", "x", expect=409)["error"]["code"] == "node_ai_working"
    _lease(monkeypatch, None)
    token_service.consume(_dispatch_token(env, key), "flowgate", None)    # the person's token is spent
    assert _write(env, FA, key, "report", "x", expect=409)["error"]["code"] == "node_token_unavailable"
    assert _node(env, key) == ("open", None)


def test_two_result_writes_at_once_register_one_result_and_spend_the_token_once(env, decided, monkeypatch):
    """Two people write the result of one running card at the same moment. The node is judged, the document made,
    the dispatch token claimed and the line taken under one Flow guard: one write registers, the other is refused
    (409) and leaves nothing — no second document, no second consume of the token, no line change."""
    import threading

    from flowgate.card_documents import legacy_adapter

    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    token = _dispatch_token(env, key)
    docs_before = len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,)))
    created_before = _doc_created(env)
    both_in = threading.Barrier(2, timeout=1.0)
    judge = legacy_adapter.running_node

    def meeting(flow_id, node_key):
        """Each write waits for the other before it judges the node (a write judging outside the guard meets the
        other here and both see the token unspent; a write held at the guard lets this wait run out)."""
        try:
            both_in.wait()
        except threading.BrokenBarrierError:
            pass
        return judge(flow_id, node_key)

    monkeypatch.setattr(legacy_adapter, "running_node", meeting)
    answers: list = []

    def write(text):
        res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{key}/slots/instruction/document",
                                 json={"content": text}, headers=_h())
        answers.append((res.status_code, res.json()))

    writers = [threading.Thread(target=write, args=(f"## Work\n\nresult {n}\n",)) for n in (1, 2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(60)
    assert sorted(code for code, _body in answers) == [201, 409], answers
    (won,) = [body for code, body in answers if code == 201]
    (lost,) = [body for code, body in answers if code == 409]
    assert lost["error"]["code"] in ("node_token_unavailable", "slot_has_document")
    result = won["doc_id"]
    assert won["bound_as_result"] is True and _node(env, key) == ("review", result)
    assert len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,))) == docs_before + 1
    assert _doc_created(env) == created_before + 1
    assert [json.loads(r["metadata"]) for r in _sql(
        env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'token_consumed'")] == [
        {"token_id": token, "doc_id": result}]
    assert _line_doc(env, FA, key, "instruction") == {"doc_id": result, "owned": 0}
    assert _node_run(env, key)["awaiting_approval"] is True


def test_a_lost_token_claim_rolls_the_result_back(env, decided, monkeypatch):
    """The token is spent by another writer after the node was judged (another process, the inbox with the raw
    token): the claim fails, the write is refused and its document, file, consume and line change are rolled back."""
    from flowgate.card_documents import legacy_adapter
    from modules.flow_gate.storage import paths as storage_paths

    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    token = _dispatch_token(env, key)
    placeholder = _line_doc(env, FA, key, "instruction")
    docs_before = len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,)))
    _sql(env["db"], "UPDATE tokens SET consumed_at = '2026-10-12T00:00:00+00:00' WHERE token_id = ?", (token,))
    judge = legacy_adapter.running_node
    monkeypatch.setattr(legacy_adapter, "running_node",
                        lambda flow_id, node_key: {**judge(flow_id, node_key), "token_id": token})   # judged unspent
    files = []
    discard = legacy_adapter.discard_file

    def discarded(doc_id):
        row = _doc_row(doc_id)
        files.append((row["file_path"], row["project_id"]))
        discard(doc_id)

    monkeypatch.setattr(legacy_adapter, "discard_file", discarded)
    refused = _write(env, FA, key, "instruction", "## Work\n\nlate\n", expect=409)
    assert refused["error"]["code"] == "node_token_unavailable"
    [(file_path, project_id)] = files
    assert storage_paths.resolve_storage_path(file_path, project_id) is None            # no stray file
    assert len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,))) == docs_before
    assert _sql(env["db"], "SELECT 1 FROM workflow_events WHERE event_type = 'token_consumed'") == []
    assert _line_doc(env, FA, key, "instruction") == placeholder and _node(env, key) == ("open", None)


def test_a_person_s_result_takes_a_line_that_has_no_stored_row(env, decided):
    """The result line has no stored row at all (its type was not registered when the card was placed, or the row
    is gone): the result is bound and the line gets its row holding it, as a run result's line."""
    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    placeholder = _line_doc(env, FA, key, "instruction")["doc_id"]
    _sql(env["db"], "DELETE FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = 'instruction'",
         (FA, key))
    assert "instruction" not in _canvas_lines(env, FA, key)
    made = _write(env, FA, key, "instruction", "## Work\n\nthe person's result\n")
    result = made["doc_id"]
    assert made["bound_as_result"] is True and _node(env, key) == ("review", result)
    assert (made["line"]["slot"], made["line"]["doc_id"], made["line"]["has_body"]) == ("instruction", result, True)
    assert _line_doc(env, FA, key, "instruction") == {"doc_id": result, "owned": 0}
    assert _canvas_lines(env, FA, key)["instruction"]["doc_id"] == result
    assert _doc_row(placeholder)["superseded_by"] is None                 # no longer the line's: left as it was
    assert _node_run(env, key)["awaiting_approval"] is True and _review_status(result) == "pending_review"


def test_a_person_s_result_takes_a_line_whose_document_is_gone(env, decided):
    """The result line's row holds a cancelled document: the row is replaced by one holding the result."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    db_documents.update(_line_doc(env, FA, key, "instruction")["doc_id"], {"status": "cancelled"})
    made = _write(env, FA, key, "instruction", "## Work\n\nthe person's result\n")
    assert made["bound_as_result"] is True and made["line"]["doc_id"] == made["doc_id"]
    assert _line_doc(env, FA, key, "instruction") == {"doc_id": made["doc_id"], "owned": 0}
    assert _node(env, key) == ("review", made["doc_id"])


_REVIEWED = (("review_count", 1), ("review_ai", "aip_review"))


@pytest.mark.parametrize("settings", [(), _REVIEWED], ids=["no_review", "card_review"])
def test_a_person_s_result_written_while_the_ai_start_holds_the_lease_stops_that_start(
        env, decided, monkeypatch, settings):
    """The card's AI start took the group lease and has not mapped its token yet (so ``node_ai_working`` does not
    see it) when a person writes the result. The two run on their own threads, each held at its seam: the AI start
    between its lease and its token's mapping, the write between its commit and its Graph contact. The AI start's
    mapping guard finds the person's result registered and refuses: its token is revoked and never mapped, the AI
    does not run (the start failure is recorded as any refused start). The run's end — on its own, its lease
    released — then reviews nothing and approves nothing: the person's result waits for the card approval."""
    import threading

    from flowgate.card_documents import legacy_adapter
    from modules.flow_gate.graph_workflow.services import ai_node_starter, node_dispatch
    from modules.flow_gate.services import ai_invoke_service, mention_service

    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"), *settings))
    _start(env)
    [job] = env["jobs"]
    dispatch = _dispatch_token(env, key)
    leased, reserved, ai_done = threading.Event(), threading.Event(), threading.Event()
    refused: list = []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: "mention")
    _lease(monkeypatch, WORK_RUN)                                          # the AI start's admission took it

    def admitted(**kw):
        """The AI invoke service after its lease: the run's token is issued and mapped once the write committed."""
        leased.set()
        assert reserved.wait(30)
        kw["issue_builder"](ai_run_id=WORK_RUN)
        return {"run_id": WORK_RUN, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", admitted)

    def ai_start():
        try:
            ai_node_starter.start_ai_invoke(job)
        except ai_node_starter.NodeNotStartable as exc:                    # what node_dispatch.start_ai does
            refused.append(str(exc))
            node_dispatch.record_ai_failure(job)
        finally:
            ai_done.set()

    tell = legacy_adapter.tell_graph

    def contact_after_the_ai_start(*args):
        """The write has committed (result, consume, line); its Graph contact waits for the AI start's mapping."""
        reserved.set()
        assert ai_done.wait(30)
        tell(*args)

    monkeypatch.setattr(legacy_adapter, "tell_graph", contact_after_the_ai_start)
    starter = threading.Thread(target=ai_start)
    starter.start()
    assert leased.wait(30)
    assert _sql(env["db"], "SELECT 1 FROM tokens WHERE ai_run_id = ?", (WORK_RUN,)) == []    # nothing issued yet
    made = _write(env, FA, key, "report", "## Report\n\nwritten by a person\n")
    starter.join(30)
    assert not starter.is_alive()
    result = made["doc_id"]
    assert refused == [f"node {key} already has a registered result"]
    assert [r["revoked_at"] is not None for r in _sql(
        env["db"], "SELECT revoked_at FROM tokens WHERE ai_run_id = ?", (WORK_RUN,))] == [True]
    assert _sql(env["db"], "SELECT 1 FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
                           "WHERE t.ai_run_id = ?", (WORK_RUN,)) == []        # the AI's token was never mapped
    assert made["bound_as_result"] is True and _node(env, key) == ("review", result)
    assert _consumed(env) == [{"token_id": dispatch, "doc_id": result}]
    assert _node_run(env, key)["dispatch"]["error_code"] == "ai_invoke_failed"
    _lease(monkeypatch, None)
    calls, approvals = _recorded_start_run(monkeypatch), _recorded_approvals(monkeypatch)
    _work_run_ended()                                                      # the run ends on its own
    assert calls == [] and approvals == []                                 # no card review, no approval
    assert _node(env, key) == ("review", result) and _review_status(result) == "pending_review"
    assert _node_run(env, key)["awaiting_approval"] is True


def test_an_ai_start_that_mapped_its_token_first_refuses_the_person_s_result(env, decided, monkeypatch):
    """The other order under the Flow guard: the AI start mapped its token while its run holds the lease, so the
    person's write is refused (409 ``node_ai_working``) and leaves nothing; the AI's own result is then approved by
    its run's end as ever."""
    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"),))
    _start(env)
    [job] = env["jobs"]
    _heads, token = _start_ai(monkeypatch, job)
    _lease(monkeypatch, WORK_RUN)
    docs_before = len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,)))
    line_before = _line_doc(env, FA, key, "report")
    refused = _write(env, FA, key, "report", "## Report\n\nwritten by a person\n", expect=409)
    assert refused["error"]["code"] == "node_ai_working"
    assert len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FA,))) == docs_before
    assert _consumed(env) == [] and _line_doc(env, FA, key, "report") == line_before
    result = _result(81, "TR")
    _ai_registers(result, "TR", token)
    _lease(monkeypatch, None)
    _work_run_ended()
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"


@pytest.mark.parametrize("settings", [(), _REVIEWED], ids=["no_review", "card_review"])
def test_the_ai_run_s_end_never_takes_a_result_a_person_registered_as_its_own(env, decided, monkeypatch, settings):
    """The card's AI started (its token mapped) and let go of the lease with no result; before its end reaches the
    card (the run-end port runs after the lease's release) a person writes the result. The run's end finds the node
    in review, but with a result its tokens never registered: no card review, no approval."""
    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"), *settings))
    _start(env)
    [job] = env["jobs"]
    _start_ai(monkeypatch, job)
    _lease(monkeypatch, None)                                              # the AI run released its lease
    made = _write(env, FA, key, "report", "## Report\n\nwritten by a person\n")
    result = made["doc_id"]
    assert made["bound_as_result"] is True and _node(env, key) == ("review", result)
    calls, approvals = _recorded_start_run(monkeypatch), _recorded_approvals(monkeypatch)
    _work_run_ended()
    assert calls == [] and approvals == []
    assert _node(env, key) == ("review", result) and _review_status(result) == "pending_review"
    assert _node_run(env, key)["awaiting_approval"] is True


def _row_stage(env, key):
    return _sql(env["db"], "SELECT stage FROM graph_node_runs WHERE node_key = ?", (key,))[0]["stage"]


def test_a_line_of_another_type_on_a_running_card_is_a_plain_draft(env, decided):
    """A running person's T card: its report line (TR) is no result of the node — the plain path."""
    from modules.flow_gate.db import documents as db_documents

    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    db_documents.update(_line_doc(env, FA, key, "report")["doc_id"], {"status": "cancelled"})
    made = _write(env, FA, key, "report", "## notes\n")
    assert made["bound_as_result"] is False and _doc_row(made["doc_id"])["status"] == "draft"
    assert _node(env, key) == ("open", None)


# ── §3 the per-document AI invoke: edit, single, the comment, no review ──────

@pytest.fixture()
def invoke(env, monkeypatch):
    """``POST /ai-invoke/start`` over the real route; the token's mention body and the admission are recorded."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import token_routes
    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(token_routes, "_build_mention_for_token", lambda **kw: "STANDARD EDIT MENTION")
    starts: list[dict] = []

    def fake_start_run(**kw):
        starts.append({**kw, "mention": kw["mention_builder"]("RAW", "scratch")})
        return {"run_id": "air_edit_1", "status": "running"}

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", fake_start_run)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def post(doc_ref, **extra):
        body = {"project": "flowgate", "module": "v02", "group": FA.rsplit(".", 1)[-1], "doc_ref": doc_ref,
                "action_scope": "edit", "mode": "single", **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers={"Authorization": "Bearer tok"})

    return post, starts


def test_the_edit_invoke_hands_the_person_s_comment_to_the_ai(env, invoke):
    post, starts = invoke
    body = put_draft(env, FA, 0, [node("tmp_t", "T")])
    doc_id = _lines(body, body["key_map"]["tmp_t"])["instruction"]["doc_id"]
    res = post(doc_id, provider_id="aip_card", messages=["Keep the table short.", "  ", "Cite the R."])
    assert res.status_code == 200, res.text
    [start] = starts
    assert (start["action_scope"], start["mode"], start["doc_ref"], start["document_review_loop"]) == (
        "edit", "single", doc_id, None)
    assert start["mention"] == ("## 사용자 메세지\n---\nKeep the table short.\n\nCite the R.\n\n"
                                "STANDARD EDIT MENTION")
    starts.clear()
    assert post(doc_id).status_code == 200
    assert starts[0]["mention"] == "STANDARD EDIT MENTION"                # no comment, no memo: as before


def test_the_edit_invoke_on_a_card_document_reads_the_card_s_memos_after_the_comment(env, invoke):
    post, starts = invoke
    body = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_n", "N")])
    key, other = body["key_map"]["tmp_t"], body["key_map"]["tmp_n"]
    _two_memos(env, key)
    _memo(env, FA, other, {"title": "Another card", "body": "not this one", "ai_ref": True})
    doc_id = _lines(body, key)["report"]["doc_id"]
    assert post(doc_id, messages=["Shorter."]).status_code == 200
    mention = starts[0]["mention"]
    assert mention.startswith("## 사용자 메세지\n---\nShorter.\n\n## User memos\n---\n### " + MEMO_ON)
    assert mention.endswith("STANDARD EDIT MENTION") and "Another card" not in mention and MEMO_OFF not in mention


def test_a_group_run_in_progress_refuses_the_edit_invoke_with_its_own_reason(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import ai_invoke_routes
    from modules.flow_gate.services.ai_invoke.runtime import _http_error

    def refused(**_kw):
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id="air_someone_else")

    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", refused)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    res = TestClient(app).post("/api/v1/ai-invoke/start", headers={"Authorization": "Bearer tok"}, json={
        "project": "flowgate", "module": "v02", "group": FA.rsplit(".", 1)[-1], "doc_ref": ROOT,
        "action_scope": "edit", "mode": "single", "messages": ["x"]})
    assert res.status_code == 409
    assert res.json() == {"code": "run_in_progress", "message": "An AI run is already in progress for this group.",
                          "run_id": "air_someone_else"}


def test_an_edit_run_on_a_card_document_starts_no_review_and_approves_nothing(env, decided, monkeypatch):
    """Traced to the run's finalization: a single edit run carries no review loop, so the finalize review checkpoint
    does nothing, and its run-end (the card's port, the last finalize step) neither reviews nor approves — also on
    the node's result in review, the case a card review would otherwise take."""
    from modules.flow_gate.services.ai_invoke import finalize, review

    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"), ("review_count", 2), ("review_ai", "aip_review")))
    _start(env)
    [job] = env["jobs"]
    _heads, token = _start_ai(monkeypatch, job)
    result = _result(81, "TR")
    _ai_registers(result, "TR", token)
    assert _node(env, key) == ("review", result)
    edit_run = {"run_id": "air_edit_1", "group_id": FA, "doc_ref": result, "action_scope": "edit", "mode": "single",
                "document_review_loop": None}
    monkeypatch.setattr(review, "_checkpoint_document_review_loop",
                        lambda run: pytest.fail("a single edit run has no review loop to checkpoint"))
    finalize._finalize_review_checkpoint(edit_run)
    calls = _recorded_start_run(monkeypatch)
    _run_ended("air_edit_1", action_scope="edit", doc_ref=result)          # ended on its own, after the edit
    assert calls == [] and _review_status(result) == "pending_review" and _node(env, key) == ("review", result)
    _work_run_ended()                                                       # the node's own run: its card review
    [loop] = calls
    assert (loop["action_scope"], loop["doc_ref"]) == ("review", result)


# ── §5 the v0.2 card memos ───────────────────────────────────────────────────

def test_card_memos_are_listed_created_changed_and_deleted(env):
    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_n", "N")])
    key, other = body["key_map"]["tmp_t"], body["key_map"]["tmp_n"]
    empty = _memos(env, FB, key)
    assert {k: v for k, v in empty.items() if k != "server_ts"} == {"ok": True, "flow_id": FB, "node_key": key,
                                                                    "memos": []}
    first = _memo(env, FB, key, {"title": "  First  ", "body": "line 1\nline 2"})["memo"]
    assert (first["title"], first["body"], first["ai_ref"], first["created_by"], first["updated_by"]) == (
        "First", "line 1\nline 2", False, "u_kim", "u_kim")                  # AI reading is off by default
    assert first["memo_id"].startswith("mem_") and first["created_at"] == first["updated_at"]
    second = _memo(env, FB, key, {"title": "Second", "ai_ref": True})["memo"]
    assert (second["body"], second["ai_ref"]) == ("", True)
    _memo(env, FB, other, {"title": "Other card"})
    assert [m["title"] for m in _memos(env, FB, key)["memos"]] == ["First", "Second"]   # oldest first, this card
    changed = _change(env, FB, key, first["memo_id"], {"ai_ref": True, "body": "new"})["memo"]
    assert (changed["title"], changed["body"], changed["ai_ref"]) == ("First", "new", True)
    assert changed["updated_at"] >= first["updated_at"]
    assert _delete(env, FB, key, second["memo_id"])["deleted"] is True
    assert [m["memo_id"] for m in _memos(env, FB, key)["memos"]] == [first["memo_id"]]
    gone = _change(env, FB, key, second["memo_id"], {"title": "x"}, expect=404)
    assert gone["error"]["code"] == "memo_not_found"
    assert _delete(env, FB, other, first["memo_id"], expect=404)["error"]["code"] == "memo_not_found"  # its card's
    assert _memos(env, FB, "gn_nobody", expect=404)["error"]["code"] == "node_not_found"
    assert _memo(env, FB, "gn_nobody", {"title": "x"}, expect=404)["error"]["code"] == "node_not_found"


@pytest.mark.parametrize("payload, errors", [
    ({}, [{"field": "title", "reason": "required"}]),
    ({"title": "  "}, [{"field": "title", "reason": "required"}]),
    ({"title": "x" * 201}, [{"field": "title", "reason": "too_long", "max": 200}]),
    ({"title": "a\nb"}, [{"field": "title", "reason": "invalid_text"}]),
    ({"title": "t", "body": 3, "ai_ref": "yes", "pinned": True},
     [{"field": "pinned", "reason": "unknown_field"}, {"field": "body", "reason": "not_a_text"},
      {"field": "ai_ref", "reason": "not_a_bool"}]),
    ({"title": "t", "body": "x" * 20001}, [{"field": "body", "reason": "too_long", "max": 20000}]),
])
def test_a_memo_is_checked_before_it_is_stored(env, payload, errors):
    key = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    refused = _memo(env, FB, key, payload, expect=422)
    assert (refused["error"]["code"], refused["error"]["params"]["errors"]) == ("invalid_request", errors)
    memo_id = _memo(env, FB, key, {"title": "t"})["memo"]["memo_id"]
    assert _change(env, FB, key, memo_id, {}, expect=422)["error"]["params"]["errors"] == [
        {"field": "body", "reason": "no_change"}]
    assert _memos(env, FB, key)["memos"][0]["title"] == "t"


def test_a_memo_body_of_four_byte_characters_at_its_maximum_round_trips(env):
    """A body of ``BODY_MAX_LEN`` four-byte characters (80,000 bytes in UTF-8, over a MySQL TEXT) is accepted on
    create and on change, and reads back exactly as written."""
    from flowgate.card_memos import memos

    key = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    smile, rocket = "\U0001F600" * memos.BODY_MAX_LEN, "\U0001F680" * memos.BODY_MAX_LEN
    assert len(smile.encode("utf-8")) == 4 * memos.BODY_MAX_LEN > MYSQL_COLUMN_BYTES["TEXT"]
    made = _memo(env, FB, key, {"title": "wide", "body": smile})["memo"]
    assert made["body"] == smile and _memos(env, FB, key)["memos"][0]["body"] == smile
    changed = _change(env, FB, key, made["memo_id"], {"body": rocket})["memo"]
    assert changed["body"] == rocket and _memos(env, FB, key)["memos"][0]["body"] == rocket
    assert _sql(env["db"], "SELECT body FROM graph_node_memos WHERE memo_id = ?", (made["memo_id"],)) == [
        {"body": rocket}]


@pytest.mark.parametrize("denied, call, required", [
    ("perm_document_read", "list", "read"), ("perm_document_create", "create", "create"),
    ("perm_document_update", "change", "update"), ("perm_document_delete", "delete", "delete")])
def test_memos_follow_the_document_permissions(env, permissions, denied, call, required):
    key = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    memo_id = _memo(env, FB, key, {"title": "t"})["memo"]["memo_id"]
    permissions.add(denied)
    refused = {"list": lambda: _memos(env, FB, key, expect=403),
               "create": lambda: _memo(env, FB, key, {"title": "u"}, expect=403),
               "change": lambda: _change(env, FB, key, memo_id, {"title": "u"}, expect=403),
               "delete": lambda: _delete(env, FB, key, memo_id, expect=403)}[call]()
    assert (refused["error"]["code"], refused["error"]["params"]["required"]) == ("flow_access_denied", required)
    permissions.clear()
    assert [m["title"] for m in _memos(env, FB, key)["memos"]] == ["t"]   # nothing changed


def test_a_memo_is_no_document_closes_no_requirement_and_calls_no_ai(env, monkeypatch):
    """The v0.1 M memo closed an open R / B (``_try_close_parent_on_child_created``); a card memo is no document."""
    from modules.flow_gate.services import ai_invoke_service

    monkeypatch.setattr(ai_invoke_service, "start_run", lambda **kw: pytest.fail("a memo never calls an AI"))
    body = put_draft(env, FA, 0, [node("tmp_r", "R")])
    key = body["key_map"]["tmp_r"]
    assert _lines(body, key)["requirement"]["doc_id"] == ROOT
    before = (_doc_row(ROOT)["status"], _doc_row(ROOT)["doc_review_status"])
    docs = _sql(env["db"], "SELECT COUNT(*) AS n FROM documents")[0]["n"]
    events = _sql(env["db"], "SELECT COUNT(*) AS n FROM workflow_events")[0]["n"]
    memo_id = _memo(env, FA, key, {"title": "Scope", "body": "only the server", "ai_ref": True})["memo"]["memo_id"]
    _change(env, FA, key, memo_id, {"body": "server and API"})
    _delete(env, FA, key, memo_id)
    assert (_doc_row(ROOT)["status"], _doc_row(ROOT)["doc_review_status"]) == before == ("open", "approved")
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM documents")[0]["n"] == docs
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM workflow_events")[0]["n"] == events
    assert env["jobs"] == []
    source = "\n".join(p.read_text(encoding="utf-8") for p in (_SERVER_DIR / "flowgate" / "card_memos").glob("*.py"))
    imported = re.findall(r"^\s*(?:from|import) (modules\.flow_gate[\w.]*)", source, re.M)
    assert imported and not any(m.split(".")[2] in ("documents", "workflow") for m in imported), imported
    assert not any(".db.documents" in m or "ai_invoke" in m for m in imported) and "start_run" not in source


def test_a_disposed_flow_refuses_memo_writes(env, monkeypatch):
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    from flowgate.card_memos import legacy_adapter

    key = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]

    def disposed(flow_id):
        raise GraphApiError(409, "flow_disposed", {"flow_id": flow_id})

    monkeypatch.setattr(legacy_adapter, "require_writable", disposed)
    assert _memo(env, FB, key, {"title": "t"}, expect=409)["error"]["code"] == "flow_disposed"
    assert _memos(env, FB, key)["memos"] == []                             # reading still works


# ── the "User memos" section ─────────────────────────────────────────────────

def test_the_memo_section_carries_only_the_memos_turned_on_in_their_order():
    from flowgate.card_memos import memos

    rows = [{"title": "A", "body": "alpha", "ai_ref": True}, {"title": "B", "body": "beta", "ai_ref": False},
            {"title": "C", "body": "  ", "ai_ref": True}, {"title": "D", "body": "delta\nmore", "ai_ref": True}]
    assert memos.section(rows) == "## User memos\n---\n### A\nalpha\n\n### C\n\n### D\ndelta\nmore"
    assert memos.section([r for r in rows if not r["ai_ref"]]) == "" and memos.section([]) == ""


def test_the_memo_section_keeps_the_titles_first_and_marks_its_cut_once():
    from flowgate.card_memos import memos

    rows = [{"title": "Short", "body": "s" * 10, "ai_ref": True}, {"title": "Huge", "body": "h" * 300, "ai_ref": True},
            {"title": "Small", "body": "m" * 20, "ai_ref": True}]
    assert memos.section(rows, limit=1000).count("[Truncated") == 0                # everything fits: no note
    text = memos.section(rows, limit=200)                                     # the huge body does not fit
    assert text == ("## User memos\n---\n### Short\n" + "s" * 10 + "\n\n### Huge\n\n### Small\n" + "m" * 20 +
                    "\n\n" + memos.OMITTED_NOTE.format(limit=200, title_only=1, left_out=0))
    assert len(text) <= 200
    titles_only = memos.section(rows, limit=150)                              # not even every title fits
    assert titles_only == ("## User memos\n---\n### Short\n\n### Huge\n\n" +
                           memos.OMITTED_NOTE.format(limit=150, title_only=2, left_out=1))
    assert len(titles_only) <= 150
    assert memos.SECTION_MAX_CHARS == 8000 and memos.section(rows).count("### ") == 3


@pytest.mark.parametrize("title", ["A", "T" * 200])
def test_the_memo_section_stays_under_its_cap_with_many_memos_turned_on(title):
    """1000 memos turned on, each with a body at its maximum: the section — header, titles, separators and the one
    truncation note — is never longer than the cap; it keeps titles only, then leaves out the titles that do not
    fit, and says so once."""
    from flowgate.card_memos import memos

    rows = [{"title": title, "body": "x" * memos.BODY_MAX_LEN, "ai_ref": True} for _ in range(1000)]
    text = memos.section(rows)
    assert len(text) <= memos.SECTION_MAX_CHARS
    assert text.startswith("## User memos\n---\n### " + title) and "x" not in text
    assert text.count("[Truncated") == 1
    shown = text.count("### ")
    assert text.endswith(memos.OMITTED_NOTE.format(limit=memos.SECTION_MAX_CHARS, title_only=shown,
                                                   left_out=1000 - shown))
    assert (shown == 1000) is (title == "A")                                  # 200-character titles: some left out


# ── the memos reach every AI run of the card ─────────────────────────────────

def test_both_stages_of_the_card_run_read_its_memos(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    _admin_and_reviewer(env)
    key = _ai_card(env)
    _two_memos(env, key)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    issued: list[dict] = []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: "NODE MENTION")
    monkeypatch.setattr(ai_invoke_service, "start_run",
                        lambda **kw: issued.append(kw["issue_builder"](ai_run_id=INSTRUCTION_RUN))
                        or {"run_id": INSTRUCTION_RUN, "status": "running"})
    ai_node_starter.start_ai_invoke(job)                                    # the instruction stage
    _assert_memos_lead(issued[-1]["mention"])
    assert issued[-1]["mention"].endswith("\n\nNODE MENTION")
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    [result_job] = env["jobs"]
    monkeypatch.setattr(ai_invoke_service, "start_run",
                        lambda **kw: issued.append(kw["issue_builder"](ai_run_id=WORK_RUN))
                        or {"run_id": WORK_RUN, "status": "running"})
    ai_node_starter.start_ai_invoke(result_job)                             # the result stage
    _assert_memos_lead(issued[-1]["mention"])


def test_the_card_review_and_rework_hops_read_its_memos(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"), ("review_count", 2), ("review_ai", "aip_review")))
    _two_memos(env, key)
    _start(env)
    [job] = env["jobs"]
    _heads, token = _start_ai(monkeypatch, job)
    result = _result(81, "TR")
    _ai_registers(result, "TR", token)
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [call] = calls
    issue = call["issue_builder"]
    issue.loop_stage = "review"
    first = issue(ai_run_id=REVIEW_RUN)["mention"]
    review_body = f"Review the target document ({result})"
    assert first.index("Review basis:") < first.index("## User memos") < first.index(review_body)
    assert MEMO_ON in first and MEMO_OFF not in first
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="Name every error code.")
    issue.loop_stage = ai_invoke_service.REWORK_HOP_KIND                   # the rework hop: the run AI revises it
    rework = issue(ai_run_id=REVIEW_RUN)
    _assert_memos_lead(rework["mention"])
    assert rework["raw_token"] and rework["token_id"]


def test_a_qa_answer_on_a_card_document_reads_the_card_s_memos(env, monkeypatch):
    from modules.flow_gate.services import q_answer_invoke_service as qa

    monkeypatch.setattr(qa, "build_answer_mention", lambda **kw: "ANSWER MENTION")
    body = put_draft(env, FA, 0, [node("tmp_t", "T")])
    key = body["key_map"]["tmp_t"]
    _two_memos(env, key)
    card_doc = _doc_row(_lines(body, key)["report"]["doc_id"])
    issued = qa.issue_answer_token(doc=card_doc, item={"id": 1}, issued_to="u_kim", api_base_url="http://h/api/v1")
    _assert_memos_lead(issued["mention"])
    assert issued["mention"].endswith("\n\nANSWER MENTION")
    free = _doc_row(ROOT)                                                  # no card line shows the root here
    free_mention = qa.issue_answer_token(doc=free, item={"id": 2}, issued_to="u_kim",
                                         api_base_url="http://h/api/v1")["mention"]
    # 0064 T-B1e: the root has no body file, so the unreadable documents section leads -- but no memo section.
    assert free_mention.startswith("[읽을 수 없는 문서]\n") and free_mention.endswith("\n\nANSWER MENTION")
    assert "## User memos" not in free_mention


def test_a_document_a_card_line_holds_or_shows_finds_its_card(env, decided):
    """The cards an AI on a document reads the memos of: the line holding it, or showing it at the end of its
    supersede chain (a v0.1 revision), or a run result that took the line."""
    from modules.flow_gate.db import documents as db_documents

    from flowgate.card_documents import lines

    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_n", "N")])
    key = body["key_map"]["tmp_t"]
    report = _lines(body, key)["report"]["doc_id"]
    revised = _doc(FB, 50, "TR")
    db_documents.update(report, {"superseded_by": revised})
    assert lines.node_keys_showing(FB, report) == [key] and lines.node_keys_showing(FB, revised) == [key]
    assert lines.node_keys_showing(FB, ROOT) == [] and lines.node_keys_showing(FB, None) == []
    (run_key,) = _chain(env, [("T", LEE)])
    _start(env)
    placeholder = _line_doc(env, FA, run_key, "instruction")["doc_id"]
    made = _write(env, FA, run_key, "instruction", "## result\n")
    assert lines.node_keys_showing(FA, made["doc_id"]) == [run_key]
    assert lines.node_keys_showing(FA, placeholder) == []                  # the line took the result, not its draft


def test_without_the_memo_port_every_mention_is_as_before(env):
    from modules.flow_gate.services import card_memo_hooks

    from flowgate.card_memos import integration

    key = put_draft(env, FA, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    _two_memos(env, key)
    assert card_memo_hooks.prepend_memo_section("M", FA, node_key=key).startswith("## User memos")
    integration.unregister()
    assert card_memo_hooks.prepend_memo_section("M", FA, node_key=key) == "M"
    integration.register()
    card_memo_hooks.register(card_memo_hooks.MEMO_SECTION, lambda *a: 1 / 0)    # a failing memo read
    assert card_memo_hooks.prepend_memo_section("M", FA, node_key=key) == "M"   # never stops the AI start
    integration.register()


# ── migration 149 and wiring ─────────────────────────────────────────────────

def test_migration_149_table_trigger_and_flow_delete_cleanup(env):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert "graph_node_memos" in tables and "trg_graph_node_memos_group_deleted" in triggers
    columns = [r["name"] for r in _sql(env["db"], "PRAGMA table_info(graph_node_memos)")]
    assert columns == ["memo_id", "group_id", "node_key", "title", "body", "ai_ref", "created_by", "created_at",
                       "updated_at", "updated_by"]
    keep = put_draft(env, FA, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    gone = put_draft(env, FB, 0, [node("tmp_t", "T")])["key_map"]["tmp_t"]
    _memo(env, FA, keep, {"title": "a"})
    _memo(env, FB, gone, {"title": "b"})
    conn = sqlite3.connect(env["db"])
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FB,))
        conn.commit()
    finally:
        conn.close()
    assert [r["group_id"] for r in _sql(env["db"], "SELECT group_id FROM graph_node_memos")] == [FA]


def test_migration_149_has_the_same_table_in_three_dialects():
    found = {}
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "149_graph_node_memos.sql").read_text(encoding="utf-8")
        found[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), dialect
        assert not re.search(r"\b(ALTER|DROP|UPDATE|DELETE FROM documents)\b", sql), dialect
        assert "ai_ref" in sql and "DEFAULT 0" in sql and "idx_graph_node_memos_card" in sql, dialect
    assert found["sqlite"] == found["postgres"] == found["mysql"] == ["graph_node_memos"]
    assert [p.name for p in (_SERVER_DIR / "sql" / "migrations" / "sqlite").glob("149_*.sql")] == [
        "149_graph_node_memos.sql"]


MYSQL_COLUMN_BYTES = {"TEXT": 65_535, "MEDIUMTEXT": 16_777_215, "LONGTEXT": 4_294_967_295}
MIGRATION_150 = "150_graph_node_memos_body_mediumtext.sql"


def _statements(sql):
    """The SQL of a migration file without its comment lines (empty: a no-op)."""
    return "\n".join(line for line in sql.splitlines() if line.strip() and not line.strip().startswith("--")).strip()


def test_the_memo_body_column_holds_the_longest_body_in_every_dialect():
    """MySQL (utf8mb4, up to 4 bytes a character): a database 149 creates now and one 149 built while the body was
    TEXT (150's forward change) both keep the body in MEDIUMTEXT, which holds ``BODY_MAX_LEN`` four-byte characters;
    TEXT (65,535 bytes) does not. SQLite / PostgreSQL TEXT has no such ceiling: their 150 is an intentional no-op."""
    from flowgate.card_memos import memos

    root = _SERVER_DIR / "sql" / "migrations"
    longest = 4 * memos.BODY_MAX_LEN

    def body_type(dialect):
        sql = (root / dialect / "149_graph_node_memos.sql").read_text(encoding="utf-8")
        return re.findall(r"^\s*body\s+(\w+)\s+NOT NULL", sql, re.M)

    assert body_type("mysql") == ["MEDIUMTEXT"] and body_type("sqlite") == body_type("postgres") == ["TEXT"]
    forward = _statements((root / "mysql" / MIGRATION_150).read_text(encoding="utf-8"))
    assert forward == "ALTER TABLE graph_node_memos MODIFY COLUMN body MEDIUMTEXT NOT NULL;"
    assert MYSQL_COLUMN_BYTES["TEXT"] < longest <= MYSQL_COLUMN_BYTES["MEDIUMTEXT"]
    for dialect in ("sqlite", "postgres"):
        sql = (root / dialect / MIGRATION_150).read_text(encoding="utf-8")
        assert _statements(sql) == "" and "intentional no-op" in sql, dialect
    for dialect in ("sqlite", "postgres", "mysql"):
        assert [p.name for p in (root / dialect).glob("150_*.sql")] == [MIGRATION_150], dialect


def test_migration_150_leaves_the_sqlite_memo_table_as_149_made_it(env):
    """The sqlite 150 runs with the others and changes nothing: the same columns, the body still TEXT."""
    columns = {r["name"]: r["type"] for r in _sql(env["db"], "PRAGMA table_info(graph_node_memos)")}
    assert columns["body"] == "TEXT" and len(columns) == 10


def test_main_mounts_the_t3_routes_and_the_packages_reach_modules_only_through_their_adapters():
    import ast

    from flowgate.card_documents.routes import router as documents_router
    from flowgate.card_memos.routes import router as memos_router

    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    for name in ("card_documents", "card_memos"):
        assert f"from flowgate.{name}.routes import router as _{name}_router" in main
        assert f'app.include_router(_{name}_router, prefix=f"{{CONTEXT}}"' in main
    assert [(r.path, sorted(r.methods)) for r in documents_router.routes] == [
        ("/api/v1/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document", ["POST"])]
    assert [(r.path, sorted(r.methods)) for r in memos_router.routes] == [
        ("/api/v1/flows/{flow_id}/nodes/{node_key}/memos", ["GET"]),
        ("/api/v1/flows/{flow_id}/nodes/{node_key}/memos", ["POST"]),
        ("/api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}", ["PATCH"]),
        ("/api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}", ["DELETE"])]
    pkg = _SERVER_DIR / "flowgate" / "card_memos"
    assert {p.stem for p in pkg.glob("*.py")} == {"__init__", "memos", "repository", "routes", "legacy_adapter",
                                                  "integration"}
    for path in pkg.glob("*.py"):
        if path.stem in ("legacy_adapter", "integration", "repository"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(m.startswith("modules.") for m in imported), path.name


def test_the_t3_routes_are_no_group_document_mutations_of_the_lease_policy():
    """Like the card settings / approval routes, they are judged by their own handlers, not the v0.1 group lease."""
    from modules.flow_gate.services import mutation_policy as policy

    for path, method in (("/flowgate/api/v1/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document", "POST"),
                         ("/flowgate/api/v1/flows/{flow_id}/nodes/{node_key}/memos", "POST"),
                         ("/flowgate/api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}", "PATCH"),
                         ("/flowgate/api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}", "DELETE")):
        assert policy.classify_mutation_route(path, {method}) == ("system", "explicit_non_group_route"), path


def test_startup_registers_the_memo_port_and_the_port_is_stdlib_only():
    import ast

    import startup
    from modules.flow_gate.services import card_memo_hooks as hooks

    from flowgate.card_memos import integration

    integration.unregister()
    assert not hooks.registered(hooks.MEMO_SECTION)
    assert hooks.memo_section(FA, node_key="gn_x") == ""
    startup.start_card_memo_integration()
    assert hooks.registered(hooks.MEMO_SECTION)
    assert "start_card_memo_integration()" in (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    path = _SERVER_DIR / "modules" / "flow_gate" / "services" / "card_memo_hooks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "logging", "threading", "typing"}
    callers = sorted(p.relative_to(_SERVER_DIR).as_posix() for p in (_SERVER_DIR / "modules").rglob("*.py")
                     if "card_memo_hooks." in p.read_text(encoding="utf-8", errors="ignore")
                     and p.name != "card_memo_hooks.py")
    assert callers == ["modules/flow_gate/api/v1/ai_invoke_routes.py",
                       "modules/flow_gate/graph_workflow/services/ai_node_starter.py",
                       "modules/flow_gate/services/q_answer_invoke_service.py"]



# ── flowgate.v02.0088 T#1: a person's result is titled in the language of its own POST ───────────────────────────

@pytest.mark.parametrize("locale, name", [("ko", "지시서"), ("ja", "指示書"), ("en", "Instruction")])
def test_a_person_s_result_is_titled_in_the_language_of_its_own_request(env, decided, locale, name):
    """The result path (``_reserve_result``) is the same POST: its X-Locale titles the result; the card's empty draft
    it supersedes keeps the title it was made with."""
    _admin_and_reviewer(env)
    (key,) = _chain(env, [("T", LEE)])
    _start(env)
    placeholder = _line_doc(env, FA, key, "instruction")["doc_id"]
    before = _doc_row(placeholder)["title"]
    res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{key}/slots/instruction/document",
                             json={"content": "## Work\n\nthe person's result\n"}, headers={**_h(), "x-locale": locale})
    assert res.status_code == 201, res.text
    made = res.json()
    result = made["doc_id"]
    assert made["bound_as_result"] is True and made["line"]["doc_id"] == result
    row = _doc_row(result)
    front = _body_of(env, result).split("\n---", 1)[0]
    assert made["line"]["title"] == row["title"] == f"T · {name}" and f"\ntitle: T · {name}\n" in front + "\n"
    assert _canvas_lines(env, FA, key)["instruction"]["title"] == f"T · {name}"
    assert _doc_row(placeholder)["title"] == before == "T · 지시서"            # placed in ko, never retitled
