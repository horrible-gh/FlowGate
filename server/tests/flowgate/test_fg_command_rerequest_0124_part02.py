"""Independently selectable scenarios; shared fixtures remain in test_fg_command_rerequest_0124.py."""
from __future__ import annotations

from test_fg_command_rerequest_0124 import (
    A,
    B,
    EXCLUDED,
    FA,
    LIMIT,
    MEMO,
    REVIEW_RUN,
    SECOND_LOOP,
    SPENT,
    THIRD_LOOP,
    X,
    _b1_after,
    _card_spent,
    _chain,
    _counts,
    _fan,
    _first_hops,
    _h,
    _hold,
    _loop_end,
    _loop_row,
    _loops,
    _mark,
    _passes,
    _read,
    _reason,
    _refuse_starts,
    _refused,
    _rerequested_at,
    _result,
    _review_status,
    _reviewed_ai_result,
    _run,
    _run_ended,
    _run_row,
    _spent,
    _sql,
    _stage_assigned,
    _start_ai,
    _wire,
    assignments,
    b1,
    command,
    db_path,
    decided,
    edge,
    env,
    events,
    fork_join,
    json,
    ledger,
    memo_routes,
    node,
    node_run,
    one,
    put_draft,
    pytest,
    record_starts,
    roles,
    starts,
    stored,
    threading,
    traceback,
    wired,
    write_line,
)

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
@pytest.mark.parametrize("case", sorted(EXCLUDED))
def test_an_excluded_card_is_never_requested_again_by_a_command(env, decided, monkeypatch, starts, fork_join, case):
    a, a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED[case](env, a, result, first)
    rows = len(assignments(env, a))
    loops, requests = len(_loops(starts)), len(ledger(env))
    command(env, "step")
    if case in {"review_verdict_hold", "run_not_clean", "completion_missing", "person_author"}:
        # explicit reentry now covers these waits; attribution and older seqs remain fixed.
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
    _hold(env, c)  # verdict hold is retryable; the independent card Hold still blocks.
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
    held = {k: v for k, v in _wire(env, c)["intervention"].items() if k != "stop_detail"}   # F98-4: an added field
    assert held == {"kind": "needs_intervention", "reason": "review_not_passed:review_verdict_hold"}
    assert _run()["step_node_keys"] == (sorted([a, b]) if mode == "step" else None)
