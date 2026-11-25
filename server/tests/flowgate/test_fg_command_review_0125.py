"""a card whose result review count was spent on a revision
its loop then reworked (``revised``, no review of it) is reviewed again by the person's menu command (다음 단계 = step,
전체 실행 = full), and by nothing else.

The harness of ``test_fg_card_intervention_assigned_0119`` (real SQLite DB, routes and migrations, the stage assignment
feature on, recorders for every AI start; nothing calls a real AI, no operating Flow or DB is touched). A review loop is a
recorder: its rounds are the review rows and revisions written here (``_spend``), its end the loop row and the run-end
contact (``_loop_end``) as the engine sends them.

- 1: the reproduction — rounds 3, each round ``issues`` and a rework -> the loop's count spent: the document ``revised``,
  its latest revision not reviewed, ``needs_intervention``, no completion record;
- 2: it stays stuck on run reads (F5, a reselection), a memo, an edit request and a document approval;
- 3 / 3-a / 3-b / 3-c: a command with no memo asks one new result review (``seq`` + 1, the card's count, basis the latest
  revision) and its loop; its pass completes the card and the command's mode decides what follows (full: at once; step:
  pending until the next command, the step boundary kept; no follower: completed);
- 3-d: spent again -> ``needs_intervention`` again; 3-f: the explicit ``/ai-invoke/start`` re-review keeps a step's scope;
- 5: a person's newer revision is reviewed only once a command asks it;
- 6: one ``seq`` for concurrent / consecutive commands, none for a revision already asked or judged, one for a new one;
  6-b: a revision rejected for its ``issues`` and submitted again with no new revision (``rejected`` + submit ->
  ``revised``: its submission mark's ``rejection_count`` up, ``submission_judged`` False) is the same revision — no
  ``seq`` for it, however many commands; a new unreviewed revision only, once;
- 7: Hold, a Time Machine operation, a document not ``revised``, other reasons (``completion_missing``, a system error)
  take no re-review.
"""
from __future__ import annotations

import json

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, X, _review_status, _sql, assignments, b1, command, db_path, decided, env, flow, node_run, one, roles, starts,
    stored, wired, write_line,
)
from test_fg_card_intervention_assigned_0119 import (  # noqa: F401 — fixtures used by name
    MEMO, NOT_PASSED, REREVIEW_RUN, REWORK_RUN, _branches, _loop_end, _loop_row, _read, _reviewed_ai_result, _run_row,
    _wire, fork_join, invoke, memo_routes,
)
from test_fg_card_settings import _h
from test_fg_card_settings_review import REVIEW_RUN, _run_ended

pytestmark = pytest.mark.usefixtures("fork_join", "completions")

ROUNDS = 3
EXHAUSTED = "not_passed:review_count_exhausted"
LOOP_1, LOOP_2, LOOP_3 = "air_cmd_rereview_1", "air_cmd_rereview_2", "air_cmd_rereview_3"


@pytest.fixture()
def completions(env):
    """``flowgate.card_completion`` registered as ``startup`` does (its ports and its install mark): a node is done by its
    completion record only, as in production (``test_fg_card_intervention_0119``)."""
    from flowgate.card_completion import integration

    integration.register()
    yield
    integration.unregister()


# ── helpers ──────────────────────────────────────────────────────────────────

def _review_row(doc_id, revision_no, verdict, run_id):
    """A review the loop wrote for ``revision_no`` (``document_reviews``)."""
    from modules.flow_gate.db import document_reviews as db_reviews

    return db_reviews.insert_review(doc_id, revision_no, B, verdict, "[]", None, "t", review_run_id=run_id)


def _revised(doc_id, revision_no):
    """The loop's AI rework of a rejected result registered again (``rejected`` + submit -> ``revised``)."""
    from modules.flow_gate.db import documents as db_documents

    db_documents.update(doc_id, {"revision_no": revision_no, "doc_review_status": "revised"})


def _doc(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def _mark(doc_id):
    from flowgate.card_settings import stage_assignment as sa

    return sa.mark(_doc(doc_id))


def _basis(a):
    return {"doc_id": a["basis_doc_id"], "revision_no": int(a["basis_revision_no"]),
            "rejection_count": int(a["basis_rejection_count"])}


def _loops(starts, requested=()):
    """Every review loop asked for: the card stage's (``starts``) and the explicit route's (``requested``)."""
    return [c for c in list(starts) + list(requested) if c.get("document_review_loop")]


def _reviews(env, key):
    return sorted((a for a in assignments(env, key) if a["stage"] == "result_review"), key=lambda a: a["seq"])


def _records(env, key):
    return _sql(env["db"], "SELECT * FROM graph_card_completions WHERE node_key = ? ORDER BY created_at", (key,))


def _run(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason, run_mode, step_node_keys FROM graph_runs "
                           "WHERE group_id = ? ORDER BY rowid DESC", (FA,))[0]


def _propagations(env, key):
    return _sql(env["db"], "SELECT outcome, status FROM graph_propagations WHERE node_key = ? "
                           "ORDER BY rowid", (key,))


def _post_command(env, mode):
    """The menu command as the client sends it (``startFlowRun``: ``{revision_no: null, mode}``), any answer."""
    return env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())


def _spend(env, result, rr, run_id=REVIEW_RUN, rounds=None):
    """The assignment's loop ran its rounds: each review ``issues`` (rejected) and each rework a new revision
    (``revised``), the last one reviewed by nobody — then the loop stopped ``review_count_exhausted``."""
    rounds = int(rr["review_rounds"]) if rounds is None else rounds
    base = int(rr["basis_revision_no"])
    _loop_row(result, run_id, "rework")
    for n in range(rounds):
        _review_row(result, base + n, "issues", run_id)
    _revised(result, base + rounds)
    _loop_end(env, rr["intent_exec_id"], result, run_id, "review_count_exhausted")
    return base + rounds


def _spent(env, monkeypatch, starts, mode, follow=True):
    """An AI card whose review count 3 was spent (NR 's case). ``follow``: R -> a -> a2, R -> b (b a person's
    report, completed here), else R -> a alone. The run started with ``mode``. -> (a, a2, result, latest revision)."""
    if follow:
        a, a2, b = _branches(env)
    else:
        a = flow(env, extra_settings=(("instruction", "omit"),))
        roles(a, execution=X, review=B)
        a2 = b = None
    stored(a, "after_review_exhausted", "stop")
    stored(a, "review_count", ROUNDS)
    command(env, mode)
    assert node_run(env, a)["status"] == "open"
    key, result, rr = _reviewed_ai_result(env, monkeypatch, starts, a)
    assert (rr["review_rounds"], rr["rework_provider_id"]) == (ROUNDS, X)
    latest = _spend(env, result, rr)
    if b is not None:
        write_line(env, b, "report", "## 결과\n\n본문\n")
        assert node_run(env, b)["status"] == "done"
    run = _read(env)                                                # the screen reads the run (its settlement)
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    return a, a2, result, latest


def _pass(env, a, seq, result, run_id):
    """The review loop of ``a``'s assignment ``seq`` passed the document's current revision."""
    rr = one(env, a, "result_review", seq)
    _review_row(result, int(_doc(result)["revision_no"]), "pass", run_id)
    _loop_row(result, run_id, "review")
    _loop_end(env, rr["intent_exec_id"], result, run_id, "review_passed")


def _assert_spent(env, a, result, latest, seqs=1):
    """``a`` stuck: in review, its latest result review spent, the document ``revised`` with its latest revision
    unreviewed, intervention ``review_not_passed:review_count_exhausted``, no completion record."""
    from modules.flow_gate.db import document_reviews as db_reviews

    reviews = _reviews(env, a)
    assert len(reviews) == seqs and reviews[-1]["verdict"] == EXHAUSTED
    doc = _doc(result)
    assert (doc["doc_review_status"], int(doc["revision_no"])) == ("revised", latest)
    assert db_reviews.get_latest_for_revision(result, latest) is None
    assert node_run(env, a)["status"] == "review"
    held = {k: v for k, v in _wire(env, a)["intervention"].items() if k != "stop_detail"}   # F98-4: an added field
    assert held == {"kind": "needs_intervention", "reason": NOT_PASSED}
    assert _records(env, a) == []


# ── 1: the reproduction ─────────────────────────────────────────────────────

def test_1_a_spent_count_on_an_ai_rework_leaves_the_revised_revision_unreviewed_and_the_card_needs_intervention(
        env, decided, monkeypatch, starts):
    a, _a2, result, latest = _spent(env, monkeypatch, starts, "step", follow=False)
    assert latest == ROUNDS                                         # rev 0 reviewed + 3 reworks: rev 3 unreviewed
    _assert_spent(env, a, result, latest)
    assert [(d, r) for d, r, _m in b1(env)] == [(result, NOT_PASSED)]
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "needs_intervention")
    assert len(_loops(starts)) == 1


# ── 2: nothing but the command moves it ─────────────────────────────────────

def test_2_reads_a_memo_an_edit_request_and_a_document_approval_ask_no_new_review(
        env, decided, monkeypatch, starts, memo_routes, invoke):
    from flowgate.card_settings import review_stage
    from modules.flow_gate.graph_workflow.api import v01_contact

    post, requested = invoke
    a, _a2, result, latest = _spent(env, monkeypatch, starts, "step", follow=False)
    for _ in range(4):                                              # run reads: F5, a reselection, the polling
        _read(env)
    review_stage.reconcile(FA)                                      # the automatic reconciliation itself
    _assert_spent(env, a, result, latest)
    res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{a}/memos", json={"title": MEMO, "body": "v2 only",
                                                                           "ai_ref": True}, headers=_h())
    assert res.status_code == 201, res.text
    _read(env)
    _assert_spent(env, a, result, latest)
    res = post(result, "edit", messages=["Apply the memo."])       # the document AI's edit (no loop)
    assert res.status_code == 200, res.text
    assert [r["action_scope"] for r in requested] == ["edit"]
    _run_ended(REWORK_RUN, action_scope="edit", doc_ref=result)
    _read(env)
    assert len(_reviews(env, a)) == 1 and len(_loops(starts, requested)) == 1
    assert node_run(env, a)["status"] == "review"
    from modules.flow_gate.db import documents as db_documents
    db_documents.update(result, {"doc_review_status": "approved"})  # a person's document approval
    v01_contact.after_review_decided(FA, result, "approve")
    _read(env)
    assert node_run(env, a)["status"] == "review" and _records(env, a) == []
    assert len(_reviews(env, a)) == 1 and len(_loops(starts, requested)) == 1
    command(env, "step")                                            # an approved document is no spent revision
    assert len(_reviews(env, a)) == 1 and len(_loops(starts, requested)) == 1


# ── 3: the menu command, no memo ────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["step", "full"])
def test_3_a_command_with_no_memo_asks_one_review_of_the_latest_revision_with_the_card_s_count(
        env, decided, monkeypatch, starts, mode):
    a, a2, result, latest = _spent(env, monkeypatch, starts, mode)
    before = len(_loops(starts))
    answer = command(env, mode)
    reviews = _reviews(env, a)
    assert [r["seq"] for r in reviews] == [1, 2]
    new = reviews[-1]
    assert (new["actor"], new["provider_id"], new["author"], new["review_rounds"], new["rework_provider_id"]) == \
        ("ai", B, "ai", ROUNDS, X)
    assert (new["stage_state"], new["intent_kind"], new["verdict"]) == ("active", "execution", None)
    assert _basis(new) == _mark(result) and _basis(new)["revision_no"] == latest       # never the spent basis
    assert _basis(new) != _basis(reviews[0])
    loops = _loops(starts)
    assert len(loops) == before + 1 and loops[-1]["doc_ref"] == result
    assert loops[-1]["document_review_loop"]["review_count"] == ROUNDS
    run = _run(env)
    assert (run["status"], run["run_mode"]) == ("running", mode)
    assert answer["status"] == "running"                             # the command's answer after its jobs
    assert _wire(env, a)["intervention"] is None                     # its review works on it
    if mode == "step":
        assert json.loads(run["step_node_keys"]) == [a]              # 0124/0126: a re-review is a step target
    assert node_run(env, a2)["status"] == "pending"










# ── 3-f: the explicit API re-review keeps the step's scope ──────────────────



# ── 5: a person's newer revision ────────────────────────────────────────────



# ── 6: one request per revision ─────────────────────────────────────────────









# ── 7: guards ───────────────────────────────────────────────────────────────
