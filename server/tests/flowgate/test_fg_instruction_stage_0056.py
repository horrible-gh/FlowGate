"""the instruction AI and the two stages of a card run (CH / 6 / 10 O2).

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

import json
import sqlite3
from pathlib import Path

import pytest

from test_fg_card_approval_0056 import _current, _events, _node_run, _resume, _run_id, _stop
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, pre_feature_attempts,
    AI, FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, get_settings, node, put_draft,
    setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, WORK_RUN, _admin_and_reviewer, _node, _recorded_start_run, _result, _review_status, _run_ended,
    _source_scopes, _start_ai, _through_instruction, _work_run_ended, _write_written,
)
# (T): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

_SERVER_DIR = Path(__file__).resolve().parents[2]
ROOT = f"{FA}.0001-R"


@pytest.fixture(autouse=True)
def card_outputs_port():
    """(T-3): the card's required outputs port (``card_completion_ports.pending_outputs``),
    registered as ``startup`` registers it — it replaced the removed card approval port."""
    from flowgate.card_completion import outputs
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    ports.register(ports.PENDING_OUTPUTS, outputs.pending_outputs)
    yield
    ports.unregister(ports.PENDING_OUTPUTS)


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
        legacy_setting(env, FA, key, field, value)
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
    assert plan == {"settings": {"instruction": "omit", "instruction_ai": "i"}, "result_doc_type": "TR",
                    "step_timeout_sec": 14400, "restart_max": 3}


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
    assert not saved["fields"]["instruction_ai"]["stored"]
    assert saved["ai_roles"]["roles"]["instruction"]["selected"] == {"kind": "assigned", "provider_id": "aip_instr"}
    refused = setting(env, FA, km["tmp_t"], "instruction_ai", "aip_nobody", expect=422)
    assert refused["error"]["params"]["errors"] == [{"field": "value", "reason": "ai_not_selectable", "cause": "deleted"}]
    ts = setting(env, FA, km["tmp_s"], "instruction_ai", "aip_instr", expect=422)
    assert ts["error"]["params"]["errors"] == [
        {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": "test"}]
    assert "instruction_ai" not in get_settings(env, FA, km["tmp_s"])["fields"]
    back = setting(env, FA, km["tmp_t"], "instruction_ai", None, expect=422)
    assert back["error"]["params"]["errors"] == [{"field": "value", "reason": "role_unset_not_allowed"}]


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








# ── failure, stop, approval left to the card ────────────────────────────────



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


# (T) pre-feature contract: a card run opened before migration 148 (no stage) is a pre-feature attempt by construction (L-2, G-14)






# ── reads: stage, line author, source tools ─────────────────────────────────







# ── the one body judgement ───────────────────────────────────────────────────







# Fixture-only legacy fields for the pre-the earlier implementation execution contract.
from test_fg_card_settings import legacy_setting
