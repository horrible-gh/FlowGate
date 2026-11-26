"""(NR, server side) — an explicit step / full command requests the result review of
a card whose review loop spent its budget again, as the review assignment's next ``seq``.

The harness of ``test_fg_card_intervention_assigned_0119`` (real SQLite DB, routes and migrations, the stage
assignment feature on, recorders for every AI start; nothing calls a real AI). An isolated test Flow only: R -> a -> a2
-> a3, ``a`` an AI card (X writes, B reviews, X reworks, count 1), ``a2`` / ``a3`` a person's report with no review.

- NR §6.1 the spent review: ``seq`` 1 done ``not_passed:retry_exhausted``, the card in review + ``needs_intervention``,
  the run idle ``needs_intervention``;
- §6.3 a step: ``seq`` 2 opened with ``seq`` 1's reviewer / rework AI / rounds (the card settings changed meanwhile),
  ``seq`` 1 untouched, the ledger request and the loop start carry ``seq`` 2, the step's targets are the card; its pass
  completes the card, the next card stays pending (idle ``step_done``) and opens with the next command;
- §6.4 a full command: the pass goes on through the next cards to ``completed``;
- §6.5 a person's save of the rejected AI result through the Document window's save (the content PATCH route function)
  opens no ``seq`` by itself (``needs_new_submission`` unchanged); the saved state (its ``doc_review_status`` and
  revision) is measured, and the command's ``seq`` 2 takes it as its basis and starts at the hop
  ``compute_review_baseline`` gives for it (read at the loop's start);
- §6.6 the excluded reasons / author / attempt kinds / Hold: nothing started, no target; and one Flow with three AI cards
  in review side by side, two admitted (``retry_exhausted``, ``total_timeout``) and one excluded
  (``review_verdict_hold``), all real review rows: a step / full command requests every admitted one (a step's targets
  are both) and nothing of the excluded one;
- §6.8 a re-requested review that spends its budget again waits again; only the next command opens ``seq`` 3;
- §6.9 a start refused: ``seq`` 2 ``failed(start_refused:<code>)``, no other AI asked;
- the recovery cycle a re-request begins (review P1 of the rework): with the submission mark unchanged, a refused start
  of ``seq`` 2 is retried by the run reads once the provider is back (``seq`` 3, then its pass completes the card), a
  pass whose automatic approval failed once is approved by the next read, both retries end at
  ``SYSTEM_ERROR_RETRY_LIMIT`` of the cycle, and a needs_intervention reason recorded in the cycle still stops them;
- §6.10 two commands in a row and a lost compare-and-set open ``seq`` 2 once;
- §6.11 a Stop during the re-requested review, then a command: the stopped run takes a new attempt (L-2 §2.10), every
  ``seq`` of the old one kept;
- §6.12 history kept: seq 1..n rows, no node / card / document added;
- the lock order decided for §5.1.3-6 (no hold inside the command's guard): a Stop's exclusive gate is taken while the
  command is inside its guard, and a Stop that then waits for the guard finishes with the command — no deadlock.
"""
from __future__ import annotations

import json
import threading
import traceback

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, A, X, _review_status, _sql, assignments, b1, command, db_path, decided, env, events, ledger, node_run, one,
    record_starts, roles, starts, stored, wired, write_line,
)
from test_fg_card_intervention_assigned_0119 import (  # noqa: F401 — fixtures used by name
    LIMIT, MEMO, _loop_end, _loop_row, _read, _refused, _reviewed_ai_result, _run_row, _wire, fork_join, memo_routes,
)
from test_fg_card_settings import _h, edge, node, put_draft
from test_fg_card_settings_review import REVIEW_RUN, _result, _run_ended, _start_ai

SPENT = "review_not_passed:retry_exhausted"
SECOND_LOOP, THIRD_LOOP = "air_rerequest_2", "air_rerequest_3"


# ── helpers ──────────────────────────────────────────────────────────────────

def _chain(env):
    """R -> a -> a2 -> a3; a: an AI card (X writes, B reviews, X reworks, count 1); a2, a3: a person's report with no
    review. -> (a, a2, a3)."""
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_a2", "T"), node("tmp_a3", "T")],
                     [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_a2"),
                      edge("tmp_e3", "tmp_a2", "tmp_a3")])
    a, a2, a3 = (body["key_map"][ref] for ref in ("tmp_a", "tmp_a2", "tmp_a3"))
    for key in (a, a2, a3):
        stored(key, "instruction", "omit")
    stored(a, "review_count", 1)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(a, execution=X, review=B)
    for key in (a2, a3):
        roles(key, execution="not_used", review="not_used")
    return a, a2, a3


def _spent(env, monkeypatch, starts, stop="retry_exhausted"):
    """NR §6.1: a's result reviewed by B, the loop's budget spent with ``stop``. -> (a, a2, a3, result, seq 1)."""
    a, a2, a3 = _chain(env)
    command(env, "full")
    key, result, rr = _reviewed_ai_result(env, monkeypatch, starts, a)
    _loop_row(result, REVIEW_RUN, "rework")
    _loop_end(env, rr["intent_exec_id"], result, REVIEW_RUN, stop)
    _read(env)                                          # the screen's run read settles it idle (as on stg)
    return a, a2, a3, result, one(env, a, "result_review", 1)


def _loops(starts):
    return [c for c in starts if c.get("document_review_loop")]


def _run():
    from modules.flow_gate.graph_workflow.store import run_store

    return run_store.current_run(FA)


def _stage_assigned(env, key):
    return [p for _r, p in events(env, "stage_assigned") if p.get("node_key") == key and p.get("stage") == "result_review"]


def _counts(env):
    return tuple(_sql(env["db"], sql)[0]["n"] for sql in (
        "SELECT COUNT(*) AS n FROM graph_node_runs", "SELECT COUNT(*) AS n FROM graph_node_documents",
        "SELECT COUNT(*) AS n FROM documents"))


def _passes(env, monkeypatch, key, result, loop_run):
    """The re-requested loop (the latest seq of ``key``'s result review) passes the result."""
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    latest = max((r for r in assignments(env, key) if r["stage"] == "result_review"), key=lambda r: r["seq"])
    _loop_row(result, loop_run, "review")
    _loop_end(env, latest["intent_exec_id"], result, loop_run, "review_passed")
    return latest


# ── the pure judgement ──────────────────────────────────────────────────────

def test_only_a_spent_review_budget_is_requested_again_by_a_command_and_nothing_is_retried_automatically():
    from flowgate.card_intervention import reasons

    for stop in ("retry_exhausted", "review_count_exhausted", "total_timeout"):
        assert reasons.command_rerequest_due("review_not_passed:" + stop) is True
        assert reasons.retry_due(["review_not_passed:" + stop]) is False             # no automatic retry
        assert reasons.kind_of("review_not_passed:" + stop) == reasons.NEEDS_INTERVENTION
    for reason in ("review_not_passed:review_verdict_hold", "review_not_passed:review_stalled",
                   "review_not_passed:restart_orphaned", "review_not_passed:", "run_not_clean:timeout",
                   "issuer_lacks_approve", "instruction_not_approved", "completion_missing", "review_not_started",
                   "approval_refused", "something_new", "", None, 3):
        assert reasons.command_rerequest_due(reason) is False, reason


# ── NR §6.1 / §6.3: a step requests the spent review again as seq 2 ─────────────

def test_a_step_requests_the_spent_review_again_as_seq_2_with_its_assignment_and_the_next_card_waits(
        env, decided, monkeypatch, starts, fork_join):
    a, a2, a3, result, first = _spent(env, monkeypatch, starts)
    # §6.1: the reproduced state
    assert (first["stage_state"], first["verdict"]) == ("done", "not_passed:retry_exhausted")
    held = {k: v for k, v in _wire(env, a)["intervention"].items() if k != "stop_detail"}   # F98-4: an added field
    assert held == {"kind": "needs_intervention", "reason": SPENT}
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "needs_intervention")
    before = dict(first)
    counts = _counts(env)
    # the card settings changed meanwhile: the re-request keeps the assignment's reviewer, rework AI and rounds
    stored(a, "review_count", 3)
    roles(a, execution=A, review=A)
    loops = len(_loops(starts))

    command(env, "step")

    second = one(env, a, "result_review", 2)
    assert second is not None
    assert (second["actor"], second["provider_id"], second["rework_provider_id"], second["review_rounds"],
            second["author"], second["source"]) == \
        ("ai", A, A, 3, "ai", first["source"])
    assert (second["stage_state"], second["verdict"], second["intent_kind"]) == ("active", None, "execution")
    assert second["basis_doc_id"] == result
    assert one(env, a, "result_review", 1) == before                       # seq 1 only read
    [req] = [l for l in ledger(env) if l["exec_id"] == second["intent_exec_id"]]
    assert (req["assignment_stage"], req["assignment_seq"]) == ("result_review", 2)
    raw = json.loads(req["request_json"])
    assert raw["assignment_ref"] == {"attempt_id": first["attempt_id"], "stage": "result_review", "seq": 2}
    assert (raw["invocation_origin"]["round_no"], raw["invocation_origin"]["target"]["seq"]) == (1, 2)
    started = _loops(starts)[loops:]
    assert len(started) == 1
    [loop] = started
    assert loop["graph_exec_id"] == second["intent_exec_id"] and loop["doc_ref"] == result
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["document_review_loop"]["rework_provider_id"],
            loop["document_review_loop"]["review_count"]) == (A, A, 3)
    [assigned] = [p for p in _stage_assigned(env, a) if p.get("seq") == 2]
    assert (assigned["rerequest_of_seq"], assigned["rerequest_cause"], assigned["cause"]) == \
        (1, "user_command", "stage_assigned")
    run = _run()
    assert (run["run_mode"], run["step_node_keys"]) == ("step", [a])           # never []: the step's own target
    assert run["status"] == "running"
    assert _wire(env, a)["intervention"] is None                             # its review is requested / works

    # its pass completes the card; the next card waits for the next command (0053)
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"
    read = _read(env)
    assert (read["status"], read["idle_reason"]) == ("idle", "step_done")
    command(env, "step")
    assert node_run(env, a2)["status"] == "open" and node_run(env, a3)["status"] == "pending"
    # §6.12: history kept — seq 1..2, no node / card line / document added by the re-request
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]
    assert _counts(env)[:2] == counts[:2] and _counts(env)[2] == counts[2]


def test_the_re_requested_rework_hop_carries_the_last_rejection_and_the_card_memo(
        env, decided, monkeypatch, starts, fork_join, memo_routes):
    """§6.3: the rejected result (as the loop's automatic rejection leaves it) with a memo for AI reading — the command's
    loop is the card's own (``legacy.start_review_loop`` with the card's node): its rework hop's mention carries the
    last rejection reason and the memo."""
    from modules.flow_gate.api import token_routes
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import ai_invoke_service

    monkeypatch.setattr(token_routes, "_build_mention_for_token", lambda **kw: "STANDARD MENTION")
    a, _a2, _a3, result, _first = _spent(env, monkeypatch, starts)
    why = "Name every error code of table v2."
    db_documents.update(result, {"doc_review_status": "rejected", "rejection_reason": why,
                                 "rejection_history": json.dumps([{"rejected_at": "t", "reason": why}])})
    res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{a}/memos", json={"title": MEMO, "body": "v2 only",
                                                                           "ai_ref": True}, headers=_h())
    assert res.status_code == 201, res.text
    loops = len(_loops(starts))
    command(env, "step")
    [loop] = _loops(starts)[loops:]
    issue = loop["issue_builder"]
    issue.loop_stage = ai_invoke_service.REWORK_HOP_KIND                       # the engine's rework hop
    mention = issue(None)["mention"]
    assert "Last rejection reason" in mention and why in mention
    assert "## User memos" in mention and MEMO in mention


# ── NR §6.4: a full command goes on after the pass ───────────────────────────



# ── NR §6.5: a person's edit of the AI's result ──────────────────────────────

def _first_hops(monkeypatch):
    """The first hop of every review loop the admission is asked to start, as the admission decides it at that moment
    (``review.compute_review_baseline`` of the loop's document: ``starts_with_rework`` -> rework, else review); the
    start itself is the suite's recorder. An engine replay of the same requested execution is no new start."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.ai_invoke import review as loop_review

    hops, recorder = [], ai_invoke_service.start_run

    def start_run(**kw):
        if kw.get("document_review_loop") and not any(f.name == "replay" for f in traceback.extract_stack()):
            baseline = loop_review.compute_review_baseline(kw["doc_ref"])
            hops.append("rework" if baseline["starts_with_rework"] else "review")
        return recorder(**kw)

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    return hops




# ── NR §6.6: what a command never starts ─────────────────────────────────────

def _hold(env, key):
    from flowgate.card_settings.fields import HOLD

    stored(key, HOLD.name, True)


def _reason(env, a, result, first, reason):
    from flowgate.card_settings import legacy_adapter

    basis = {"doc_id": result, "revision_no": int(first["basis_revision_no"]),
             "rejection_count": int(first["basis_rejection_count"])}
    legacy_adapter.record_submission_wait(FA, a, result, reason, basis, attempt_id=first["attempt_id"])


EXCLUDED = {
    "review_verdict_hold": lambda env, a, result, first: _reason(env, a, result, first,
                                                                 "review_not_passed:review_verdict_hold"),
    "review_stalled": lambda env, a, result, first: _reason(env, a, result, first, "review_not_passed:review_stalled"),
    "run_not_clean": lambda env, a, result, first: _reason(env, a, result, first, "run_not_clean:timeout"),
    "issuer_lacks_approve": lambda env, a, result, first: _reason(env, a, result, first, "issuer_lacks_approve"),
    "unknown_reason": lambda env, a, result, first: _reason(env, a, result, first, "a_reason_not_known_yet"),
    "completion_missing": lambda env, a, result, first: _sql(
        env["db"], "DELETE FROM graph_run_events WHERE cause = 'auto_approval_skipped'"),
    "person_author": lambda env, a, result, first: _sql(
        env["db"], "UPDATE graph_stage_assignments SET author = 'person', rework_provider_id = NULL, "
                   "author_provider_id = NULL WHERE attempt_id = ? AND stage = 'result_review'",
        (first["attempt_id"],)),
    "legacy_attempt": lambda env, a, result, first: _sql(
        env["db"], "DELETE FROM graph_stage_assignments WHERE attempt_id = ?", (first["attempt_id"],)),
    "hold": lambda env, a, result, first: _hold(env, a),
}




def _fan(env):
    """R -> a, R -> b, R -> c: three AI cards side by side (each: X writes, B reviews, X reworks, count 1)."""
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_b", "T"), node("tmp_c", "T")],
                     [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_r", "tmp_b"),
                      edge("tmp_e3", "tmp_r", "tmp_c")])
    keys = [body["key_map"][ref] for ref in ("tmp_a", "tmp_b", "tmp_c")]
    for key in keys:
        stored(key, "instruction", "omit")
        stored(key, "review_count", 1)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    for key in keys:
        roles(key, execution=X, review=B)
    return keys


def _card_spent(env, monkeypatch, starts, key, n, stop):
    """The card ``key``'s AI (X) writes its result as the engine runs it, its run end opens B's review loop (the
    assigned path), and that loop stops with ``stop`` — real rows of the card alone (``_executions_ended`` would end the
    other cards' executions too, so only this card's work execution is ended). -> (result, its seq 1)."""
    from flowgate.graph_run import execution_store as ledger_store
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    work, loop = f"air_fan_work_{n}", f"air_fan_review_{n}"
    [job] = [j for j in env["jobs"] if j.get("node_key") == key and j.get("provider_id") == X]
    _heads, token = _start_ai(monkeypatch, job, work)
    result = _result(90 + n, "TR")
    v01_contact.after_inbox_registered(FA, result, "TR", token)
    token_service.consume(token, "flowgate", result)
    for row in _sql(env["db"], "SELECT exec_id, status, lease_token FROM graph_attempt_runs WHERE exec_id = ?",
                    (job["graph_exec_id"],)):
        if row["status"] == "requested":
            ledger_store.cancel(row["exec_id"], None, "requested")
        elif row["status"] == "running":
            ledger_store.finish(row["exec_id"], row["lease_token"], "ended")
    record_starts(monkeypatch, starts)
    _run_ended(work, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["rework_provider_id"], rr["author"]) == ("ai", B, X, "ai")
    _loop_row(result, loop, "rework")
    _loop_end(env, rr["intent_exec_id"], result, loop, stop)
    return result, one(env, key, "result_review", 1)




# ── NR §6.8: spent again, the next command only ──────────────────────────────



# ── NR §6.9: a refused start ─────────────────────────────────────────────────



# ── review P1 of the rework: the re-request begins a new recovery cycle ───────

def _refuse_starts(monkeypatch):
    """Every AI invoke admission refuses with its own code; -> (the list of refused review loops, the recorder to put
    back). An engine replay of the same requested execution is no new start."""
    from modules.flow_gate.services import ai_invoke_service

    tried, recorder = [], ai_invoke_service.start_run

    class Refused(RuntimeError):
        code = "ai_disabled"

    def refused(**kwargs):
        if not any(f.name == "replay" for f in traceback.extract_stack()):
            tried.append((kwargs.get("graph_exec_id"), (kwargs.get("document_review_loop") or {}).get(
                "reviewer_provider_id")))
        raise Refused("the reviewer is disabled")

    monkeypatch.setattr(ai_invoke_service, "start_run", refused)
    return tried, recorder


def _rerequested_at(env, key, seq=2):
    """The event_seq of the command re-request that opened ``seq`` of ``key``'s result review."""
    [found] = [p["event_seq"] for p in _stage_assigned(env, key)
               if p.get("seq") == seq and p.get("rerequest_cause") == "user_command"]
    return found


def _b1_after(env, floor, reason=None):
    """B-1 records after ``floor`` (event_seq): (reason, submission mark)."""
    return [(r, p.get("submission_mark")) for r, p in events(env, "auto_approval_skipped")
            if p["event_seq"] > floor and (reason is None or r == reason)]


def _mark(first):
    return {"doc_id": first["basis_doc_id"], "revision_no": int(first["basis_revision_no"]),
            "rejection_count": int(first["basis_rejection_count"])}












# ── NR §6.10: twice in a row, a lost compare-and-set ──────────────────────────





# ── NR §6.11: a Stop during the re-requested review, then a command ───────────



# ── the lock order (§5.1.3-6, decision ①: no hold inside the command's guard) ──
