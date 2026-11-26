"""the card settings a node's run applies after the start: instruction use / omit and
the card's AI review (review AI, review count, review basis). the earlier change the earlier implementation: after review is gone and
what follows is always the former follow; a result that is not approved waits for the card's approval.

On the same real SQLite DB and routes as ``test_fg_card_settings`` (its fixtures and helpers), from the
result registration on: the AI of one card writes the card's report as the node's result — with its instruction
used after the instruction stage (the earlier change the earlier implementation: ``_in_review`` walks that stage the way the engine runs
it, ``test_fg_instruction_stage_0056`` tests the stage itself), with it omitted at once — and only that type
binds and fills its card line; when the node's AI run has ended (the AI invoke engine's run-end port, ``ai_run_end_hooks``) the card's review count starts the
v0.1 document review loop with the card's review AI, count and basis; a passed review (or no review)
approves the result and moves the node on, any other end leaves it awaiting a person's approval. A rejection
— the review's on the way to a rework or when its count is spent — is no node event, and the card is locked
while the review works.
The v0.1 AI invoke admission is replaced by a recorder (what it is asked to start); the approval, the
review loop table and the Graph contacts are the real ones. The remote source tools grant a node's real
tokens what the AI was told to write (an omitted instruction's TR: write), and every review hop's real
mention, the first one included, tells the review AI the card's review basis.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, pre_feature_attempts,
    AI, FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, get_settings, node, put_draft,
    setting,
)

# (T): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

_SERVER_DIR = Path(__file__).resolve().parents[2]
WORK_RUN, REVIEW_RUN = "air_card_work", "air_card_review"
INSTRUCTION_RUN = "air_card_instruction"


def _admin_and_reviewer(env):
    _sql(env["db"], "UPDATE users SET is_admin = 1 WHERE user_id = 'u_kim'")
    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES ('aip_review', 'aip_review', 'cli', 'claude', 't', 't')")


def _node(env, key):
    row = _sql(env["db"], "SELECT status, result_doc_id FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    return row["status"], row["result_doc_id"]


def _review_status(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id).get("doc_review_status")


def _start_ai(monkeypatch, job, run_id=WORK_RUN):
    """The node's AI start as the engine runs it: the run's token is minted (and mapped to the node) by
    the start's own issuer. Returns (the document types the mention asks for, the AI's token id)."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    heads, issued = [], []

    def fake_mention(**kw):
        heads.append(kw["head_type"])
        return "mention"

    def fake_start_run(**kw):
        issued.append(kw["issue_builder"](ai_run_id=run_id))
        return {"run_id": run_id, "status": "running"}

    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", fake_mention)
    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    ai_node_starter.start_ai_invoke(job)
    [issue] = issued
    return heads, issue["token_id"]


def _result(seq, typ):
    """A registered result document with a body (approval reads it), waiting for review."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    doc_id = _doc(FA, seq, typ)
    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {typ} {seq}\n\nbody\n", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md", "doc_review_status": "pending_review"})
    return doc_id


def _write_written(doc_id, body="## 작업\n\n지시 AI가 쓴 지시서 본문\n"):
    """A card document gets a body after its front matter (what an edit submission writes)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body.startswith("---\n") else f"---\ntitle: {doc_id}\n---\n{body}", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md"})


def _executions_ended():
    """(T0 baseline, harness): the end of the Flow's Graph executions in the the earlier implementation ledger
    (``graph_attempt_runs``), as ``terminal.cleanup`` -> ``graph_run.execution.finish`` ends them before an AI run's
    lease is released and its run-end hooks run. The recorder standing in for the AI invoke start never launches, so its
    execution is still ``requested``: it ends as the finish of a launcher that holds no lease token ends it (cancelled);
    an acquired one ends ``ended``. Without it the ledger keeps the node worked on (``ai_running``, ``LeaseLost: attempt
    already has an execution``), which these suites model by the Flow's group AI lease alone. Called once the result
    is registered (``_in_review``); an instruction stage's execution is left to ``request_write``, which hands it off
    to the result stage as the run-end hand-off does."""
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.db.connection import get_store

    rows = get_store()._fetch_all(
        "SELECT e.exec_id, e.status, e.lease_token FROM graph_attempt_runs e JOIN graph_runs r ON r.run_id = e.run_id "
        "WHERE r.group_id = ? AND e.status IN ('requested', 'running')", [FA])
    for row in rows or []:
        if row["status"] == "requested":
            ledger.cancel(row["exec_id"], None, "requested")
        else:
            ledger.finish(row["exec_id"], row["lease_token"], "ended")


def _run_ended(run_id, **fields):
    """An AI run of the card ended on its own (unless ``fields`` say how) and released its lease."""
    from modules.flow_gate.services import ai_run_end_hooks

    ai_run_end_hooks.run_ended({"run_id": run_id, "group_id": FA, "doc_ref": f"{FA}.0001-R", "action_scope": "new",
                                "mode": "single", "issued_to": "u_kim", "provider_id": "aip_assignee",
                                "outcome": "complete", "end_reason": "exited", "stop_code": None, **fields})


def _through_instruction(env, monkeypatch):
    """The instruction stage of an AI card with its instruction used, as the engine runs
    it: its instruction AI writes the card's instruction into the line's empty document (an edit start) and its
    run ends on its own; the instruction is approved and the result stage's start is queued. -> the instruction."""
    [job] = env["jobs"]
    assert (job["stage"], job["action_scope"]) == ("instruction", "edit")
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"])
    env["jobs"].clear()
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    assert _review_status(job["target_doc_id"]) == "approved"
    return job["target_doc_id"]


def _in_review(env, monkeypatch, settings=(), written="TR"):
    """An AI card with ``settings`` runs (through its instruction stage when its instruction is used); its AI
    registers a result as the inbox does (the Graph contact, then its token's consume): (node key, result, AI
    token). the earlier change the earlier implementation: the run's end reviews or approves only a result its own tokens registered."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    for field, value in settings:
        legacy_setting(env, FA, key, field, value)
    _start(env)
    if dict(settings).get("instruction") != "omit":
        _through_instruction(env, monkeypatch)
    [job] = env["jobs"]
    _heads, token = _start_ai(monkeypatch, job)
    result = _result(81, written)
    v01_contact.after_inbox_registered(FA, result, written, token)
    token_service.consume(token, "flowgate", result)
    assert _node(env, key) == ("review", result)
    _executions_ended()       # T0 harness: its process ended; an AI still working is the lease (``_lease``)
    return key, result, token


def _recorded_start_run(monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    calls = []
    monkeypatch.setattr(ai_invoke_service, "start_run",
                        lambda **kw: calls.append(kw) or {"run_id": REVIEW_RUN, "status": "running"})
    return calls


def _work_run_ended(**ending):
    """The node's AI run ended and released its lease — on its own unless ``ending`` says how it stopped
    (the finalized record: ``end_reason`` from the worker, ``stop_code`` from finalization)."""
    _run_ended(WORK_RUN, **ending)


# ── instruction use / omit: what the AI writes and what the node binds ───────

@pytest.mark.parametrize("instruction, written, other, line", [
    (None, "TR", "T", "report"), ("use", "TR", "T", "report"), ("omit", "TR", "T", "report")])
def test_instruction_use_or_omit_decides_what_the_ai_writes_and_what_the_node_binds(
        env, decided, monkeypatch, instruction, written, other, line):
    """with its instruction used the card's report is the result too, written after the
    instruction stage (walked here; ``test_fg_instruction_stage_0056`` tests the stage)."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    if instruction:
        setting(env, FA, key, "instruction", instruction)
    _start(env)
    if instruction != "omit":
        _through_instruction(env, monkeypatch)
    [job] = env["jobs"]
    assert (job["doc_type"], job["result_doc_type"], job["stage"]) == ("T", written, "result")
    heads, token = _start_ai(monkeypatch, job)
    assert heads == [written]                                            # the AI is asked for this type
    stray = _doc(FA, 80, other)
    v01_contact.after_inbox_registered(FA, stray, other, token)
    assert _node(env, key) == ("open", None)                             # the other type is not the result
    result = _doc(FA, 81, written)
    v01_contact.after_inbox_registered(FA, result, written, token)
    assert _node(env, key) == ("review", result)
    lines = {r["slot"]: r["doc_id"] for r in _sql(
        env["db"], "SELECT slot, doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ?", (FA, key))}
    assert lines[line] == result                                         # it fills its own card line
    assert [slot for slot, doc_id in lines.items() if doc_id == result] == [line]


def test_the_result_type_follows_the_card_only_for_instruction_led_ai_cards():
    from flowgate.card_settings import fields as f

    omit = {"instruction": "omit"}
    # T2 does not fold: its TR2 names the T2 it applies, and an omitted instruction leaves no T2
    assert [f.result_doc_type("document", t, omit) for t in ("N", "T", "T2", "D", "DB", "TS")] == [
        "NR", "TR", None, None, None, None]
    # instruction use (the default) -> the instruction stage, then the report is the result
    for used in ({"instruction": "use"}, {}):
        assert [f.result_doc_type("document", t, used) for t in ("N", "T", "T2", "D", "DB", "TS")] == [
            "NR", "TR", "TR2", "D", "DB", None]
        assert [f.instruction_stage("document", t, used) for t in ("N", "T", "T2", "D", "DB", "TS", "R", "WP")] == [
            "N", "T", "T2", "DS", "DS", None, None, None]
    assert f.result_doc_type("custom", None, {}) == "TR" and f.instruction_stage("custom", None, {}) == "T"
    assert [f.instruction_stage("document", t, omit) for t in ("N", "T", "D")] == [None, None, None]
    # a Custom card runs as a work instruction, so it folds like a work card
    assert f.result_doc_type("custom", None, omit) == "TR"
    assert f.run_plan(f.RUN, omit, "custom", None) == {"settings": omit, "result_doc_type": "TR", "step_timeout_sec": 14400, "restart_max": 3}
    assert f.run_plan(f.RUN, omit, "document", "T") == {"settings": omit, "result_doc_type": "TR", "step_timeout_sec": 14400, "restart_max": 3}
    assert f.run_plan(f.RUN, omit, "document", "D") == {"settings": omit, "step_timeout_sec": 14400, "restart_max": 3}
    assert f.run_plan(f.RUN, omit, "document", "T2") == {"settings": omit, "step_timeout_sec": 14400, "restart_max": 3}
    assert f.run_plan(f.RUN, {}, "document", "T") == {
        "settings": {}, "result_doc_type": "TR", "instruction": {"doc_type": "T", "provider_id": None},
        "step_timeout_sec": 14400, "restart_max": 3}


# (T) pre-feature contract: a person card's result type was its own; an assigned attempt's is its stage plan's (L-2 /, K-2) — test_fg_ai_roles_l2_manual.py::test_k2_*
def test_a_human_node_binds_its_own_type_whatever_its_card_says(pre_feature_attempts, env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact

    key = _run_one_card(env, {"kind": "human", "user_id": "u_lee", "provider_id": None})
    setting(env, FA, key, "instruction", "omit")
    _start(env)
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    report = _doc(FA, 80, "TR")
    v01_contact.after_inbox_registered(FA, report, "TR", token)
    assert _node(env, key) == ("open", None)
    result = _doc(FA, 81, "T")
    v01_contact.after_inbox_registered(FA, result, "T", token)
    assert _node(env, key) == ("review", result)


# ── the source tools follow what the node's AI was told to write ─────────────

def _run_card(env, doc_type):
    """A confirmed R -> ``doc_type`` Flow (FA) with an AI card; returns the card's node key."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", doc_type, assignee=AI)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return first["key_map"]["tmp_c"]


def _source_scopes(raw_token):
    """The scopes the remote source tools grant a worker token (their own authentication, which
    re-judges an existing grant on every call)."""
    from modules.flow_gate.db import remote_tool_grants as db_grants
    from modules.flow_gate.services import remote_tool_service

    grant = remote_tool_service._authenticate(raw_token)
    assert grant is not None
    return set(db_grants.get_scopes(grant["grant_id"]))


@pytest.mark.parametrize("doc_type, instruction, written, writes", [
    ("T", None, "T", False), ("T", "use", "T", False), ("T", "omit", "TR", True), ("N", "omit", "NR", False)])
def test_the_source_tools_follow_what_the_node_ai_was_told_to_write(
        env, decided, monkeypatch, doc_type, instruction, written, writes):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, git_service, mention_service

    monkeypatch.setattr(git_service, "ensure_worktree", lambda *_a, **_k: "ok")
    key = _run_card(env, doc_type)
    if instruction:
        setting(env, FA, key, "instruction", instruction)
    _start(env)
    [job] = env["jobs"]
    heads, issued = [], []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec",
                        lambda **kw: heads.append(kw["head_type"]) or "mention")

    def fake_start_run(**kw):
        issued.append(kw["issue_builder"](ai_run_id=WORK_RUN))          # the start's own token
        issued.append(kw["issue_builder"](ai_run_id=WORK_RUN))          # a retry's reissue, same issuer
        return {"run_id": WORK_RUN, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    ai_node_starter.start_ai_invoke(job)
    assert heads == [written, written]                                   # what the AI is asked to write
    tokens = [job["token"]["raw_token"]] + [i["raw_token"] for i in issued]   # dispatch, start, reissue
    for raw in tokens:
        scopes = _source_scopes(raw)
        assert {"read", "grep"} <= scopes
        assert ({"write", "remove"} <= scopes) is writes and ("write" in scopes) is writes
    if writes:
        # the result is bound: the node is no longer open, so its tokens are judged as before
        result = _doc(FA, 81, "TR")
        v01_contact.after_inbox_registered(FA, result, "TR", issued[0]["token_id"])
        assert _node(env, key) == ("review", result)
        # Bound edit tokens still describe a TR after registration. Their source grant
        # ends when the backing token is consumed, as the inbox does after the contact.
        from modules.flow_gate.services import remote_tool_service, token_service
        assert all("write" in _source_scopes(raw) for raw in tokens)
        for issue in [job["token"], *issued]:
            token_service.consume(issue["token_id"], "flowgate", result)
            assert remote_tool_service._authenticate(issue["raw_token"]) is None


def test_a_token_the_graph_never_mapped_is_judged_as_before(env, decided, monkeypatch):
    from modules.flow_gate.services import git_service, token_service

    monkeypatch.setattr(git_service, "ensure_worktree", lambda *_a, **_k: "ok")
    key = _run_one_card(env, AI)
    setting(env, FA, key, "instruction", "omit")
    _start(env)
    stray = token_service.issue(project="flowgate", group_id=FA, action_scope="new", doc_ref=f"{FA}.0001-R",
                                issued_to="u_kim")
    assert "write" not in _source_scopes(stray["raw_token"])               # the root R: read, as before


# ── without a review: approved at once ──────────────

@pytest.mark.parametrize("stored_human", [False, True])
def test_without_a_review_the_result_is_approved_when_the_ai_run_ends(env, decided, monkeypatch, stored_human):
    key, result, _token = _in_review(env, monkeypatch)
    if stored_human:                                                      # a card that stored human before 0056
        _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                        "VALUES (?, ?, 'after_review', '\"human\"', 't')", (FA, key))
    assert _review_status(result) == "pending_review"                     # nothing moves while the AI still runs
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    assert calls == []                                                    # no review: no review loop
    assert (_node(env, key)[0], _review_status(result)) == ("done", "approved")






# ── the card's AI review ─────────────────────────────────────────────────────

# (T) pre-feature contract: review_ai None asks the project default reviewer (assigned: wait_setup, G-9)










def _loop_row(result, stage):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    db_loops.insert({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "review_count": 2,
                     "reviewer_provider_id": "aip_review", "review_criteria": "document_type_default",
                     "rework_provider_id": "aip_assignee", "rework_timeout_sec": 3600,
                     "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "review_baseline_id": 0,
                     "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t", "deadline_at": "t",
                     "current_stage": stage})






# ── wiring ───────────────────────────────────────────────────────────────────







# Fixture-only legacy fields for the pre-the earlier implementation execution contract.
from test_fg_card_settings import legacy_setting
