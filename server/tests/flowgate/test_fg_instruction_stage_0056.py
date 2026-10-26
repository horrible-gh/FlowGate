"""flowgate.v02.0056 T#2 — the instruction AI and the two stages of a card run (0002-CH turn 5 / 6 §3 / 10 O2).

On the same real SQLite DB, routes and fixtures as ``test_fg_card_settings`` (card documents, card settings and the
card approval port registered as ``startup`` registers them):

- the run cards (N, T, T2, Custom, D / P / L / DB) keep an ``instruction_ai``; it falls back to the run AI, then
  to the node's assignee AI; a test card (TS) has none, and with the instruction omitted it is not read;
- an AI card with its instruction used runs the instruction stage first. Whether that stage runs is decided by
  the body of the card's instruction, not by the document existing: no document -> the instruction AI writes a
  new one; the line's document without a body (its front matter only, or blanks) -> the instruction AI writes
  into that document (an edit start, no new document); a body -> no AI, the instruction is approved and the
  result stage starts. The one body judgement is ``card_documents.lines.has_body``;
- the instruction's AI run that ended on its own with a written instruction approves it at once with no review
  (the card's review count is the result's) and starts the result stage, whose mention carries the instruction
  as a reference document; a stage that failed (stopped, nothing written) records ``ai_invoke_failed`` on the
  node as an AI start failure does and never starts the result stage;
- the result binder takes only the result stage's document as the node's result: an instruction registered by
  the instruction stage is recorded and fills its line, with no node event and no propagation; a report the
  instruction stage registered stays behind the stage's end (the move to the result stage, or its failure) —
  no contact, run read or resume binds it later; neither does one whose registration was still on its way when the
  stage ended (the inbox contacts the Graph before it consumes the token; a PostgreSQL commit can land late): the
  instruction AI's token is mapped as the instruction stage's, and the node's dispatch token is marked so by a
  contact during the stage, while a person's report with the dispatch token after a failed stage still binds;
- a card run opened before migration 148 (its node run has no stage) finishes with the type its AI was told;
- instruction omitted runs the result stage only; a stopped run ([다음 단계]) still finishes both stages of a
  card it opened; each node run tells its stage; the instruction line reads "instruction AI or a person".

The v0.1 AI invoke admission is replaced by recorders (what it is asked to start, the mention it is given); the
documents, approvals, tokens, the Graph contacts and the run-end port are the real ones.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from test_fg_card_approval_0056 import _approve, _current, _events, _node_run, _resume, _run_id, _stop
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, get_settings, node, put_draft,
    setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, WORK_RUN, _admin_and_reviewer, _node, _recorded_start_run, _result, _review_status, _run_ended,
    _source_scopes, _start_ai, _through_instruction, _work_run_ended, _write_written,
)

_SERVER_DIR = Path(__file__).resolve().parents[2]
ROOT = f"{FA}.0001-R"


@pytest.fixture(autouse=True)
def card_approval_port():
    """The card approval's Graph port, registered as ``startup`` registers it."""
    from flowgate.card_approval import integration

    integration.register()
    yield
    integration.unregister()


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(env, key):
    return _sql(env["db"], "SELECT status, result_doc_id, stage, instruction_doc_id, dispatch_error_code "
                           "FROM graph_node_runs WHERE node_key = ?", (key,))[0]


def _line(env, key, slot):
    return _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, slot))[0]["doc_id"]


def _count(env, typ):
    return _sql(env["db"], "SELECT COUNT(*) AS n FROM documents WHERE group_id = ? AND type_code = ?",
                (FA, typ))[0]["n"]


def _one_job(env):
    [job] = env["jobs"]
    env["jobs"].clear()
    return job


def _card(env, doc_type="T", settings=(), node_type="document"):
    """A confirmed R -> card Flow (FA) with an AI card and its ``settings``; returns the card's node key."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", doc_type, node_type=node_type, assignee=AI)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    for field, value in settings:
        setting(env, FA, key, field, value)
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return key


def _record_start(monkeypatch, job, run_id):
    """Start ``job`` as the engine does, recording the mention's arguments and the start: (mention kwargs,
    start kwargs, the run's token row)."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    mentions, starts, issued = [], [], []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec",
                        lambda **kw: mentions.append(kw) or "mention")

    def fake_start_run(**kw):
        starts.append(kw)
        issued.append(kw["issue_builder"](ai_run_id=run_id))
        return {"run_id": run_id, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    ai_node_starter.start_ai_invoke(job)
    [mention], [start], [issue] = mentions, starts, issued
    return mention, start, issue


def _token(env, token_id):
    return _sql(env["db"], "SELECT action_scope, doc_ref, provider_id, ai_run_id FROM tokens WHERE token_id = ?",
                (token_id,))[0]


def _register(env, doc_id, typ, token_id, contact=True):
    """The AI's inbox registration of ``doc_id`` with its token (the token's consume event; the Graph contact
    unless ``contact`` is False — a lost contact)."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    token_service.consume(token_id, "flowgate", doc_id)
    if contact:
        v01_contact.after_inbox_registered(FA, doc_id, typ, token_id)


def _register_as_the_inbox(env, doc_id, typ, token_id, contact=True, before_consume=None):
    """The inbox registration in the inbox's own order (``inbox_routes._handle_new``): the Graph contact first
    (unless ``contact`` is False — lost, or blind to a document not committed yet), then ``before_consume`` (what
    lands while the request is still on its way: an AI run's end, say), then the token's consume event."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    if contact:
        v01_contact.after_inbox_registered(FA, doc_id, typ, token_id)
    if before_consume is not None:
        before_consume()
    token_service.consume(token_id, "flowgate", doc_id)


def _token_stage(env, token_id):
    return _sql(env["db"], "SELECT stage FROM graph_run_token_map WHERE token_id = ?", (token_id,))[0]["stage"]


def _blank(doc_id, body):
    """The card's line document keeps no body: its front matter and ``body`` (blanks)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: T\ntitle: {doc_id}\n---\n{body}", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md"})


# ── the instruction AI setting ───────────────────────────────────────────────

def test_the_instruction_ai_is_a_setting_of_the_run_cards_only():
    from flowgate.card_settings import fields as f

    run = [x.name for x in f.fields_of(f.RUN)]
    assert run[:3] == ["instruction", "instruction_ai", "run_ai"] and f.field_of(f.RUN, "instruction_ai").kind == f.AI
    assert f.field_of(f.RUN, "instruction_ai").default is None              # None: the run AI, then the assignee
    assert all(f.field_of(kind, "instruction_ai") is None for kind in (f.TEST, f.REQUIREMENT, f.CHECK))
    cards = [("document", t) for t in ("N", "T", "T2", "D", "P", "L", "DB")] + [("custom", None)]
    assert all(f.card_kind(*card) == f.RUN for card in cards)
    assert [f.instruction_stage(*card, {}) for card in cards] == ["N", "T", "T2", "DS", "DS", "DS", "DS", "T"]
    assert [f.result_doc_type(*card, {}) for card in cards] == ["NR", "TR", "TR2", "D", "P", "L", "DB", "TR"]
    # a card run opened before migration 148 (one stage): its AI writes the node's own type, or the omitted report
    assert [f.result_doc_type(*card, {}, one_stage=True) for card in cards] == [None] * len(cards)
    assert [f.result_doc_type(*card, {"instruction": "omit"}, one_stage=True) for card in cards] == [
        "NR", "TR", None, None, None, None, None, "TR"]
    assert f.instruction_stage("document", "TS", {}) is None                # TS: no instruction AI (reorganized later)
    assert [f.instruction_provider(s) for s in ({}, {"run_ai": "r"}, {"instruction_ai": "i", "run_ai": "r"})] == [
        None, "r", "i"]
    # instruction omitted: no instruction stage, the instruction AI is not read
    plan = f.run_plan(f.RUN, {"instruction": "omit", "instruction_ai": "i"}, "document", "T")
    assert plan == {"settings": {"instruction": "omit", "instruction_ai": "i"}, "result_doc_type": "TR"}


def test_the_instruction_ai_is_read_and_written_like_any_card_setting(env):
    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES ('aip_instr', 'aip_instr', 'cli', 'claude', 't', 't')")
    first = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_d", "D"), node("tmp_x", None, node_type="custom"),
                                   node("tmp_s", "TS")])
    km = first["key_map"]
    for ref in ("tmp_t", "tmp_d", "tmp_x"):
        body = get_settings(env, FA, km[ref])
        assert body["fields"]["instruction_ai"] == {"value": None, "stored": False, "default": None, "choices": None}
    saved = setting(env, FA, km["tmp_t"], "instruction_ai", "aip_instr")
    assert saved["fields"]["instruction_ai"]["value"] == "aip_instr" and saved["fields"]["instruction_ai"]["stored"]
    refused = setting(env, FA, km["tmp_t"], "instruction_ai", "aip_nobody", expect=422)
    assert refused["error"]["params"]["errors"] == [{"field": "value", "reason": "unknown_ai"}]
    ts = setting(env, FA, km["tmp_s"], "instruction_ai", "aip_instr", expect=422)
    assert ts["error"]["params"]["errors"] == [
        {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": "test"}]
    assert "instruction_ai" not in get_settings(env, FA, km["tmp_s"])["fields"]
    back = setting(env, FA, km["tmp_t"], "instruction_ai", None)
    assert back["fields"]["instruction_ai"] == {"value": None, "stored": False, "default": None, "choices": None}


@pytest.mark.parametrize("settings, instruction_ai, run_ai", [
    ((), "aip_assignee", "aip_assignee"),
    ((("run_ai", "aip_card"),), "aip_card", "aip_card"),
    ((("instruction_ai", "aip_instr"),), "aip_instr", "aip_assignee"),
    ((("instruction_ai", "aip_instr"), ("run_ai", "aip_card")), "aip_instr", "aip_card"),
])
def test_the_instruction_ai_falls_back_to_the_run_ai_then_the_assignee(
        env, decided, monkeypatch, settings, instruction_ai, run_ai):
    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES ('aip_instr', 'aip_instr', 'cli', 'claude', 't', 't')")
    _admin_and_reviewer(env)
    key = _card(env, "T", settings)
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["provider_id"]) == ("instruction", instruction_ai)
    _mention, start, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    assert start["provider_id"] == instruction_ai and _token(env, issue["token_id"])["provider_id"] == instruction_ai
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)
    assert (result_job["stage"], result_job["provider_id"]) == ("result", run_ai)
    _mention, start, _issue = _record_start(monkeypatch, result_job, WORK_RUN)
    assert start["provider_id"] == run_ai
    assert _row(env, key)["stage"] == "result"


# ── the instruction stage: decided by the body, not by the document existing ─

@pytest.mark.parametrize("blank", [None, "\n   \n\t\n"])
def test_an_instruction_without_a_body_is_written_by_the_instruction_ai_into_its_document(
        env, decided, monkeypatch, blank):
    """No body (the front matter the card wrote, or blanks after it): the instruction AI writes into the line's
    document — an edit start on it, the Flow's root as a reference, no new document."""
    _admin_and_reviewer(env)
    key = _card(env, "T", (("review_count", 2), ("review_ai", "aip_review")))
    instruction = _line(env, key, "instruction")
    if blank is not None:
        _blank(instruction, blank)
    before = _count(env, "T")
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["action_scope"], job["target_doc_id"], job["result_doc_type"]) == (
        "instruction", "edit", instruction, "T")
    row = _row(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"], row["result_doc_id"]) == (
        "open", "instruction", instruction, None)
    mention, start, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    assert (start["action_scope"], start["doc_ref"], start["mode"]) == ("edit", instruction, "single")
    assert (mention["action_scope"], mention["head_type"], mention["parent_doc"]["doc_id"], mention["ref_doc_ids"]) == (
        "edit", "T", instruction, [ROOT])
    token = _token(env, issue["token_id"])
    assert (token["action_scope"], token["doc_ref"], token["ai_run_id"]) == ("edit", instruction, INSTRUCTION_RUN)
    assert _count(env, "T") == before                                    # no new document

    # the instruction AI wrote it and its run ended on its own: approved at once, no review, the result stage
    _write_written(instruction)
    calls = _recorded_start_run(monkeypatch)
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=instruction)
    assert calls == []                                                   # no review loop on the instruction
    assert _review_status(instruction) == "approved"
    row = _row(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"], row["result_doc_id"]) == (
        "open", "result", instruction, None)
    assert _events(env, key) == ["opened"]                               # no node event, no propagation
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_propagations WHERE node_key = ?", (key,))[0]["n"] == 0
    result_job = _one_job(env)
    assert (result_job["stage"], result_job["result_doc_type"], result_job["ref_doc_ids"]) == (
        "result", "TR", [instruction])

    # the result stage: the instruction as a reference document; only its report is the result, reviewed
    mention, start, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    assert (start["action_scope"], start["doc_ref"]) == ("new", ROOT)
    assert (mention["head_type"], mention["parent_doc"]["doc_id"], mention["ref_doc_ids"]) == ("TR", ROOT, [instruction])
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    assert _node(env, key) == ("review", result) and _line(env, key, "report") == result
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [loop] = calls
    assert (loop["action_scope"], loop["doc_ref"], loop["document_review_loop"]["review_count"]) == (
        "review", result, 2)                                             # the review is the result's alone


@pytest.mark.parametrize("submitted", [False, True])
def test_a_written_instruction_skips_the_instruction_ai_and_is_approved(env, decided, monkeypatch, submitted):
    """A body already (a person's or an AI's, saved in the editor or submitted): no instruction AI; the
    instruction is approved and the result stage starts with it."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    key = _card(env, "T")
    instruction = _line(env, key, "instruction")
    _write_written(instruction, "## 작업\n\n사람이 쓴 지시서\n")
    if submitted:
        db_documents.update(instruction, {"status": "open", "doc_review_status": "pending_review"})
    _start(env)
    job = _one_job(env)                                                   # the result stage's, no instruction AI
    assert (job["stage"], job["ref_doc_ids"], job["result_doc_type"]) == ("result", [instruction], "TR")
    assert _review_status(instruction) == "approved"
    row = _row(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"]) == ("open", "result", instruction)
    assert _events(env, key) == ["opened"]


@pytest.mark.parametrize("contact", [True, False])
def test_with_no_instruction_document_the_instruction_ai_writes_a_new_one(env, decided, monkeypatch, contact):
    """No document on the instruction line: the instruction AI writes the instruction type as a new document
    (registered with its token); it takes the line, binds nothing and moves no node — also when the
    registration's contact was lost (found by the node's tokens when the run ends)."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    key = _card(env, "T")
    old = _line(env, key, "instruction")
    db_documents.update(old, {"status": "cancelled"})                    # the line keeps no live document
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["action_scope"], job["target_doc_id"], job["ref_doc_ids"]) == (
        "instruction", "new", None, None)
    mention, start, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    assert (start["action_scope"], start["doc_ref"], mention["head_type"]) == ("new", ROOT, "T")
    stray = _doc(FA, 70, "TR")
    _register(env, stray, "TR", issue["token_id"])                       # a report in the instruction stage
    assert _node(env, key) == ("open", None)                             # is no result
    written = _doc(FA, 71, "T")
    _write_written(written)
    _register(env, written, "T", issue["token_id"], contact=contact)
    row = _row(env, key)
    assert (row["status"], row["result_doc_id"], row["stage"]) == ("open", None, "instruction")
    assert row["instruction_doc_id"] == (written if contact else None)
    assert _events(env, key) == ["opened"]
    _run_ended(INSTRUCTION_RUN)
    assert _review_status(written) == "approved" and _line(env, key, "instruction") == written
    row = _row(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", written)
    assert _one_job(env)["ref_doc_ids"] == [written]


def test_instruction_omitted_runs_only_the_result_stage(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T", (("instruction", "omit"), ("instruction_ai", "aip_card")))
    instruction = _line(env, key, "instruction")
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["result_doc_type"], job["ref_doc_ids"], job["provider_id"]) == (
        "result", "TR", None, "aip_assignee")
    row = _row(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", None)
    _mention, _start_kw, issue = _record_start(monkeypatch, job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert _review_status(instruction) is None                           # the instruction is never written


@pytest.mark.parametrize("doc_type, node_type, instruction_type, report", [
    ("N", "document", "N", "NR"), ("D", "document", "DS", "D"), ("DB", "document", "DS", "DB"),
    (None, "custom", "T", "TR"),
])
def test_each_run_card_writes_its_instruction_then_its_report(
        env, decided, monkeypatch, doc_type, node_type, instruction_type, report):
    """A design card now writes its DS before its design document, a research card its N before its NR, a
    Custom card its T before its TR."""
    _admin_and_reviewer(env)
    key = _card(env, doc_type, node_type=node_type)
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["result_doc_type"], job["action_scope"]) == ("instruction", instruction_type, "edit")
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)
    assert (result_job["stage"], result_job["result_doc_type"], result_job["ref_doc_ids"]) == (
        "result", report, [job["target_doc_id"]])
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, report)
    _register(env, result, report, issue["token_id"])
    assert _node(env, key) == ("review", result)
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert (_review_status(job["target_doc_id"]), _review_status(result)) == ("approved", "approved")


# ── failure, stop, approval left to the card ────────────────────────────────

@pytest.mark.parametrize("ending, written", [
    ({"end_reason": "cancelled", "stop_code": "cancelled"}, True),
    ({"end_reason": "timeout", "stop_code": "timeout"}, False),
    ({}, False),                                                         # ended on its own, nothing written
])
def test_a_failed_instruction_stage_records_the_ai_failure_and_never_starts_the_result(
        env, decided, monkeypatch, ending, written):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    if written:
        _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"], **ending)
    assert env["jobs"] == []                                              # no result stage
    row = _row(env, key)
    assert (row["status"], row["stage"], row["dispatch_error_code"]) == ("open", "instruction", "ai_invoke_failed")
    assert _events(env, key) == ["opened", "dispatch_failed"]             # the AI start failure path
    assert _review_status(job["target_doc_id"]) is None                  # nothing approved
    assert _node_run(env, key)["dispatch"]["error_code"] == "ai_invoke_failed"
    # L0009 E-23: the node's token is a person's again; their result binds
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    result = _result(82, "TR")
    _register(env, result, "TR", token)
    assert _node(env, key) == ("review", result)


def _new_instruction_start(env, monkeypatch, stray=True):
    """An AI T card whose instruction line keeps no live document: its instruction AI starts new and (``stray``),
    before its instruction, registers a report (a body, waiting for review) with its token. -> (key, job, AI token,
    report or None)."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    key = _card(env, "T")
    db_documents.update(_line(env, key, "instruction"), {"status": "cancelled"})
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["action_scope"]) == ("instruction", "new")
    _mention, _start_kw, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    assert _token_stage(env, issue["token_id"]) == "instruction"          # mapped as the instruction stage's
    assert _token_stage(env, job["token"]["token_id"]) is None            # the dispatch token: no stage yet
    if not stray:
        return key, job, issue["token_id"], None
    report = _result(70, "TR")
    _register(env, report, "TR", issue["token_id"])
    assert _node(env, key) == ("open", None)
    return key, job, issue["token_id"], report


@pytest.mark.parametrize("contact", [True, False])
def test_a_report_the_instruction_stage_registered_never_becomes_the_result(env, decided, monkeypatch, contact):
    """After the move to the result stage the instruction stage's report stays behind: a run read's reconciliation
    and a resume bind nothing, and the result AI's own report is the node's result — on its contact, or found by
    the reconciliation after a lost one."""
    key, _job, token, stray = _new_instruction_start(env, monkeypatch)
    written = _doc(FA, 71, "T")
    _write_written(written)
    _register(env, written, "T", token)
    _run_ended(INSTRUCTION_RUN)
    result_job = _one_job(env)
    assert _row(env, key)["stage"] == "result" and _review_status(written) == "approved"
    shown = _node_run(env, key)                                         # a run read reconciles first
    assert (shown["status"], shown["result_doc_id"], shown["stage"]) == ("open", None, "result")
    _stop(env)
    assert _resume(env)["status"] == "running"                           # a resume reconciles too
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"], contact=contact)
    if not contact:
        assert _node(env, key) == ("open", None)
        assert _node_run(env, key)["result_doc_id"] == result           # the reconciliation finds the real one
    assert _node(env, key) == ("review", result) and _line(env, key, "report") == result
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert (_review_status(result), _review_status(stray)) == ("approved", "pending_review")


@pytest.mark.parametrize("contact", [True, False])
def test_a_report_a_failed_instruction_stage_registered_never_becomes_the_result(env, decided, monkeypatch, contact):
    """The instruction stage failed after its AI registered a report: the failure keeps that report behind the
    stage — a run read or a resume does not put the node in review — and the report a person registers with the
    node's token (L0009 E-23) is the result, on its contact or by the reconciliation after a lost one."""
    key, job, _token, stray = _new_instruction_start(env, monkeypatch)
    _run_ended(INSTRUCTION_RUN, end_reason="timeout", stop_code="timeout")
    assert env["jobs"] == []
    shown = _node_run(env, key)
    assert (shown["status"], shown["result_doc_id"], shown["dispatch"]["error_code"]) == (
        "open", None, "ai_invoke_failed")
    _stop(env)
    assert _resume(env)["status"] == "running"
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    result = _result(82, "TR")
    _register(env, result, "TR", job["token"]["token_id"], contact=contact)      # a person, the dispatch token
    if not contact:
        assert _node_run(env, key)["result_doc_id"] == result
    assert _node(env, key) == ("review", result) and _review_status(stray) == "pending_review"


def test_the_inbox_contacts_the_graph_before_it_consumes_the_token():
    """The order ``_register_as_the_inbox`` follows: a new registration reaches the result binder before its token is
    consumed (its ``token_consumed`` event comes after the contact), so a stage can end between the two."""
    src = (_SERVER_DIR / "modules" / "flow_gate" / "api" / "inbox_routes.py").read_text(encoding="utf-8")
    start = src.index("def _handle_new(")
    body = src[start:src.index("\ndef ", start + 1)]
    contact = body.index("_graph_contact.after_inbox_registered(")
    assert body.count("token_service.consume(") == 1 and contact < body.index("token_service.consume(")


@pytest.mark.parametrize("result_contact", [True, False])
@pytest.mark.parametrize("arrival", ["contact_then_end", "end_then_contact", "committed_after_end"])
@pytest.mark.parametrize("end", ["result", "timeout", "cancelled"])
def test_a_report_the_instruction_ai_registered_as_its_stage_ended_never_becomes_the_result(
        env, decided, monkeypatch, end, arrival, result_contact):
    """The instruction AI's report is still on its way when its stage ends — the move to the result stage, or a
    timeout / cancel that fails it — in the inbox's order: its contact before the end and its consume after
    (``contact_then_end``), the whole request after the end (``end_then_contact``), or a registration whose contact
    saw nothing and whose consume lands after the end (``committed_after_end``: a PostgreSQL commit after the
    boundary). Its token is the instruction stage's, so no contact, run read or resume binds it; the node's real
    result — the result AI's report, or after a failure a person's with the dispatch token (L0009 E-23) — binds on
    its contact or by the reconciliation after a lost one."""
    key, job, token, _none = _new_instruction_start(env, monkeypatch, stray=False)
    if end == "result":
        written = _doc(FA, 71, "T")
        _write_written(written)
        _register_as_the_inbox(env, written, "T", token)
    ending = {} if end == "result" else {"end_reason": end, "stop_code": end}

    def stage_end():
        _run_ended(INSTRUCTION_RUN, **ending)

    stray = _result(70, "TR")
    if arrival == "contact_then_end":
        _register_as_the_inbox(env, stray, "TR", token, before_consume=stage_end)
    else:
        stage_end()
        _register_as_the_inbox(env, stray, "TR", token, contact=arrival == "end_then_contact")
    row = _row(env, key)
    if end == "result":
        assert (row["stage"], row["dispatch_error_code"]) == ("result", None)
        result_job = _one_job(env)
    else:
        assert (row["stage"], row["dispatch_error_code"]) == ("instruction", "ai_invoke_failed")
        assert env["jobs"] == []
    shown = _node_run(env, key)                                         # a run read reconciles first
    assert (shown["status"], shown["result_doc_id"]) == ("open", None)
    _stop(env)
    assert _resume(env)["status"] == "running"                           # a resume reconciles too
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    assert _token_stage(env, token) == "instruction"
    if end == "result":
        _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
        real = issue["token_id"]
        assert _token_stage(env, real) == "result"
    else:
        real = job["token"]["token_id"]                                  # a person, the dispatch token
        assert _token_stage(env, real) is None
    result = _result(81, "TR")
    _register_as_the_inbox(env, result, "TR", real, contact=result_contact)
    if not result_contact:
        assert _node(env, key) == ("open", None)
        assert _node_run(env, key)["result_doc_id"] == result           # the reconciliation finds the real one
    assert _node(env, key) == ("review", result) and _review_status(stray) == "pending_review"


def test_a_report_a_person_registered_as_the_instruction_stage_failed_stays_behind_it(env, decided, monkeypatch):
    """A person registers a report with the node's dispatch token while the instruction AI still works; the stage
    times out between the registration's contact and its consume. The contact marked the dispatch token as the
    instruction stage's, so the late consume binds nothing — on a run read or a resume — as the contact judged."""
    key, job, _token, _none = _new_instruction_start(env, monkeypatch, stray=False)
    dispatch = job["token"]["token_id"]
    early = _result(72, "TR")
    _register_as_the_inbox(env, early, "TR", dispatch, before_consume=lambda: _run_ended(
        INSTRUCTION_RUN, end_reason="timeout", stop_code="timeout"))
    assert _row(env, key)["dispatch_error_code"] == "ai_invoke_failed"
    assert _token_stage(env, dispatch) == "instruction"
    shown = _node_run(env, key)
    assert (shown["status"], shown["result_doc_id"]) == ("open", None)
    _stop(env)
    assert _resume(env)["status"] == "running"
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)


def _before_migration_148(env):
    """The node runs as a run opened before migration 148 leaves them: the columns did not exist, and the real
    migration adds them to that schema, empty."""
    conn = sqlite3.connect(env["db"])
    try:
        for column in ("stage", "instruction_doc_id", "result_event_floor"):
            conn.execute(f"ALTER TABLE graph_node_runs DROP COLUMN {column}")
        conn.execute("ALTER TABLE graph_run_token_map DROP COLUMN stage")
        conn.commit()
        conn.executescript((_SERVER_DIR / "sql" / "migrations" / "sqlite" / "148_graph_node_run_stage.sql").read_text(
            encoding="utf-8"))
    finally:
        conn.close()


@pytest.mark.parametrize("contact", [True, False])
@pytest.mark.parametrize("instruction, written, other", [("use", "T", "TR"), ("omit", "TR", "T")])
def test_a_card_run_opened_before_migration_148_finishes_with_the_type_its_ai_was_told(
        env, decided, monkeypatch, instruction, written, other, contact):
    """A card run whose AI started under the one-stage contract before migration 148 (instruction use: the node's
    own T; omitted: its TR): after 148 its node run has no stage, and the document its AI was told to write is
    still its result — on the contact, or by a run read's reconciliation after a lost one — and the AI run's end
    approves it; the other type binds nothing."""
    _admin_and_reviewer(env)
    key = _card(env, "T", (("instruction", instruction),))
    _start(env)
    job = _one_job(env)
    told = {**job, "stage": "result", "instruction": None, "action_scope": "new", "target_doc_id": None,
            "ref_doc_ids": None, "result_doc_type": written}            # what the dispatch before 148 queued
    mention, _start_kw, issue = _record_start(monkeypatch, told, WORK_RUN)
    assert mention["head_type"] == written
    _before_migration_148(env)
    row = _row(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"]) == ("open", None, None)
    assert _token_stage(env, issue["token_id"]) is None
    _register(env, _result(80, other), other, issue["token_id"])
    assert _node_run(env, key)["result_doc_id"] is None                  # the other type is no result
    result = _result(81, written)
    _register(env, result, written, issue["token_id"], contact=contact)
    if not contact:
        assert _node_run(env, key)["result_doc_id"] == result
    assert _node(env, key) == ("review", result)
    _work_run_ended()                                                    # the run end approves it
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"


def test_a_stopped_run_finishes_both_stages_of_a_card_it_opened(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    _stop(env)                                                           # 다음 단계: the card is open
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)                                           # the result stage starts while stopped
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()
    run = _current(env)
    node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (run["status"], node_run["status"], node_run["stage"], node_run["instruction_doc_id"]) == (
        "stopped", "done", "result", job["target_doc_id"])
    assert (run["pending_propagation_count"], run["pending_node_keys"]) == (1, [key])
    assert _resume(env)["status"] == "completed"


def test_an_instruction_the_issuer_cannot_approve_is_left_to_the_card_approval(env, decided, monkeypatch):
    """Without the issuer's ``document.approve`` the instruction is not approved, the result stage still starts,
    and the card waits for its approval, which approves the instruction first."""
    from modules.flow_gate.workflow.routers import workflow

    _admin_and_reviewer(env)
    real = workflow._get_user_permissions
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda u: set())   # the run's issuer
    key = _card(env, "T")
    _start(env)
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    instruction = job["target_doc_id"]
    _write_written(instruction)
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=instruction)
    assert _review_status(instruction) is None
    result_job = _one_job(env)
    assert result_job["ref_doc_ids"] == [instruction]
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()                                                    # not approved automatically either
    assert _node(env, key) == ("review", result) and _node_run(env, key)["awaiting_approval"] is True
    monkeypatch.setattr(workflow, "_get_user_permissions", real)
    body = _approve(env, key)
    assert body["approval"]["approved_doc_ids"] == [instruction, result] and _node(env, key) == ("done", result)


# ── reads: stage, line author, source tools ─────────────────────────────────

def test_the_node_run_tells_its_stage_and_its_instruction(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    instruction = _line(env, key, "instruction")
    shown = _node_run(env, key)
    assert (shown["status"], shown["stage"], shown["instruction_doc_id"], shown["awaiting_approval"]) == (
        "open", "instruction", instruction, False)
    _through_instruction(env, monkeypatch)
    shown = _node_run(env, key)
    assert (shown["stage"], shown["instruction_doc_id"]) == ("result", instruction)
    canvas = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h()).json()
    card = next(n for n in canvas["nodes"] if n["node_key"] == key)
    assert (card["node_run"]["stage"], card["node_run"]["instruction_doc_id"]) == ("result", instruction)


def test_the_instruction_line_is_written_by_the_instruction_ai_or_a_person(env):
    first = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_s", "TS"), node("tmp_x", None, node_type="custom")])
    km, lines = first["key_map"], first["card_documents"]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_t"]]] == [
        ("instruction", "instruction_ai_or_human"), ("report", "run")]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_x"]]] == [
        ("instruction", "instruction_ai_or_human"), ("report", "run")]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_s"]]] == [
        ("instruction", "human"), ("scenario", "run"), ("report", "run")]            # TS: a person's
    from flowgate.card_documents import slots

    assert (slots.author_of("instruction", "T"), slots.author_of("instruction", "DS"),
            slots.author_of("instruction", "TS"), slots.author_of("requirement", "R"), slots.author_of("report", "TR")) == (
        "instruction_ai_or_human", "instruction_ai_or_human", "human", "human", "run")
    # a node without a document of its own shows its instruction first, as it showed a person's line
    from modules.flow_gate.graph_workflow.projection.graph_projector import first_line_document

    by_id = {"d_i": {"doc_id": "d_i"}, "d_r": {"doc_id": "d_r"}}
    assert first_line_document([{"doc_id": "d_r", "author": "run"}, {"doc_id": "d_i",
                                "author": "instruction_ai_or_human"}], by_id) == {"doc_id": "d_i"}


def test_the_source_tools_judge_a_node_token_by_its_stage(env, decided, monkeypatch):
    """The instruction stage writes the instruction (T: read only); the result stage writes the report (TR: the
    source write scopes), for the dispatch token as well as the run's own."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "ensure_worktree", lambda *_a, **_k: "ok")
    _admin_and_reviewer(env)
    _card(env, "T")
    _start(env)
    job = _one_job(env)
    dispatch_raw = job["token"]["raw_token"]
    _mention, _start_kw, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    for raw in (dispatch_raw, issue["raw_token"]):
        assert "write" not in _source_scopes(raw)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    for raw in (dispatch_raw, issue["raw_token"]):
        assert {"write", "remove"} <= _source_scopes(raw)


# ── the one body judgement ───────────────────────────────────────────────────

def test_the_body_judgement_is_one_function_the_v02_cards_share(env):
    from flowgate.card_approval import approval, legacy_adapter
    from flowgate.card_documents import lines

    first = put_draft(env, FA, 0, [node("tmp_t", "T")])
    instruction = first["card_documents"][first["key_map"]["tmp_t"]][0]["doc_id"]
    from modules.flow_gate.db import documents as db_documents

    doc = lambda: db_documents.get_by_id(instruction)                     # noqa: E731
    assert lines.has_body(None) is False
    assert lines.has_body(doc()) is False                                # the card's draft: front matter only
    _blank(instruction, "\n \t \n")
    assert lines.has_body(doc()) is False                                # blanks only
    _blank(instruction, "\n# 지시\n\n본문\n")
    assert lines.has_body(doc()) is True
    db_documents.update(instruction, {"file_path": None})
    assert lines.has_body(doc()) is False                                # no readable file
    # the card approval asks the same function (no judgement of its own)
    assert not hasattr(legacy_adapter, "has_body")
    src = (_SERVER_DIR / "flowgate" / "card_approval" / "approval.py").read_text(encoding="utf-8")
    assert "card_lines.has_body(" in src
    settings_src = (_SERVER_DIR / "flowgate" / "card_settings" / "settings.py").read_text(encoding="utf-8")
    assert "card_lines.has_body(" in settings_src


def test_the_stage_ports_are_registered_and_answer_nothing_without_a_handler():
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert ports.instruction_document("f", {"node_key": "k"}) == {}
        assert ports.approve_instruction("f", {"node_key": "k"}, "d", "u") is False
        integration.register()
        assert ports.registered(ports.INSTRUCTION_DOCUMENT) and ports.registered(ports.APPROVE_INSTRUCTION)
    finally:
        integration.unregister()


def test_migration_148_adds_the_stage_columns_in_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "148_graph_node_run_stage.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE graph_node_runs ADD COLUMN stage" in sql
        assert "ALTER TABLE graph_node_runs ADD COLUMN instruction_doc_id" in sql
        assert "ALTER TABLE graph_node_runs ADD COLUMN result_event_floor" in sql
        assert "ALTER TABLE graph_run_token_map ADD COLUMN stage" in sql
