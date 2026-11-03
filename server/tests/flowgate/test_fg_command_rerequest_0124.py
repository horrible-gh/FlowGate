"""flowgate.v02.0124 T#1 (0003-NR §5.1, §6 server side) — an explicit step / full command requests the result review of
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
        assert reasons.retry_due(["review_not_passed:" + stop]) is False             # 0119: no automatic retry
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
    assert _wire(env, a)["intervention"] == {"kind": "needs_intervention", "reason": SPENT}
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

def test_a_full_command_requests_the_spent_review_again_and_goes_on_to_completed_after_its_pass(
        env, decided, monkeypatch, starts, fork_join):
    a, a2, a3, result, first = _spent(env, monkeypatch, starts)
    command(env, "full")
    second = one(env, a, "result_review", 2)
    assert second is not None and second["intent_kind"] == "execution"
    assert _run()["step_node_keys"] is None                                    # a full command has no targets
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    assert node_run(env, a)["status"] == "done" and node_run(env, a2)["status"] == "open"
    write_line(env, a2, "report", "## 결과\n\n본문\n")
    assert node_run(env, a2)["status"] == "done" and node_run(env, a3)["status"] == "open"
    write_line(env, a3, "report", "## 결과\n\n본문\n")
    read = _read(env)
    assert (read["status"], read["idle_reason"]) == ("idle", "completed")
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]


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


def test_a_person_s_save_of_the_rejected_ai_result_opens_no_seq_and_the_command_s_seq_2_starts_from_its_saved_state(
        env, decided, monkeypatch, starts, fork_join):
    """§6.5 on the real save: the result rejected as the loop's automatic rejection leaves it, then a person saves an
    edit through the Document window's [저장] (``documents.update_document_content``, the content PATCH route's own
    function: its write checks, its Flow guard, its save contact). Measured here (NR §6.5 left it unconfirmed): the save
    writes the body and keeps the document's revision and its ``doc_review_status`` (``rejected``) — a body save is no
    submission. So the save / a read opens nothing, the command's ``seq`` 2 takes the saved document's mark as its
    basis, and its first hop is the one ``compute_review_baseline`` gives for that state: rework (the AI reworks the
    person's edit with the last rejection reason)."""
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.storage.paths import get_storage_root

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    why = "Name every error code of table v2."
    db_documents.update(result, {"doc_review_status": "rejected", "rejection_reason": why,
                                 "rejection_history": json.dumps([{"rejected_at": "t", "reason": why}])})
    before = db_documents.get_by_id(result)
    edited = "---\ntitle: 결과\n---\n## 결과\n\n사람이 고친 본문: 표 v2의 오류 코드 전부\n"

    documents.update_document_content(result, documents.DocumentContentUpdate(content=edited),
                                      current_user={"user_id": "u_kim", "is_admin": True})

    saved = db_documents.get_by_id(result)
    body = (get_storage_root("flowgate") / saved["file_path"]).read_text(encoding="utf-8")
    assert "사람이 고친 본문" in body                                              # the person's edit is saved
    measured = (int(saved.get("revision_no") or 0), saved.get("doc_review_status"))
    assert measured == (int(before.get("revision_no") or 0), "rejected"), measured     # NR §6.5: measured here
    assert sa.needs_new_submission(first, saved) is False                     # an AI's document: unchanged
    _read(env)
    assert one(env, a, "result_review", 2) is None                             # no seq by the save / a read
    assert one(env, a, "result_review", 1) == first
    hops = _first_hops(monkeypatch)

    command(env, "step")

    second = one(env, a, "result_review", 2)
    assert (second["basis_doc_id"], second["basis_revision_no"], second["basis_rejection_count"]) == \
        tuple(sa.mark(db_documents.get_by_id(result)).values())               # the saved document's mark
    assert hops == ["rework"]                                                  # compute_review_baseline of "rejected"
    assert _run()["step_node_keys"] == [a]


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


@pytest.mark.parametrize("case", sorted(EXCLUDED))
def test_an_excluded_card_is_never_requested_again_by_a_command(env, decided, monkeypatch, starts, fork_join, case):
    a, a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED[case](env, a, result, first)
    rows = len(assignments(env, a))
    loops, requests = len(_loops(starts)), len(ledger(env))
    command(env, "step")
    if case in {"review_verdict_hold", "run_not_clean", "completion_missing", "person_author"}:
        # 0126: explicit reentry now covers these waits; attribution and older seqs remain fixed.
        second = one(env, a, "result_review", 2)
        assert second is not None and len(assignments(env,a)) == rows + 1
        assert len(_loops(starts)) == loops + 1 and len(ledger(env)) == requests + 1
        assert node_run(env,a2)["status"] == "pending"
        if case == "person_author":
            assert second["review_rounds"] == 1 and second["rework_provider_id"] is None
        return
    assert len(assignments(env, a)) == rows and one(env, a, "result_review", 2) is None
    assert len(_loops(starts)) == loops and len(ledger(env)) == requests
    assert a not in (_run()["step_node_keys"] or [])
    assert node_run(env, a)["status"] == "review" and node_run(env, a2)["status"] == "pending"
    assert _run_row(env)["status"] == "idle"


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


@pytest.mark.parametrize("mode", ["step", "full"])
def test_a_command_requests_every_admitted_card_of_a_flow_again_and_nothing_of_an_excluded_one(
        env, decided, monkeypatch, starts, fork_join, mode):
    """§6.6 last row on real rows: three AI cards of one Flow in review at once — a (``retry_exhausted``) and b
    (``total_timeout``) admitted, c (``review_verdict_hold``) excluded. One command: a and b each get their ``seq`` 2 with
    its ledger request and loop start, both are the step's targets; c is asked too (every review row is) and gets
    nothing — no ``seq``, no ledger request, no loop start, still waiting with its own reason."""
    from flowgate.card_settings import assigned_review

    a, b, c = _fan(env)
    command(env, "full")
    results, firsts = {}, {}
    for n, (key, stop) in enumerate(((a, "retry_exhausted"), (b, "total_timeout"), (c, "review_verdict_hold"))):
        results[key], firsts[key] = _card_spent(env, monkeypatch, starts, key, n, stop)
    _read(env)
    assert {k: _wire(env, k)["intervention"]["reason"] for k in (a, b, c)} == {
        a: "review_not_passed:retry_exhausted", b: "review_not_passed:total_timeout",
        c: "review_not_passed:review_verdict_hold"}
    assert _run_row(env)["status"] == "idle"
    _hold(env, c)  # 0126: verdict hold is retryable; the independent card Hold still blocks.
    asked = []
    real = assigned_review.rerequest_review_locked

    def spy(ctx, row):
        opened = real(ctx, row)
        asked.append((row["node_key"], opened))
        return opened

    monkeypatch.setattr(assigned_review, "rerequest_review_locked", spy)
    requests_of_c = len([l for l in ledger(env) if l["attempt_id"] == firsts[c]["attempt_id"]])
    loops = len(_loops(starts))

    command(env, mode)

    assert sorted(asked) == sorted([(a, True), (b, True), (c, False)])
    for key in (a, b):
        second = one(env, key, "result_review", 2)
        assert (second["actor"], second["provider_id"], second["rework_provider_id"], second["intent_kind"]) == \
            ("ai", B, X, "execution")
        assert second["basis_doc_id"] == results[key]
        assert one(env, key, "result_review", 1) == firsts[key]
    started = _loops(starts)[loops:]
    assert sorted(s["doc_ref"] for s in started) == sorted([results[a], results[b]])
    assert one(env, c, "result_review", 2) is None and one(env, c, "result_review", 1) == firsts[c]
    assert len([l for l in ledger(env) if l["attempt_id"] == firsts[c]["attempt_id"]]) == requests_of_c
    assert node_run(env, c)["status"] == "review"
    assert _wire(env, c)["intervention"] == {"kind": "needs_intervention",
                                             "reason": "review_not_passed:review_verdict_hold"}
    assert _run()["step_node_keys"] == (sorted([a, b]) if mode == "step" else None)


# ── NR §6.8: spent again, the next command only ──────────────────────────────

def test_a_re_requested_review_spent_again_waits_again_and_only_the_next_command_opens_seq_3(
        env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, _first = _spent(env, monkeypatch, starts)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    _loop_row(result, SECOND_LOOP, "rework")
    _loop_end(env, second["intent_exec_id"], result, SECOND_LOOP, "retry_exhausted")
    assert one(env, a, "result_review", 2)["verdict"] == "not_passed:retry_exhausted"
    loops = len(_loops(starts))
    for _ in range(3):
        _read(env)                                                             # reads retry nothing
    assert one(env, a, "result_review", 3) is None and len(_loops(starts)) == loops
    assert _wire(env, a)["intervention"]["kind"] == "needs_intervention"
    assert _run_row(env)["idle_reason"] == "needs_intervention"
    command(env, "step")
    third = one(env, a, "result_review", 3)
    assert (third["provider_id"], third["rework_provider_id"], third["intent_kind"]) == (B, X, "execution")
    assert len(_loops(starts)) == loops + 1


# ── NR §6.9: a refused start ─────────────────────────────────────────────────

def test_a_refused_start_of_the_re_requested_review_fails_seq_2_with_its_code_and_asks_no_other_ai(
        env, decided, monkeypatch, starts, fork_join):
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    tried = []

    class Refused(RuntimeError):
        code = "ai_disabled"                                                   # an admission refusal's own code

    def refused(**kwargs):
        tried.append((kwargs.get("graph_exec_id"), (kwargs.get("document_review_loop") or {}).get(
            "reviewer_provider_id")))
        raise Refused("the reviewer is disabled")

    monkeypatch.setattr(ai_invoke_service, "start_run", refused)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    assert (second["stage_state"], second["fail_cause"]) == ("failed", "start_refused:ai_disabled")
    assert tried == [(second["intent_exec_id"], B)]                            # once, the assigned reviewer only
    [req] = [l for l in ledger(env) if l["exec_id"] == second["intent_exec_id"]]
    assert req["status"] != "requested"                                        # the refused start ended its request
    assert one(env, a, "result_review", 1)["verdict"] == first["verdict"]
    assert ("review_not_started" in [r for _d, r, _m in b1(env)])
    assert _run()["step_node_keys"] == [a]


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


def test_a_refused_start_after_a_re_request_of_the_same_submission_is_retried_once_its_provider_is_back(
        env, decided, monkeypatch, starts, fork_join):
    """total_timeout of seq 1 (the submission mark unchanged), the command's seq 2 refused once: a system error of the
    new cycle — the review_not_passed record the command answered no longer stops its retry. The next run read opens
    seq 3 (the existing 0119 retry) with the reviewer back; its pass completes the card."""
    from flowgate.card_settings import assigned_review
    from modules.flow_gate.services import ai_invoke_service

    a, a2, _a3, result, first = _spent(env, monkeypatch, starts, stop="total_timeout")
    tried, recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    assert (second["stage_state"], second["fail_cause"]) == ("failed", "start_refused:ai_disabled")
    floor = _rerequested_at(env, a)
    assert _b1_after(env, floor) == [("review_not_started", _mark(first))]           # the same submission's mark
    assert [r for r, _p in events(env, "auto_approval_skipped") if r == "review_not_passed:total_timeout"]  # kept
    assert assigned_review.command_cycle(second["run_id"], a, second["attempt_id"], "result") == (floor, 2)
    assert assigned_review.review_retry_due(second) is True                   # the probe the review ran: False before

    monkeypatch.setattr(ai_invoke_service, "start_run", recorder)             # the provider is back
    loops = len(_loops(starts))
    _read(env)

    third = one(env, a, "result_review", 3)
    assert third is not None
    assert (third["actor"], third["provider_id"], third["rework_provider_id"], third["stage_state"],
            third["intent_kind"]) == ("ai", B, X, "active", "execution")
    assert third["basis_doc_id"] == result
    [loop] = _loops(starts)[loops:]
    assert loop["graph_exec_id"] == third["intent_exec_id"] and loop["doc_ref"] == result
    assert len(tried) == 1                                                      # refused once, retried once
    assert one(env, a, "result_review", 1) == first                            # seq 1 / 2 kept as history
    assert (one(env, a, "result_review", 2)["stage_state"], one(env, a, "result_review", 2)["fail_cause"]) == \
        ("failed", "start_refused:ai_disabled")
    _passes(env, monkeypatch, a, result, THIRD_LOOP)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"                           # the step's next card: next command
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2, 3]


def test_the_retries_of_a_re_requested_start_end_at_the_limit_of_its_own_cycle(
        env, decided, monkeypatch, starts, fork_join):
    """The reviewer stays refused: seq 2 (the command's) and the reads' retries are tried ``SYSTEM_ERROR_RETRY_LIMIT``
    times in all, counted from the re-request — then nothing is tried again however often the run is read."""
    a, _a2, _a3, _result, first = _spent(env, monkeypatch, starts)
    tried, _recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    for _ in range(LIMIT + 3):
        _read(env)
    rows = [r for r in assignments(env, a) if r["stage"] == "result_review"]
    assert [r["seq"] for r in rows] == list(range(1, LIMIT + 2))              # seq 1, then LIMIT tries of the cycle
    assert all((r["stage_state"], r["fail_cause"]) == ("failed", "start_refused:ai_disabled") for r in rows[1:])
    assert len(tried) == LIMIT and {reviewer for _e, reviewer in tried} == {B}
    assert one(env, a, "result_review", 1) == first
    assert _b1_after(env, _rerequested_at(env, a), "review_not_started") == [("review_not_started", _mark(first))]
    assert _wire(env, a)["intervention"] == {"kind": "system_error", "reason": "review_not_started"}


def test_a_needs_intervention_reason_recorded_after_the_re_request_still_stops_the_retries(
        env, decided, monkeypatch, starts, fork_join):
    """The cycle only drops the records before the re-request: a needs_intervention reason recorded in it (here an
    abnormal end) stops the retry of the refused start as before — nothing is requested again but by a command."""
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    _tried, recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    _reason(env, a, result, first, "run_not_clean:timeout")
    monkeypatch.setattr(ai_invoke_service, "start_run", recorder)
    loops = len(_loops(starts))
    for _ in range(3):
        _read(env)
    assert one(env, a, "result_review", 3) is None and len(_loops(starts)) == loops


def test_a_re_requested_pass_whose_approval_failed_once_completes_on_the_next_read(
        env, decided, monkeypatch, starts, fork_join):
    """seq 2 passes the same submission, its automatic approval is refused once (``approval_refused``, a system error
    of the cycle): the next run read approves it — the seq 1 review_not_passed record of that mark no longer stops it."""
    from flowgate.card_settings import legacy_adapter

    a, a2, _a3, result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    floor = _rerequested_at(env, a)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    assert one(env, a, "result_review", 2)["verdict"] == "passed"
    assert node_run(env, a)["status"] == "review" and _review_status(result) != "approved"
    assert _b1_after(env, floor) == [("approval_refused", _mark(first))]
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)  # the cause is gone
    _read(env)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"
    assert _b1_after(env, floor) == [("approval_refused", _mark(first))]       # nothing recorded by the retry


def test_the_approval_retries_of_a_re_requested_pass_end_at_the_limit_of_its_own_cycle(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import legacy_adapter

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    floor = _rerequested_at(env, a)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    for _ in range(LIMIT + 3):
        _read(env)
    assert _b1_after(env, floor, "approval_refused") == [("approval_refused", _mark(first))] * LIMIT
    assert node_run(env, a)["status"] == "review" and _review_status(result) != "approved"
    assert _wire(env, a)["intervention"] == {"kind": "system_error", "reason": "approval_refused"}
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]


# ── NR §6.10: twice in a row, a lost compare-and-set ──────────────────────────

def test_two_commands_in_a_row_open_seq_2_once(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)
    command(env, "step")
    again = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "full"}, headers=_h())
    assert again.status_code in (200, 201, 409), again.text                    # 409 run_already_active while it works
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]
    assert len([l for l in ledger(env) if l["assignment_seq"] == 2]) == 1


def test_a_lost_compare_and_set_requests_nothing(env, decided, monkeypatch, starts, fork_join):
    """Another writer opened seq 2 between the judgement and the insert: insert-if-absent loses, nothing requested."""
    from flowgate.card_settings import assigned_review
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)

    from modules.flow_gate.db import documents as db_documents

    def other_writer():
        return sa.restart_review_stage(FA, first, sa.mark(db_documents.get_by_id(result)))

    with_flow_guard(FA, other_writer)
    monkeypatch.setattr(sa, "current_review_assignment", lambda attempt_id, stage: dict(first))   # a stale read
    requests = len(ledger(env))

    def body():
        ctx = run_apply.load_context({**_run(), "status": "running"}, "flowgate", None, [])
        row = next(r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == a)
        return assigned_review.rerequest_review_locked(ctx, row), ctx.jobs

    opened, jobs = with_flow_guard(FA, body)
    assert opened is False and jobs == []
    assert len(ledger(env)) == requests and one(env, a, "result_review", 2)["intent_kind"] is None


# ── NR §6.11: a Stop during the re-requested review, then a command ───────────

def test_a_stop_during_the_re_requested_review_keeps_its_seq_and_the_next_command_goes_on(
        env, decided, monkeypatch, starts, fork_join):
    """§6.11 with the Stop contract as it is (0061 T#7, not changed here): the Stop cancels seq 2's request, puts the
    card back to pending and invalidates its attempt; the next command opens the card again as a new attempt. The
    stopped attempt's seq 1..2 stay as its history."""
    a, _a2, _a3, _result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    res = env["client"].post(f"/api/v1/runs/{_run()['run_id']}/stop", json={}, headers=_h())
    assert res.status_code == 200, res.text
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "stopped")
    [req] = [l for l in ledger(env) if l["exec_id"] == second["intent_exec_id"]]
    assert req["status"] == "cancelled"
    assert node_run(env, a)["status"] == "pending"
    kept = sorted((r["seq"], r["stage_state"]) for r in assignments(env, a)
                  if r["attempt_id"] == first["attempt_id"] and r["stage"] == "result_review")
    assert [s for s, _st in kept] == [1, 2]                                    # the stopped seq is history
    command(env, "step")                                                       # the next command goes on
    assert node_run(env, a)["status"] == "open"
    assert sorted((r["seq"], r["stage_state"]) for r in assignments(env, a)
                  if r["attempt_id"] == first["attempt_id"] and r["stage"] == "result_review") == kept
    assert len({r["attempt_id"] for r in assignments(env, a)}) == 2          # a new attempt of the same card


# ── the lock order (§5.1.3-6, decision ①: no hold inside the command's guard) ──

def test_the_command_takes_no_stop_gate_inside_its_guard_and_a_stop_after_it_does_not_deadlock(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import assigned_review
    from flowgate.graph_run import fencing
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.graph_workflow.services import run_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)
    monkeypatch.setattr(assigned_review, "hold_of", lambda *a_, **k: (_ for _ in ()).throw(
        AssertionError("a node hold inside the command's guard")))
    inside, release, gate_taken = threading.Event(), threading.Event(), threading.Event()
    real = assigned_review.ensure_intent_locked
    errors, seen = [], []

    def ensure(*args, **kwargs):
        inside.set()
        release.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(assigned_review, "ensure_intent_locked", ensure)
    group = db_groups.get_by_id(FA)

    def run_command():
        try:
            run_service.start_run(group, "u_kim", 1, "step")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def stop_order():
        try:
            with fencing.exclusive(FA):                                        # a Stop: the gate first ...
                gate_taken.set()
                with_flow_guard(FA, lambda: seen.append(one(env, a, "result_review", 2) is not None))  # ... the guard
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    commander = threading.Thread(target=run_command)
    commander.start()
    assert inside.wait(20)                                                     # the command is inside its guard
    stopper = threading.Thread(target=stop_order)
    stopper.start()
    assert gate_taken.wait(5)                                                  # it holds no gate there
    release.set()
    commander.join(30)
    stopper.join(30)
    assert not commander.is_alive() and not stopper.is_alive()                 # no deadlock
    assert errors == [] and seen == [True]                                     # the Stop's guard after the commit
    assert one(env, a, "result_review", 2) is not None
