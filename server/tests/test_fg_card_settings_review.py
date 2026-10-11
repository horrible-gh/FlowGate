"""flowgate.v02.0053 T#2 — the card settings a node's run applies after the start: instruction use / omit,
the card's AI review (review AI, review count, review basis) and after review (follow / human).

On the same real SQLite DB and routes as ``test_fg_card_settings`` (its fixtures and helpers), from the
result registration on: the AI of one card writes the node's own type with its instruction used and the
card's report with it omitted, and only that type binds and fills its card line; when the node's AI run
has ended (the AI invoke engine's run-end port, ``ai_run_end_hooks``) the card's review count starts the
v0.1 document review loop with the card's review AI, count and basis, and after review decides whether a
passed review (or no review) approves the result and moves the node on, or leaves it to a person. While
the review works, its rejection on the way to a rework is not the node's outcome and the card is locked.
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
    AI, FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, get_settings, node, put_draft,
    setting,
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
WORK_RUN, REVIEW_RUN = "air_card_work", "air_card_review"


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


def _in_review(env, monkeypatch, settings=(), written="T"):
    """An AI card with ``settings`` runs; its AI registers a result: (node key, result, AI token)."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    for field, value in settings:
        setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    _heads, token = _start_ai(monkeypatch, job)
    result = _result(81, written)
    v01_contact.after_inbox_registered(FA, result, written, token)
    assert _node(env, key) == ("review", result)
    return key, result, token


def _recorded_start_run(monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    calls = []
    monkeypatch.setattr(ai_invoke_service, "start_run",
                        lambda **kw: calls.append(kw) or {"run_id": REVIEW_RUN, "status": "running"})
    return calls


def _work_run_ended():
    from modules.flow_gate.services import ai_run_end_hooks

    ai_run_end_hooks.run_ended({"run_id": WORK_RUN, "group_id": FA, "doc_ref": f"{FA}.0001-R", "action_scope": "new",
                                "mode": "single", "issued_to": "u_kim", "provider_id": "aip_assignee",
                                "outcome": "complete"})


# ── instruction use / omit: what the AI writes and what the node binds ───────

@pytest.mark.parametrize("instruction, written, other, line", [
    (None, "T", "TR", "instruction"), ("use", "T", "TR", "instruction"), ("omit", "TR", "T", "report")])
def test_instruction_use_or_omit_decides_what_the_ai_writes_and_what_the_node_binds(
        env, decided, monkeypatch, instruction, written, other, line):
    from modules.flow_gate.graph_workflow.api import v01_contact

    key = _run_one_card(env, AI)
    if instruction:
        setting(env, FA, key, "instruction", instruction)
    _start(env)
    [job] = env["jobs"]
    assert (job["doc_type"], job["result_doc_type"]) == ("T", written)
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
    assert f.result_doc_type("document", "T", {"instruction": "use"}) is None
    assert f.result_doc_type("document", "T", {}) is None and f.result_doc_type("custom", None, {}) is None
    # flowgate.v02.0053 T#8: a Custom card runs as a work instruction, so it folds like a work card
    assert f.result_doc_type("custom", None, omit) == "TR"
    assert f.run_plan(f.RUN, omit, "custom", None) == {"settings": omit, "result_doc_type": "TR"}
    assert f.run_plan(f.RUN, omit, "document", "T") == {"settings": omit, "result_doc_type": "TR"}
    assert f.run_plan(f.RUN, omit, "document", "D") == {"settings": omit}
    assert f.run_plan(f.RUN, omit, "document", "T2") == {"settings": omit}


def test_a_human_node_binds_its_own_type_whatever_its_card_says(env, decided):
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
        assert all("write" not in _source_scopes(raw) for raw in tokens)


def test_a_token_the_graph_never_mapped_is_judged_as_before(env, decided, monkeypatch):
    from modules.flow_gate.services import git_service, token_service

    monkeypatch.setattr(git_service, "ensure_worktree", lambda *_a, **_k: "ok")
    key = _run_one_card(env, AI)
    setting(env, FA, key, "instruction", "omit")
    _start(env)
    stray = token_service.issue(project="flowgate", group_id=FA, action_scope="new", doc_ref=f"{FA}.0001-R",
                                issued_to="u_kim")
    assert "write" not in _source_scopes(stray["raw_token"])               # the root R: read, as before


# ── after review without a review: follow / human ────────────────────────────

@pytest.mark.parametrize("after_review, status, review", [
    ("follow", "done", "approved"), ("human", "review", "pending_review"), (None, "review", "pending_review")])
def test_without_a_review_follow_approves_the_result_when_the_ai_run_ends(
        env, decided, monkeypatch, after_review, status, review):
    settings = (("after_review", after_review),) if after_review else ()
    key, result, _token = _in_review(env, monkeypatch, settings)
    assert _review_status(result) == "pending_review"                     # nothing moves while the AI still runs
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    assert calls == []                                                    # no review: no review loop
    assert (_node(env, key)[0], _review_status(result)) == (status, review)


def test_follow_approves_the_report_an_omitted_instruction_wrote(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"), ("after_review", "follow")), "TR")
    _work_run_ended()
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")


def test_a_run_that_is_not_the_node_ai_changes_nothing(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("after_review", "follow"),))
    ai_run_end_hooks.run_ended({"run_id": "air_someone_else", "group_id": FA, "action_scope": "new",
                                "mode": "single", "issued_to": "u_kim"})
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review")


# ── the card's AI review ─────────────────────────────────────────────────────

@pytest.mark.parametrize("review_ai, reviewer", [("aip_review", "aip_review"), (None, "aip_default")])
def test_a_card_review_starts_the_review_loop_with_its_review_ai_count_and_basis(
        env, decided, monkeypatch, review_ai, reviewer):
    from modules.flow_gate.settings import ai_settings_service

    monkeypatch.setattr(ai_settings_service, "resolve_effective",
                        lambda _project: {"providers": [{"id": "aip_default", "name": "default"}]})
    _sql_provider = "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) " \
                    "VALUES ('aip_default', 'aip_default', 'cli', 'claude', 't', 't')"
    _sql(env["db"], _sql_provider)
    settings = [("run_ai", "aip_card"), ("review_count", 2), ("review_basis", "last"), ("after_review", "follow"),
                ("time_limit", 120), ("restart", 1)]
    if review_ai:
        settings.append(("review_ai", review_ai))
    key, result, _token = _in_review(env, monkeypatch, settings)
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [call] = calls
    assert {k: call[k] for k in ("project_id", "group_id", "doc_ref", "action_scope", "mode", "issued_to")} == {
        "project_id": "flowgate", "group_id": FA, "doc_ref": result, "action_scope": "review", "mode": "single",
        "issued_to": "u_kim"}
    assert call["document_review_loop"] == {
        "review_count": 2, "reviewer_provider_id": reviewer, "review_criteria": "last_rejection_only",
        "rework_provider_id": "aip_card", "rework_timeout_sec": 7200, "rework_message": "",
        "failure_restart_max_attempts": 1, "total_timeout_sec": 14400}
    assert callable(call["issue_builder"]) and call.get("provider_id") is None
    # the review decides now: nothing is approved before it ends
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review")


@pytest.mark.parametrize("basis", ["type", "last"])
def test_every_review_hop_tells_the_review_ai_the_card_review_basis(env, decided, monkeypatch, basis):
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review"),
                                                         ("review_basis", basis)))
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    [call] = calls
    issue = call["issue_builder"]
    issue.loop_stage = "review"                        # admission: the loop's first hop is a review hop
    first = issue(ai_run_id=REVIEW_RUN)["mention"]     # the first hop's prompt is this mention as issued
    review_body = f"Review the target document ({result})"
    assert review_body in first and first.index("Review basis:") < first.index(review_body)
    if basis == "type":
        assert "Review basis: document type." in first
    else:
        assert "Review basis: last rejection only. The document has no rejection yet" in first
    # the loop rejected the result and its rework revised it: the next review hop (and a retry's
    # reissue) comes from the same issuer and reads the last rejection as it is then
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="Name every error code.")
    again = issue(ai_run_id=REVIEW_RUN)["mention"]
    assert review_body in again
    if basis == "type":
        assert "Review basis: document type." in again and "Name every error code." not in again
    else:
        assert "Last rejection:\nName every error code." in again


def test_the_review_basis_note_names_the_basis_and_the_missing_rejection():
    from flowgate.card_settings import fields as f

    assert f.review_basis_note("document_type_default", "ignored").startswith("Review basis: document type.")
    assert "has no rejection yet" in f.review_basis_note("last_rejection_only", None)
    assert "has no rejection yet" in f.review_basis_note("last_rejection_only", "  ")
    assert f.review_basis_note("last_rejection_only", " fix A ").endswith("Last rejection:\nfix A")


def test_the_review_loop_request_follows_the_card_within_the_loop_ranges():
    from flowgate.card_settings import fields as f

    base = f.review_loop({"review_count": -1}, reviewer="r", reworker="w")
    assert (base["review_count"], base["review_criteria"], base["rework_timeout_sec"],
            base["failure_restart_max_attempts"]) == (-1, "document_type_default", 3600, 0)
    assert f.review_loop({"review_count": 1, "time_limit": 30}, reviewer="r", reworker="w")["rework_timeout_sec"] == 1800
    assert f.review_loop({"review_count": 1, "restart": 5}, reviewer="r", reworker="w")[
        "failure_restart_max_attempts"] == 2
    assert f.review_loop({"review_count": 1, "restart": -1}, reviewer="r", reworker="w")[
        "failure_restart_max_attempts"] == -1
    assert [f.after_result(f.RUN, s) for s in ({}, {"after_review": "follow"}, {"review_count": 1},
                                              {"review_count": 0, "after_review": "follow"})] == [
        None, f.APPROVE, f.REVIEW, f.APPROVE]
    assert f.after_result(f.REQUIREMENT, {"review_count": 1}) is None


@pytest.mark.parametrize("after_review, stop_reason, status, review", [
    ("follow", "review_passed", "done", "approved"),
    ("human", "review_passed", "review", "pending_review"),
    ("follow", "review_count_exhausted", "review", "pending_review"),
    ("follow", "review_verdict_hold", "review", "pending_review"),
])
def test_when_the_card_review_ends_follow_approves_only_a_passed_result(
        env, decided, monkeypatch, after_review, stop_reason, status, review):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review"),
                                                         ("after_review", after_review)))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}})
    assert (_node(env, key)[0], _review_status(result)) == (status, review)


def _loop_row(result, stage):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    db_loops.insert({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "review_count": 2,
                     "reviewer_provider_id": "aip_review", "review_criteria": "document_type_default",
                     "rework_provider_id": "aip_assignee", "rework_timeout_sec": 3600,
                     "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "review_baseline_id": 0,
                     "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t", "deadline_at": "t",
                     "current_stage": stage})


def test_while_the_card_review_works_its_rejection_is_not_the_outcome_and_the_card_is_locked(
        env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services import result_binder

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    assert get_settings(env, FA, key)["locked"] is False                  # in review, no AI review yet
    _loop_row(result, "rework")
    db_documents.update(result, {"doc_review_status": "rejected"})       # the review's rejection, before rework
    result_binder.reconcile_if_needed(FA, "flowgate")
    assert _node(env, key) == ("review", result)
    assert get_settings(env, FA, key)["locked"] is True
    assert setting(env, FA, key, "after_review", "follow", expect=409)["error"]["code"] == "node_settings_locked"
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    result_binder.reconcile_if_needed(FA, "flowgate")                    # the review ended rejected
    assert _node(env, key) == ("rejected", result)
    assert get_settings(env, FA, key)["locked"] is False


def test_the_review_loop_of_a_passed_result_is_read_from_its_row(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review"),
                                                         ("after_review", "follow")))
    _loop_row(result, "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_passed' WHERE run_id = ?", (REVIEW_RUN,))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "review"}})   # the stale copy
    assert (_node(env, key)[0], _review_status(result)) == ("done", "approved")


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_engine_calls_the_run_end_port_after_the_release_and_the_port_is_stdlib_only():
    src = (_SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke" / "finalize.py").read_text(encoding="utf-8")
    body = src[src.index("def _finalize_run("):src.index("def _auto_resume_test_gate_chain(")]
    assert body.index('terminal.cleanup(run, handoff=respawn_pending, reason="normal_finish")') < \
        body.index("ai_run_end_hooks.run_ended(run)")
    tree = ast.parse((_SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_run_end_hooks.py").read_text(
        encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "logging", "threading", "typing"}


def test_the_source_tools_ask_the_card_port_before_the_sequence_and_the_port_is_stdlib_only():
    src = (_SERVER_DIR / "modules" / "flow_gate" / "services" / "remote_tool_service.py").read_text(encoding="utf-8")
    body = src[src.index("def _worker_token_step_type_result("):src.index("def _worker_token_step_type(")]
    assert body.index("_SELF_SCOPED_WORK_TYPES") < body.index("worker_step_type_hooks.token_step_type(token_rec)") \
        < body.index("db_wfseq.get_sequence_for_member_doc")
    tree = ast.parse((_SERVER_DIR / "modules" / "flow_gate" / "services" / "worker_step_type_hooks.py").read_text(
        encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "threading", "typing"}


def test_integration_registers_every_port_and_the_run_end_hook():
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from modules.flow_gate.services import ai_run_end_hooks, worker_step_type_hooks

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert not any(ports.registered(n) for n in ports.PORT_NAMES)
        assert not ai_run_end_hooks.registered(ai_run_end_hooks.ON_RUN_ENDED)
        assert not worker_step_type_hooks.registered(worker_step_type_hooks.TOKEN_STEP_TYPE)
        integration.register()
        assert all(ports.registered(n) for n in ports.PORT_NAMES)
        assert ai_run_end_hooks.registered(ai_run_end_hooks.ON_RUN_ENDED)
        assert worker_step_type_hooks.registered(worker_step_type_hooks.TOKEN_STEP_TYPE)
    finally:
        integration.unregister()
    ai_run_end_hooks.run_ended({"run_id": "x"})                           # nothing registered: a no-op
    assert worker_step_type_hooks.token_step_type({"token_id": "x", "group_id": FA}) is None
