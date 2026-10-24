"""flowgate.default.0688 T#2: an answer that lands after the document it was asked about.

T#1 (test_q_handoff_park_0688.py) holds a hop that submitted its document while a question of
its own was open, has the responder answer it and resumes the gate it held exactly once. The
document, though, was written on the assumption the question stated. T#2 decides with the
review gate the chain already has -- no new verdict:

  * review.resolve_review_gate refuses an approval while a question a hop of the slot raised
    before submitting is unanswered, or its answer was never put in front of a review round
    (`_answer_check_gate`): a reviewed step gets one review round that checks it (beyond a
    finite budget at most ANSWER_CHECK_EXTRA_ROUNDS; under -1 while fewer rounds than that
    were given an answer to check, issues → rework in between or not), an unreviewed step --
    or a spent round -- stops on question_answer_unverified for a human;
  * "put in front of a review round" is the durable delivery ledger the review mention
    writes (`_append_answered_questions` → workflow_events qna_answers_delivered, keyed by
    the hop's run and the answer ids) -- never a time comparison, so an answer that lands
    while a round runs is not checked by it;
  * a record the check cannot read -- a failed lookup, a question whose asking run has no
    record, the ledger -- stops on review_history_unreadable: never an approval, never a next
    hop; a hop that cannot be given (or recorded as given) its answers is not launched;
  * the hand-off's own-question probe that cannot read the questions parks the chain instead
    of handing off;
  * that review's `pass` approves and the original hand-off continues once, its `issues`
    goes through the automatic rejection and the author's rework, its `hold` stops;
  * the review / rework mentions carry the questions and their answers, each naming its item;
  * an unreviewed step's document is not auto-approved by the inbox while its hop's own
    question is open, or while that cannot be read (`hop_left_own_question_open`);
  * the answer-side resume waits only on the parked hop's own open questions, not on every
    open question in the group, and an AI-asked answer reaches it even while unrelated
    items keep the container open.

  TestAnswerCheckGate          the gate decision from the durable records (unit)
  TestUntilItPassesAnswerLoop  -1 / finite: pass+Q → answer → check issues → rework → pass+Q
                               is held; an answer that lands while a round runs
  TestAnsweredQuestionMention  review / rework clause, its delivery record, the extra-round
                               budget sentence
  TestUndeliveredAnswersAreNotLaunched  a review / rework hop whose answers cannot be read or
                               recorded: held, nothing launched (warm and cold); an unbriefed
                               review's pass is not an approval
  TestDeliveryLedger           event_logger's ledger write / read
  TestAnswerCheckAfterResume   park → answer → answer-check review → pass: approve + next hop
                               once / issues: rework, then approve + next hop once; an
                               unreviewed step is held; a purged asking run holds; trace
  TestHandoffProbeToGate       submission → hand-off → gate with an unreadable open-Q listing
  TestUnrelatedQuestions       a person's or another run's open Q does not hold the resume
  TestRegisterAnswerTrigger    the answer path reaches the resume for an AI-asked item
  TestInboxDefersApproval      an unreviewed step with its hop's own open Q is not approved
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import q_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import chain as chain_module  # noqa: E402
from modules.flow_gate.services.ai_invoke import finalize  # noqa: E402
from modules.flow_gate.services.ai_invoke import review  # noqa: E402
from modules.flow_gate.services.ai_invoke.review import (  # noqa: E402
    REVIEW_HISTORY_UNREADABLE_STOP_CODE,
    AnswerRecordsUnreadable,
)
from modules.flow_gate.services.ai_invoke.runtime import (  # noqa: E402
    QUESTION_ANSWER_UNVERIFIED_STOP_CODE,
    REVIEW_HOP_KIND,
    REWORK_HOP_KIND,
    WORK_HOP_KIND,
)
from modules.flow_gate.workflow import event_logger  # noqa: E402

from test_q_handoff_park_0688 import (  # noqa: E402,F401  (fixtures are used by name)
    API,
    GROUP,
    HOP,
    REWORK,
    SPINE,
    T_DOC,
    TR_DOC,
    FakeQuestions,
    _park_rework_hop,
    _pending,
    _run,
    gate_chain,
    handoff,
    responder,
)


# Review rows carry now_iso() (JST); answer / sequence / item rows a DB default (UTC).
REVIEWED_AT = "2026-10-11T09:00:00+09:00"
ANSWERED_AFTER = "2026-10-11T01:30:00Z"          # 10:30 JST
ANSWERED_BEFORE = "2026-10-10 23:00:00"          # 08:00 JST (naive = UTC)
CHECK_REVIEWED_AT = "2026-10-11T11:00:00+09:00"  # the answer-check review
LATER_ANSWER = "2026-10-11T02:30:00Z"            # 11:30 JST
SEQUENCE_CREATED_AT = "2026-10-10T20:00:00Z"     # this chain's workflow sequence
BEFORE_THE_SEQUENCE = "2026-07-01T00:00:00Z"     # an item from long before it

_REAL_RUN_REVIEW_GATE = review.run_review_gate
_REAL_SPAWN_REVIEW = review._spawn_review_hop
_REAL_SPAWN_REWORK = review._spawn_rework_hop
_REAL_LOG_DELIVERY = event_logger.log_answer_delivery
_REAL_LIST_DELIVERIES = event_logger.list_answer_deliveries

SLOT = {"item_seq": 5, "doc_id": TR_DOC, "doc_type": "TR", "revision_no": 1,
        "review_status": "revised", "rejection_history": []}


def _record(run_id, **over):
    record = {"run_id": run_id, "group_id": GROUP, "outcome": "complete", "mode": "single",
              "doc_ref": TR_DOC, "hop_item_seq": None, "provider_id": "aip_1"}
    record.update(over)
    return record


@pytest.fixture(autouse=True)
def ledger(monkeypatch):
    """The answer delivery ledger (workflow_events qna_answers_delivered), in memory."""
    state = {"rows": [], "fail_read": None, "fail_write": None}

    def _log(**kw):
        if state["fail_write"] is not None:
            raise state["fail_write"]
        state["rows"].append(json.loads(json.dumps(
            {key: kw[key] for key in ("doc_id", "revision_no", "stage", "run_id",
                                      "chain_id", "items")})))
        return {"id": len(state["rows"])}

    def _list(doc_id):
        if state["fail_read"] is not None:
            raise state["fail_read"]
        return [dict(row) for row in state["rows"] if row["doc_id"] == doc_id]

    monkeypatch.setattr(event_logger, "log_answer_delivery", _log)
    monkeypatch.setattr(event_logger, "list_answer_deliveries", _list)
    return state


@pytest.fixture
def world(monkeypatch, ledger):
    """One reviewed slot (TR, item_seq 5) and the durable records the gate reads."""
    questions = FakeQuestions().install(monkeypatch)
    state = {
        "questions": questions,
        "ledger": ledger,
        "slot": dict(SLOT),
        "count": 1,
        "reviews": [{"id": 71, "verdict": "issues", "revision_no": 0, "created_at": REVIEWED_AT,
                     "comment": "fix", "findings": [{"locus": "a.py:1", "note": "wrong"}]}],
        "runs": {REWORK: _record(REWORK)},
    }
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(review, "_pending_review_slot", lambda doc_ref: dict(state["slot"]))
    monkeypatch.setattr(review, "resolve_review_count", lambda *a, **kw: state["count"])
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda doc_id: list(state["reviews"]))
    monkeypatch.setattr(review.db_reviews, "get_latest_by_doc",
                        lambda doc_id: (state["reviews"] or [None])[0])
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc",
                        lambda doc_ref: {"id": 1, "created_at": SEQUENCE_CREATED_AT})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sid: [
        {"item_seq": 4, "type": "T", "result_doc_id": T_DOC},
        {"item_seq": 5, "type": "TR", "result_doc_id": TR_DOC},
        {"item_seq": 6, "type": "T", "result_doc_id": None},
    ])
    monkeypatch.setattr(db_runs, "get", lambda run_id: state["runs"].get(run_id))
    return state


def _bundle(**over):
    bundle = {"doc_ref": SPINE, "target_seq": 6, "review_count_overrides": None}
    bundle.update(over)
    return bundle


def _ask(state, asker=REWORK, *, doc=TR_DOC, at=ANSWERED_AFTER, body="Which DB?",
         answer="PostgreSQL, not SQLite", **extra):
    pk = state["questions"].add(asker_run_id=asker, doc=doc, body=body, title="DB choice",
                                **extra)
    if at is not False:
        state["questions"].answer(pk, at=at, body=answer)
    return pk


def _answer_ids(state, pk):
    return [row["id"] for row in state["questions"].answers.get(pk, [])]


def _start_review(state, run_id, slot=None):
    """A review round starts: the REAL mention builder gives it the answers no round has
    been given yet and records that delivery under ``run_id``."""
    return review._append_answered_questions(
        "REVIEW BASE", _bundle(), dict(slot or state["slot"]), REVIEW_HOP_KIND,
        group_id=GROUP, run_id=run_id)


def _verdict(state, rid, verdict, revision, run_id=None, created_at=REVIEWED_AT):
    """The round's verdict row lands (newest first, like document_reviews.list_by_doc)."""
    state["reviews"].insert(0, {
        "id": rid, "verdict": verdict, "revision_no": revision, "created_at": created_at,
        "comment": f"round {rid}", "review_run_id": run_id,
        "findings": [] if verdict == "pass" else [{"locus": f"f{rid}", "note": f"n{rid}"}]})


# ═══════════════════════════ the gate decision (unit) ═══════════════════════════

class TestAnswerCheckGate:
    def test_a_last_round_rework_s_answer_buys_one_answer_check_review(self, world):
        pk = _ask(world)
        assert review._derive_review_gate(_bundle())["approve_first"] is True   # T#1's gate

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["round_no"], gate["answer_check"]) == (
            REVIEW_HOP_KIND, 2, True)
        assert gate["answer_check_item_ids"] == [pk]
        assert (gate["count"], gate["limit"], gate["rounds_used"]) == (1, 1, 1)
        assert gate["slot"]["doc_id"] == TR_DOC and "approve_first" not in gate

    def test_a_round_whose_mention_carried_the_answer_has_checked_it(self, world):
        pk = _ask(world)
        assert f"item {pk}," in _start_review(world, "aiv_check")
        _verdict(world, 72, "pass", 1, "aiv_check", created_at=CHECK_REVIEWED_AT)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    @pytest.mark.parametrize("arrival", ["a_second_answer", "the_round_s_own_question"])
    def test_an_answer_that_lands_while_the_round_runs_is_not_checked_by_it(
            self, world, arrival):
        # 0688 TR0008 rev1 review: the check round's mention read the answer of 01:30Z; a new
        # answer landed at 01:45Z; the round's `pass` was registered at 02:00Z. The verdict is
        # newer than the answer, but the round never saw it -- no approval, no next hop.
        pk = _ask(world, at="2026-10-11T01:30:00Z")
        assert review.resolve_review_gate(_bundle())["round_no"] == 2
        _start_review(world, "aiv_check")
        world["runs"]["aiv_check"] = _record("aiv_check")
        if arrival == "a_second_answer":           # a person adds to the responder's answer
            world["questions"].answer(pk, at="2026-10-11T01:45:00Z", body="Use PG 16")
            expected = [pk]
        else:                                      # the round asked; a person answered it
            expected = [_ask(world, asker="aiv_check", at="2026-10-11T01:45:00Z")]
        _verdict(world, 72, "pass", 1, "aiv_check", created_at="2026-10-11T02:00:00Z")

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == expected and "approve_first" not in gate

    def test_a_pass_given_before_the_answer_is_reviewed_again_within_the_budget(self, world):
        # The reviewer asked, assumed, passed -- and the answer came later.
        world["count"] = 2
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = [{"id": 71, "verdict": "pass", "revision_no": 0,
                             "created_at": REVIEWED_AT, "findings": []}]
        pk = _ask(world)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["round_no"], gate["answer_check_item_ids"]) == (
            REVIEW_HOP_KIND, 2, [pk])

    def test_until_it_passes_always_reviews_again(self, world):
        world["count"] = -1
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = [{"id": 71, "verdict": "pass", "revision_no": 0,
                             "created_at": REVIEWED_AT, "findings": []}]
        _ask(world)
        assert review.resolve_review_gate(_bundle())["stage"] == REVIEW_HOP_KIND

    def test_a_further_answer_after_the_spent_check_round_is_held_not_reviewed_again(
            self, world):
        # count=1: round 1 issues → rework → the answer-check round 2 was given the answer and
        # passed; a hop of that round asked again and was answered. No third round.
        _ask(world)
        _start_review(world, "aiv_check_review")
        world["runs"]["aiv_check_review"] = _record("aiv_check_review")
        _verdict(world, 72, "pass", 1, "aiv_check_review", created_at=CHECK_REVIEWED_AT)
        later = _ask(world, asker="aiv_check_review", at=LATER_ANSWER)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == [later]
        assert gate["rounds_used"] == 2 and gate["slot"]["doc_id"] == TR_DOC

    def test_until_it_passes_does_not_loop_on_a_reviewer_that_passes_and_asks_again(
            self, world):
        # -1 has no extra round to spend. Round 1 passed and asked; the answer bought round 2
        # (an answer check), which passed and asked again: that second answer is held.
        world["count"] = -1
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = []
        world["runs"]["aiv_review_1"] = _record("aiv_review_1")
        _start_review(world, "aiv_review_1")
        _verdict(world, 71, "pass", 0, "aiv_review_1")
        _ask(world, asker="aiv_review_1")
        # A first pass overtaken by an answer is reviewed again (no repeat yet)...
        assert review.resolve_review_gate(_bundle())["stage"] == REVIEW_HOP_KIND
        world["runs"]["aiv_review_2"] = _record("aiv_review_2")
        _start_review(world, "aiv_review_2")
        _verdict(world, 72, "pass", 0, "aiv_review_2", created_at=CHECK_REVIEWED_AT)
        again = _ask(world, asker="aiv_review_2", at=LATER_ANSWER)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == [again]

    def test_an_unreviewed_step_is_held_instead_of_approved_on_an_assumption(self, world):
        world["count"] = 0
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = []
        world["runs"][HOP] = _record(HOP, mode="continuous", doc_ref=SPINE, hop_item_seq=5)
        pk = _ask(world, asker=HOP, doc=T_DOC)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"], gate["count"]) == (
            "stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE, 0)
        assert gate["answer_check_item_ids"] == [pk] and str(pk) in gate["detail"]

    def test_a_q_only_hop_s_answer_needs_no_check(self, world):
        # The hop stopped without its document; the document after it was written with the
        # answer in hand -- no extra review, no hold.
        world["count"] = 0
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = []
        world["runs"][HOP] = _record(HOP, mode="continuous", doc_ref=SPINE, hop_item_seq=5,
                                     outcome="none")
        _ask(world, asker=HOP, doc=T_DOC)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    def test_questions_that_are_not_about_this_slot_change_nothing(self, world):
        world["runs"]["aiv_step4"] = _record("aiv_step4", mode="continuous", doc_ref=SPINE,
                                             hop_item_seq=4)
        _ask(world, asker="aiv_step4", doc=T_DOC)            # another step's hop
        _ask(world, asker="aiv_step4", doc=T_DOC, at=False)  # ...still open
        _ask(world, asker=None, kind="human")                 # a person's own question
        # A run purged long ago, whose item predates this chain's sequence: provably not a
        # hop of any of its slots.
        _ask(world, asker="aiv_purged_long_ago", created_at=BEFORE_THE_SEQUENCE)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    @pytest.mark.parametrize("count", [1, 0])
    def test_an_unanswered_question_of_the_slot_holds_the_approval(self, world, count):
        # The hop submitted on an assumption nobody has answered -- however the gate was
        # reached (an unreadable hand-off probe, a person's resume over an escalated item).
        world["count"] = count
        if count == 0:
            world["slot"].update(revision_no=0, review_status="pending_review")
            world["reviews"] = []
        pk = _ask(world, at=False)
        assert review._derive_review_gate(_bundle())["approve_first"] is True

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == [pk] and "still unanswered" in gate["detail"]
        assert "approve_first" not in gate

    @pytest.mark.parametrize("asked_at", [None, "2026-10-11T00:10:00Z", "last week"])
    def test_a_question_whose_asking_run_has_no_record_holds_instead_of_approving(
            self, world, asked_at):
        # 0688 TR0008 rev1 review: count=1 after a pass, an answer to check -- the control
        # asks for the answer-check round. The same records with the asking run gone (purged
        # after 90 days) cannot say whether the question was about this slot: held.
        _ask(world, created_at=asked_at)
        assert review.resolve_review_gate(_bundle())["round_no"] == 2      # the control
        world["runs"].pop(REWORK)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", REVIEW_HISTORY_UNREADABLE_STOP_CODE)
        assert REWORK in gate["detail"] and "approve_first" not in gate

    @pytest.mark.parametrize("broken", ["answers", "runs", "containers", "reviews", "ledger"])
    def test_an_unreadable_record_holds_instead_of_approving(self, world, monkeypatch,
                                                              broken):
        # count=1, the last rework landed: _derive_review_gate approves. The records that
        # would say whether the answer was checked cannot be read -- no approval.
        _ask(world)
        assert review._derive_review_gate(_bundle())["approve_first"] is True

        def _down(*a, **kw):
            raise RuntimeError(f"{broken} unavailable")

        if broken == "answers":
            monkeypatch.setattr(review_answers_module(), "list_by_question_item", _down)
        elif broken == "runs":
            monkeypatch.setattr(db_runs, "get", _down)
        elif broken == "containers":
            from modules.flow_gate.db import questions as db_questions
            monkeypatch.setattr(db_questions, "get_container_by_doc", _down)
        elif broken == "ledger":
            world["ledger"]["fail_read"] = RuntimeError("ledger unavailable")
        else:
            calls = {"n": 0}
            real = review.db_reviews.list_by_doc

            def _second_read_fails(doc_id):            # the derivation reads first
                calls["n"] += 1
                if calls["n"] > 1:
                    raise RuntimeError("reviews unavailable")
                return real(doc_id)

            monkeypatch.setattr(review.db_reviews, "list_by_doc", _second_read_fails)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", REVIEW_HISTORY_UNREADABLE_STOP_CODE)
        assert "approve_first" not in gate and gate["slot"]["doc_id"] == TR_DOC
        assert f"{broken} unavailable" in gate["detail"]

    def test_an_answered_item_without_answer_rows_holds(self, world):
        pk = _ask(world)
        world["questions"].answers[pk] = []
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["stop_code"]) == ("stop", REVIEW_HISTORY_UNREADABLE_STOP_CODE)
        assert str(pk) in gate["detail"]

    @pytest.mark.parametrize("stamp", [None, "", "yesterday afternoon"])
    def test_stamps_are_never_the_evidence_either_way(self, world, stamp):
        # An answer time that cannot be read is not "checked" (an answer-check round) and a
        # verdict time that cannot be read does not undo a delivery on record.
        pk = _ask(world, at=stamp)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["answer_check"], gate["answer_check_item_ids"]) == (
            REVIEW_HOP_KIND, True, [pk])
        _start_review(world, "aiv_check")
        _verdict(world, 72, "pass", 1, "aiv_check", created_at=stamp)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    def test_a_review_without_a_run_or_a_delivery_checked_nothing(self, world):
        # A person's review (no run) and an AI review whose mention carried no answer --
        # neither is evidence that the answer was compared.
        pk = _ask(world)
        _verdict(world, 72, "pass", 1, None, created_at=CHECK_REVIEWED_AT)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["stop_code"], gate["answer_check_item_ids"]) == (
            "stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE, [pk])
        world["reviews"][0]["review_run_id"] = "aiv_manual_review"
        world["ledger"]["rows"].append({"doc_id": TR_DOC, "run_id": "aiv_other",
                                        "stage": REVIEW_HOP_KIND,
                                        "items": [{"item_id": pk,
                                                   "answer_id": _answer_ids(world, pk)[-1]}]})
        gate = review.resolve_review_gate(_bundle())
        assert gate["stop_code"] == QUESTION_ANSWER_UNVERIFIED_STOP_CODE

    def test_without_questions_an_unreadable_review_stamp_changes_nothing(self, world):
        world["reviews"][0]["created_at"] = None
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    def test_non_approval_decisions_are_never_touched(self, world, monkeypatch):
        _ask(world)

        def _never(*a, **kw):
            raise AssertionError("only approvals are checked")

        monkeypatch.setattr(review, "questions_on_slot", _never)
        world["count"] = 2                                    # a round remains: review
        assert review.resolve_review_gate(_bundle())["stage"] == REVIEW_HOP_KIND

    def test_the_sequence_bound_is_compared_as_instants_across_formats(self):
        moment = review._record_moment
        assert moment(ANSWERED_AFTER) > moment(REVIEWED_AT)
        assert moment("2026-10-11T00:00:00.500Z") > moment(REVIEWED_AT)
        assert moment("2026-10-11 00:30:00") > moment(REVIEWED_AT)  # naive = UTC
        assert moment("not a time") is None and moment(None) is None
        sequence = {"created_at": SEQUENCE_CREATED_AT}
        before = review._asked_before_the_sequence
        assert before({"created_at": BEFORE_THE_SEQUENCE}, sequence) is True
        assert before({"created_at": "2026-10-11T05:00:00+09:00"}, sequence) is False
        assert before({"created_at": None}, sequence) is False
        assert before({"created_at": BEFORE_THE_SEQUENCE}, {"created_at": None}) is False
        assert before({"created_at": BEFORE_THE_SEQUENCE}, None) is False


def review_answers_module():
    from modules.flow_gate.db import answers as db_answers
    return db_answers


# ═══════════════════════════ -1 / finite: the answer loop and a running round ═══════════════════════════

class TestUntilItPassesAnswerLoop:
    """0688 TR0008 review: under -1 a check that went `issues` → rework → a plain `pass` that
    asked again bought a new answer-check round every cycle. The gate counts the rounds that
    were given an answer to check (`_answer_checks_spent`) from the delivery ledger."""

    @pytest.fixture
    def loop(self, world):
        world["count"] = -1
        world["slot"].update(revision_no=0, review_status="pending_review")
        world["reviews"] = []
        return world

    def _round(self, state, rid, verdict, revision):
        """One review round: its mention is built (delivery recorded), then its verdict."""
        run_id = f"aiv_r{rid}"
        state["runs"][run_id] = _record(run_id)
        _start_review(state, run_id)
        _verdict(state, rid, verdict, revision, run_id)
        return run_id

    def _land_rework(self, state, revision):
        state["slot"].update(revision_no=revision, review_status="revised")

    def _spent(self, state):
        slot = state["slot"]
        return review._answer_checks_spent(
            review._questions_on_assumption(_bundle(), slot),
            review._answers_checked_by_reviews(slot, state["reviews"]))

    def test_pass_q_check_issues_rework_pass_q_is_held_not_reviewed_again(self, loop):
        # Round 1 passes and its reviewer hop asked Q1 on an assumption; the answer lands.
        r1 = self._round(loop, 71, "pass", 0)
        q1 = _ask(loop, asker=r1)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["round_no"], gate["answer_check"],
                gate["answer_check_item_ids"]) == (REVIEW_HOP_KIND, 2, True, [q1])

        # The answer check (round 2) is given Q1 and finds a conflict: rejection → rework.
        self._round(loop, 72, "issues", 0)
        assert loop["ledger"]["rows"][-1]["run_id"] == "aiv_r72"
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["round_no"]) == (REWORK_HOP_KIND, 2)
        self._land_rework(loop, 1)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["round_no"]) == (REVIEW_HOP_KIND, 3)     # -1 reviews again

        # Round 3 passes -- and its hop asked Q2 on a new assumption; the answer lands.
        r3 = self._round(loop, 73, "pass", 1)
        q2 = _ask(loop, asker=r3)
        assert self._spent(loop) == 1

        gate = review.resolve_review_gate(_bundle())

        # The slot's one answer check is spent: held for a human, no round 4, no approval.
        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == [q2] and "approve_first" not in gate
        assert (gate["count"], gate["rounds_used"]) == (-1, 3)

    def test_the_cycle_never_grants_a_second_check_however_often_it_repeats(self, loop):
        # The review's reproduction: four cycles of pass+Q → answer → check → issues →
        # rework. Only the first answer bought a check; every later approval is a hold.
        r1 = self._round(loop, 71, "pass", 0)
        _ask(loop, asker=r1)
        checks, revision, rid = [], 0, 72
        for cycle in range(4):
            gate = review.resolve_review_gate(_bundle())
            if gate["stage"] == REVIEW_HOP_KIND and gate.get("answer_check"):
                checks.append(gate["round_no"])
            else:
                assert (gate["stage"], gate["stop_code"]) == (
                    "stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
            # The loop carries on anyway (a human resumes it after rejecting by hand, say):
            # issues → rework → a pass whose hop asks again.
            self._round(loop, rid, "issues", revision)
            revision += 1
            self._land_rework(loop, revision)
            asker = self._round(loop, rid + 1, "pass", revision)
            _ask(loop, asker=asker)
            rid += 2
        gate = review.resolve_review_gate(_bundle())
        assert checks == [2]
        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)

    def test_until_it_passes_without_questions_still_reviews_until_a_pass(self, loop):
        # The -1 contract itself is untouched: issues → rework → review, then a pass approves.
        self._round(loop, 71, "issues", 0)
        assert review.resolve_review_gate(_bundle())["stage"] == REWORK_HOP_KIND
        self._land_rework(loop, 1)
        assert review.resolve_review_gate(_bundle())["round_no"] == 2
        self._round(loop, 72, "issues", 1)
        self._land_rework(loop, 2)
        assert review.resolve_review_gate(_bundle())["round_no"] == 3
        self._round(loop, 73, "pass", 2)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)
        assert loop["ledger"]["rows"] == []                  # nothing to deliver, nothing kept

    def test_a_rework_answer_given_to_the_next_ordinary_round_approves_on_its_pass(self, loop):
        # -1, round 1 issues; the rework hop asked, assumed and landed; the answer came before
        # round 2 started, so round 2 (an ordinary -1 round) was given it and passed.
        self._round(loop, 71, "issues", 0)
        self._land_rework(loop, 1)
        loop["runs"][REWORK] = _record(REWORK)
        _ask(loop)
        self._round(loop, 72, "pass", 1)
        gate = review.resolve_review_gate(_bundle())
        assert (gate["stage"], gate["approve_first"]) == (WORK_HOP_KIND, True)

    def test_a_second_answer_while_the_check_runs_neither_approves_nor_buys_a_round(
            self, loop):
        # -1: the check round was given A1; a person's A2 to the same item landed while it
        # ran. The round never saw A2 -- and A1's round still counts as the spent check, so
        # reading only the latest answer cannot hand out another round.
        r1 = self._round(loop, 71, "pass", 0)
        q1 = _ask(loop, asker=r1, at="2026-10-11T01:30:00Z")
        assert review.resolve_review_gate(_bundle())["answer_check"] is True
        loop["runs"]["aiv_r72"] = _record("aiv_r72")
        _start_review(loop, "aiv_r72")
        loop["questions"].answer(q1, at="2026-10-11T01:45:00Z", body="Correction: PG 16")
        _verdict(loop, 72, "pass", 0, "aiv_r72", created_at="2026-10-11T02:00:00Z")

        assert self._spent(loop) == 1
        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["stop_code"]) == ("stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE)
        assert gate["answer_check_item_ids"] == [q1] and "approve_first" not in gate

    def test_an_answer_that_lands_while_an_ordinary_round_runs_goes_to_the_check_round(
            self, loop):
        # count=2: round 1 issues → the rework asked, assumed and landed → round 2 starts
        # before the answer, which lands while it runs; round 2 passes. Round 3 checks it.
        loop["count"] = 2
        self._round(loop, 71, "issues", 0)
        pk = _ask(loop, at=False)
        self._land_rework(loop, 1)
        assert review.resolve_review_gate(_bundle())["round_no"] == 2      # not an approval
        loop["runs"]["aiv_r72"] = _record("aiv_r72")
        assert "Answered questions" not in _start_review(loop, "aiv_r72")
        loop["questions"].answer(pk, at=ANSWERED_AFTER)
        _verdict(loop, 72, "pass", 1, "aiv_r72", created_at=CHECK_REVIEWED_AT)

        gate = review.resolve_review_gate(_bundle())

        assert (gate["stage"], gate["round_no"], gate["answer_check"],
                gate["answer_check_item_ids"]) == (REVIEW_HOP_KIND, 3, True, [pk])
        assert "extra answer-check round after the step's 2" in review._append_engine_review_clause(
            "M", gate)


# ═══════════════════════════ the questions in the hop mentions ═══════════════════════════

class TestAnsweredQuestionMention:
    def test_the_review_hop_checks_only_answers_no_round_was_given(self, world):
        new = _ask(world, body="Which DB?\nWe assumed SQLite.", answer="PostgreSQL.")
        old = _ask(world, at=ANSWERED_BEFORE, body="Old question", answer="Old answer")
        world["reviews"][0]["review_run_id"] = "aiv_r71"
        world["ledger"]["rows"].append({"doc_id": TR_DOC, "run_id": "aiv_r71",
                                        "stage": REVIEW_HOP_KIND,
                                        "items": [{"item_id": old,
                                                   "answer_id": _answer_ids(world, old)[-1]}]})

        mention = review._append_answered_questions("BASE", _bundle(chain_id="chain_x"), SLOT,
                                                    REVIEW_HOP_KIND, group_id=GROUP,
                                                    run_id="aiv_r2")

        assert mention.startswith("BASE\n\n## Answered questions to check\n---\n")
        assert f"item {new}, on {TR_DOC}" in mention and f"item {old}," not in mention
        assert "    Which DB?\n    We assumed SQLite." in mention
        assert "Answer (ai, " + ANSWERED_AFTER + "):\n    PostgreSQL." in mention
        assert "return 'issues', put the question (Q<seq>, item <id>)" in mention
        assert "return 'hold'" in mention
        assert not any("가" <= ch <= "힣" for ch in mention.replace("BASE", ""))
        # What the hop is given is on record under its run, by answer id.
        assert world["ledger"]["rows"][-1] == {
            "doc_id": TR_DOC, "revision_no": 1, "stage": REVIEW_HOP_KIND, "run_id": "aiv_r2",
            "chain_id": "chain_x",
            "items": [{"item_id": new, "answer_id": _answer_ids(world, new)[-1],
                       "anchor_doc_id": TR_DOC}]}

    def test_the_rework_hop_follows_every_answer_about_the_document(self, world):
        new = _ask(world)
        old = _ask(world, at=ANSWERED_BEFORE, answer="Old answer")
        mention = review._append_answered_questions("BASE", _bundle(), SLOT, REWORK_HOP_KIND,
                                                    group_id=GROUP, run_id="aiv_w")
        assert "## Answered questions to apply" in mention
        assert "follow the answer" in mention
        assert f"item {new}," in mention and f"item {old}," in mention
        row = world["ledger"]["rows"][-1]
        assert (row["stage"], row["run_id"]) == (REWORK_HOP_KIND, "aiv_w")
        assert [item["item_id"] for item in row["items"]] == [new, old]

    def test_without_an_answered_question_the_mention_is_left_alone(self, world):
        _ask(world, at=False)
        assert review._append_answered_questions("BASE", _bundle(), SLOT,
                                                 REVIEW_HOP_KIND, run_id="r") == "BASE"
        assert review._append_answered_questions("", _bundle(), SLOT, REWORK_HOP_KIND) == ""
        assert world["ledger"]["rows"] == []

    @pytest.mark.parametrize("stage", [REVIEW_HOP_KIND, REWORK_HOP_KIND])
    @pytest.mark.parametrize("broken", ["lookup", "purged_run", "ledger_write", "ledger_read"])
    def test_a_hop_whose_answers_cannot_be_read_or_recorded_raises(self, world, monkeypatch,
                                                                   stage, broken):
        # 0688 TR0008 rev1 review: the old builder swallowed this and launched the hop with
        # its base mention; its verdict / fix was later taken as the answer's comparison.
        _ask(world)
        if broken == "lookup":
            monkeypatch.setattr(review, "questions_on_slot",
                                lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
        elif broken == "purged_run":
            world["runs"].pop(REWORK)
        elif broken == "ledger_write":
            world["ledger"]["fail_write"] = RuntimeError("events table down")
        else:
            world["reviews"][0]["review_run_id"] = "aiv_r71"
            world["ledger"]["fail_read"] = RuntimeError("events table down")
        if broken == "ledger_read" and stage == REWORK_HOP_KIND:
            # A rework is given every answer; it reads no ledger -- but its own delivery
            # still has to be recorded.
            world["ledger"]["fail_write"] = RuntimeError("events table down")
        with pytest.raises(AnswerRecordsUnreadable):
            review._append_answered_questions("BASE", _bundle(), SLOT, stage,
                                              group_id=GROUP, run_id="aiv_x")
        assert world["ledger"]["rows"] == []

    def test_long_text_is_clipped(self, world):
        _ask(world, body="x" * 5000)
        mention = review._append_answered_questions("BASE", _bundle(), SLOT, REVIEW_HOP_KIND)
        assert "x" * review.ANSWERED_QUESTION_TEXT_MAX + " ..." in mention
        assert "x" * (review.ANSWERED_QUESTION_TEXT_MAX + 1) not in mention

    def test_the_answer_check_round_is_told_it_is_the_last_one(self):
        extra = review._append_engine_review_clause(
            "M", {"count": 1, "round_no": 2, "limit": 1})
        assert "an extra answer-check round after the step's 1 review round(s)" in extra
        assert "This is the LAST round" in extra
        ordinary = review._append_engine_review_clause(
            "M", {"count": 2, "round_no": 1, "limit": 2})
        assert "round 1 of 2" in ordinary and "LAST round" not in ordinary

    def test_spawned_review_and_rework_hops_carry_the_clause(self, world, monkeypatch):
        from modules.flow_gate.services import invoke_mention_service, workflow_decision_service

        pk = _ask(world)
        started: list = []
        monkeypatch.setattr(svc, "start_run", lambda **kw: started.append(kw) or {"ok": True})
        monkeypatch.setattr(review, "resolve_reviewer", lambda *a, **kw: "aip_2")
        monkeypatch.setattr(review, "resolve_step_executor", lambda *a, **kw: "aip_1")
        monkeypatch.setattr(workflow_decision_service, "request_review", lambda **kw: {
            "token": "t", "token_id": "ti", "scratch_dir": "s", "mention": "REVIEW BASE"})
        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "t", "token_id": "ti", "scratch_dir": "s", "mention": "REWORK BASE"})
        gate = {"slot": dict(SLOT), "count": 1, "round_no": 2, "limit": 1,
                "answer_check": True}

        review._spawn_review_hop(GROUP, _bundle(), gate)
        review._spawn_rework_hop(GROUP, _bundle(), gate)
        review_mention = started[0]["issue_builder"](ai_run_id="aiv_r")["mention"]
        rework_issued = started[1]["issue_builder"](ai_run_id="aiv_w")

        assert review_mention.startswith("REVIEW BASE\n\n## Automated follow-up")
        assert "extra answer-check round" in review_mention
        assert review_mention.index("## Automated follow-up") < review_mention.index(
            "## Answered questions to check")
        assert f"item {pk}," in review_mention
        assert rework_issued["mention"].startswith("REWORK BASE\n\n## Answered questions to apply")
        assert rework_issued["raw_token"] == "t"
        assert [(row["run_id"], row["stage"]) for row in world["ledger"]["rows"]] == [
            ("aiv_r", REVIEW_HOP_KIND), ("aiv_w", REWORK_HOP_KIND)]


# ═══════════════════════════ a hop that cannot be given its answers is not launched ═══════════════════════════

class TestUndeliveredAnswersAreNotLaunched:
    """0688 TR0008 rev1 review: a check review / rework built while the questions could not be
    read ran with its base mention, and its later verdict / fix approved the slot."""

    @pytest.fixture
    def launch(self, world, monkeypatch):
        from modules.flow_gate.services import invoke_mention_service, workflow_decision_service

        state = {"launched": [], "parked": [], "settled": [], "next_hops": [],
                 "rejected": []}

        def _start_run(**kw):
            # Like admission.start_run: the token + mention are built before the run starts,
            # and a builder that raises launches nothing.
            issued = kw["issue_builder"](ai_run_id=f"aiv_{kw['hop_kind']}_{len(state['launched'])}")
            state["launched"].append((kw["hop_kind"], issued["mention"]))
            return {"ok": True}

        monkeypatch.setattr(svc, "start_run", _start_run)
        monkeypatch.setattr(svc, "_spawn_review_hop", _REAL_SPAWN_REVIEW)
        monkeypatch.setattr(svc, "_spawn_rework_hop", _REAL_SPAWN_REWORK)
        monkeypatch.setattr(review, "resolve_reviewer", lambda *a, **kw: "aip_2")
        monkeypatch.setattr(review, "resolve_step_executor", lambda *a, **kw: "aip_1")
        monkeypatch.setattr(workflow_decision_service, "request_review", lambda **kw: {
            "token": "t", "token_id": "ti", "scratch_dir": "s", "mention": "REVIEW BASE"})
        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "t", "token_id": "ti", "scratch_dir": "s", "mention": "REWORK BASE"})
        monkeypatch.setattr(svc, "_auto_resume", {})
        monkeypatch.setattr(svc, "_park_handoff",
                            lambda run, bundle, stop_code: state["parked"].append(stop_code))
        monkeypatch.setattr(svc, "_settle_gate_pass", lambda group_id, slot, bundle, run: (
            state["settled"].append(slot["doc_id"]) or "continue"))
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda group_id, bundle: (
            state["next_hops"].append(bundle)))
        monkeypatch.setattr(chain_module, "park_for_server_test_run", lambda *a: False)
        monkeypatch.setattr(svc, "_log_review_annotation_failure", lambda *a, **kw: None)
        monkeypatch.setattr(svc, "_auto_reject", lambda slot, latest, bundle: (
            state["rejected"].append((latest or {}).get("id")) or {"ok": True}))
        state["world"] = world
        return state

    def _gate(self, launch, run_id="aiv_hop"):
        run = {"run_id": run_id, "group_id": GROUP}
        return _REAL_RUN_REVIEW_GATE(GROUP, {**_bundle(), "chain_id": "chain_0688"}, run), run

    def test_a_check_review_whose_answers_cannot_be_read_is_held_not_launched(
            self, launch, monkeypatch):
        world = launch["world"]
        pk = _ask(world)                     # count=1, last rework landed → the check round
        real = review.answered_questions_on_slot
        monkeypatch.setattr(review, "answered_questions_on_slot",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("questions down")))

        started, run = self._gate(launch)

        assert started is False and launch["parked"] == [REVIEW_HISTORY_UNREADABLE_STOP_CODE]
        assert launch["launched"] == [] and world["ledger"]["rows"] == []
        assert launch["settled"] == [] and launch["next_hops"] == []
        assert svc.peek_auto_resume(GROUP) is None
        assert "questions down" in run["review_reject_detail"]

        # The lookup is back. A `pass` from a review whose mention carried no answer (the
        # rev1 reproduction: a REVIEW BASE-only round) is no evidence of a comparison:
        # nothing is approved and no next hop starts.
        monkeypatch.setattr(review, "answered_questions_on_slot", real)
        _verdict(world, 72, "pass", 1, "aiv_unbriefed", created_at=CHECK_REVIEWED_AT)
        started, run = self._gate(launch, "aiv_unbriefed")
        assert started is False
        assert launch["parked"][-1] == QUESTION_ANSWER_UNVERIFIED_STOP_CODE
        assert launch["settled"] == [] and launch["next_hops"] == []
        assert review.resolve_review_gate(_bundle())["answer_check_item_ids"] == [pk]

    def test_after_the_outage_the_check_is_launched_with_its_answers_and_only_then_approves(
            self, launch, monkeypatch):
        world = launch["world"]
        pk = _ask(world)
        real = review.answered_questions_on_slot
        monkeypatch.setattr(review, "answered_questions_on_slot",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("questions down")))
        assert self._gate(launch)[0] is False
        monkeypatch.setattr(review, "answered_questions_on_slot", real)

        # A person resumes: the same gate launches the check round, briefed and recorded.
        assert self._gate(launch, "aiv_resumed")[0] is True
        (kind, mention), = launch["launched"]
        assert kind == REVIEW_HOP_KIND and f"item {pk}," in mention
        (row,) = world["ledger"]["rows"]
        assert (row["run_id"], row["items"][0]["item_id"]) == ("aiv_review_0", pk)
        assert launch["settled"] == [] and launch["next_hops"] == []

        # That round's pass approves and continues once.
        _verdict(world, 72, "pass", 1, "aiv_review_0", created_at=CHECK_REVIEWED_AT)
        assert self._gate(launch, "aiv_review_0")[0] is True
        assert launch["settled"] == [TR_DOC] and len(launch["next_hops"]) == 1

    def test_a_rework_whose_answers_cannot_be_read_is_held_not_launched(self, launch,
                                                                        monkeypatch):
        world = launch["world"]
        pk = _ask(world)
        _start_review(world, "aiv_check")                       # the check round was given it
        _verdict(world, 72, "issues", 1, "aiv_check", created_at=CHECK_REVIEWED_AT)
        assert review.resolve_review_gate(_bundle())["stage"] == REWORK_HOP_KIND
        monkeypatch.setattr(review, "answered_questions_on_slot",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("questions down")))

        started, run = self._gate(launch)

        # The rejection still lands first (10-1), but no fix runs without the answers.
        assert started is False and launch["rejected"] == [72]
        assert launch["parked"] == [REVIEW_HISTORY_UNREADABLE_STOP_CODE]
        assert launch["launched"] == [] and launch["settled"] == []
        assert launch["next_hops"] == [] and svc.peek_auto_resume(GROUP) is None
        assert [row["run_id"] for row in world["ledger"]["rows"]] == ["aiv_check"]
        assert pk in [item["item_id"] for item in world["ledger"]["rows"][0]["items"]]

    def test_a_rework_whose_delivery_cannot_be_recorded_is_held_not_launched(self, launch):
        world = launch["world"]
        _ask(world)
        _start_review(world, "aiv_check")
        _verdict(world, 72, "issues", 1, "aiv_check", created_at=CHECK_REVIEWED_AT)
        world["ledger"]["fail_write"] = RuntimeError("events table down")

        started, run = self._gate(launch)

        assert started is False and launch["parked"] == [REVIEW_HISTORY_UNREADABLE_STOP_CODE]
        assert launch["launched"] == [] and launch["settled"] == []
        assert "events table down" in run["review_reject_detail"]


# ═══════════════════════════ the ledger itself ═══════════════════════════

class TestDeliveryLedger:
    def test_a_delivery_is_written_and_read_back_by_document_only(self, monkeypatch):
        from modules.flow_gate.db import connection
        from modules.flow_gate.db import workflow_events as db_events

        created: list = []
        monkeypatch.setattr(db_events, "create", lambda data: created.append(data) or {"id": 1})
        _REAL_LOG_DELIVERY(project_id="flowgate", actor_user_id="usr_admin", group_id=GROUP,
                           doc_id=TR_DOC, revision_no=1, stage=REVIEW_HOP_KIND,
                           run_id="aiv_r2", chain_id="chain_0688",
                           items=[{"item_id": 501, "answer_id": 9001, "anchor_doc_id": TR_DOC}])
        (row,) = created
        assert (row["event_type"], row["group_id"]) == ("qna_answers_delivered", GROUP)
        meta = json.loads(row["metadata"])
        assert (meta["doc_id"], meta["run_id"], meta["items"][0]["answer_id"]) == (
            TR_DOC, "aiv_r2", 9001)

        queries: list = []

        class _Store:
            def _fetch_all(self, sql, params):
                queries.append((sql, params))
                return [{"id": 1, "metadata": row["metadata"]},
                        {"id": 2, "metadata": "{not json"},
                        {"id": 3, "metadata": json.dumps({"doc_id": TR_DOC + "X"})}]

        monkeypatch.setattr(connection, "get_store", lambda: _Store())
        assert _REAL_LIST_DELIVERIES(TR_DOC) == [meta]
        (sql, params), = queries
        assert "event_type = ?" in sql and params[0] == "qna_answers_delivered"
        assert params[1] == f'%"doc_id": "{TR_DOC}"%'

    def test_a_ledger_that_cannot_be_read_raises(self, monkeypatch):
        from modules.flow_gate.db import connection

        class _Down:
            def _fetch_all(self, sql, params):
                raise RuntimeError("down")

        monkeypatch.setattr(connection, "get_store", lambda: _Down())
        with pytest.raises(RuntimeError):
            _REAL_LIST_DELIVERIES(TR_DOC)


# ═══════════════════════════ park → answer → the answer check (resume path) ═══════════════════════════

def _answer_rework_question(chain, pk, at=ANSWERED_AFTER):
    chain["questions"].answer(pk, at=at, body="Use PostgreSQL")
    for row in chain["reviews"]:
        row.setdefault("created_at", REVIEWED_AT)
    return q_service.auto_resume_answered_chain(
        doc_id=TR_DOC, api_base_url=API, responder_run={"run_id": "aiv_responder_1"})


def _review_run():
    return {"run_id": "aiv_review", "group_id": GROUP, "doc_ref": TR_DOC, "mode": "single",
            "hop_kind": REVIEW_HOP_KIND, "status": "finished"}


class TestAnswerCheckAfterResume:
    @pytest.fixture
    def last_round(self, gate_chain, monkeypatch, ledger):
        """count=1: review 71 issues → this rework hop asked, assumed, landed revision 1.

        The review hop the gate launches builds its mention for real (the delivery is
        recorded under its run, aiv_review); only the provider run is not started."""
        gate_chain["count"] = 1
        gate_chain["ledger"] = ledger
        rejected: list = []

        def _auto_reject(slot, latest, bundle):
            rejected.append((slot["doc_id"], (latest or {}).get("id")))
            gate_chain["doc"]["doc_review_status"] = "rejected"
            return {"ok": True}

        def _spawn_review(group_id, bundle, gate):
            review._append_answered_questions("REVIEW BASE", bundle, gate["slot"],
                                              REVIEW_HOP_KIND, group_id=group_id,
                                              run_id="aiv_review")
            gate_chain["review_hops"].append((bundle, gate))
            return {"ok": True, "run_id": "aiv_review", "chain_id": bundle.get("chain_id")}

        monkeypatch.setattr(svc, "_auto_reject", _auto_reject)
        monkeypatch.setattr(svc, "_spawn_review_hop", _spawn_review)
        gate_chain["rejected"] = rejected
        return gate_chain

    def _resume_into_the_check(self, chain):
        pk = _park_rework_hop(chain)
        trace = _answer_rework_question(chain, pk)
        return pk, trace

    def test_the_answer_resumes_into_one_answer_check_review_not_an_approval(self, last_round):
        pk, trace = self._resume_into_the_check(last_round)

        assert trace is not None and trace["requester_run_id"] == REWORK
        assert trace["resumed_run_id"] == "aiv_review"
        assert trace["answer_check_item_ids"] == [pk]
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        # No approval and no next hop before the check; exactly one review hop.
        assert last_round["settle"] == [] and last_round["started"] == []
        assert last_round["rework_hops"] == []
        assert len(last_round["review_hops"]) == 1
        bundle, gate = last_round["review_hops"][0]
        assert (gate["stage"], gate["round_no"], gate["answer_check"]) == (
            REVIEW_HOP_KIND, 2, True)
        assert gate["answer_check_item_ids"] == [pk]
        # The original hand-off rides on: chain, target, picks; the review is gate-owned.
        assert (bundle["chain_id"], bundle["target_seq"], bundle["last_stage"],
                bundle["rounds_before"]) == ("chain_0688", 6, REVIEW_HOP_KIND, 1)
        assert bundle["review_count_overrides"] == {"5": 1}
        assert svc.peek_auto_resume(GROUP)["last_stage"] == REVIEW_HOP_KIND
        # Its mention's delivery is on record, keyed by its run and the answer.
        (row,) = last_round["ledger"]["rows"]
        assert (row["run_id"], row["chain_id"], row["items"][0]["item_id"]) == (
            "aiv_review", "chain_0688", pk)

    def test_a_consistent_answer_passes_and_continues_the_hand_off_exactly_once(
            self, last_round):
        self._resume_into_the_check(last_round)
        last_round["reviews"].insert(0, {"id": 72, "verdict": "pass", "revision_no": 1,
                                         "created_at": CHECK_REVIEWED_AT, "findings": [],
                                         "review_run_id": "aiv_review"})
        queued = dict(svc.peek_auto_resume(GROUP))

        started = _REAL_RUN_REVIEW_GATE(GROUP, queued, _review_run())

        assert started is True
        assert len(last_round["settle"]) == 1 and last_round["settle"][0]["doc_id"] == TR_DOC
        assert len(last_round["started"]) == 1          # no extra rework, one next hop
        hop = last_round["started"][0]
        assert (hop["mode"], hop["doc_ref"], hop["chain_id"]) == ("continuous", SPINE,
                                                                   "chain_0688")
        assert last_round["rework_hops"] == [] and last_round["rejected"] == []

    def test_a_conflicting_answer_is_reworked_before_the_chain_moves_on(self, last_round):
        pk, _ = self._resume_into_the_check(last_round)
        last_round["reviews"].insert(0, {
            "id": 72, "verdict": "issues", "revision_no": 1, "created_at": CHECK_REVIEWED_AT,
            "comment": "contradicts the answer", "review_run_id": "aiv_review",
            "findings": [{"locus": f"Q1 (item {pk})", "note": "uses SQLite; answer says PostgreSQL"}],
        })

        # The check review's verdict: rejected with its findings, the author reworks.
        assert _REAL_RUN_REVIEW_GATE(GROUP, dict(svc.peek_auto_resume(GROUP)),
                                     _review_run()) is True
        assert last_round["rejected"] == [(TR_DOC, 72)]
        assert len(last_round["rework_hops"]) == 1
        assert last_round["settle"] == [] and last_round["started"] == []
        rework_bundle, rework_gate = last_round["rework_hops"][0]
        assert rework_gate["stage"] == REWORK_HOP_KIND and rework_gate["reject_first"] is True
        assert rework_bundle["chain_id"] == "chain_0688"

        # The rework lands revision 2: the answer was given to review 72, the budget is
        # spent -- approve and continue, once, with no further review and no hold.
        last_round["doc"].update(revision_no=2, doc_review_status="revised")
        rework_run = {**_review_run(), "run_id": "aiv_rework2", "hop_kind": REWORK_HOP_KIND}
        assert _REAL_RUN_REVIEW_GATE(GROUP, dict(svc.peek_auto_resume(GROUP)),
                                     rework_run) is True
        assert len(last_round["settle"]) == 1 and len(last_round["started"]) == 1
        assert len(last_round["review_hops"]) == 1 and len(last_round["rework_hops"]) == 1

    def test_an_unreviewed_step_is_held_for_a_human_after_the_answer(self, gate_chain,
                                                                     monkeypatch):
        gate_chain["count"] = 0
        gate_chain["doc"].update(doc_review_status="pending_review", revision_no=0)
        gate_chain["reviews"] = []
        records = {HOP: _record(HOP, mode="continuous", doc_ref=SPINE, hop_item_seq=5,
                                stop_code="question_pending")}
        monkeypatch.setattr(db_runs, "get", lambda run_id: records.get(run_id))
        pk = gate_chain["questions"].add(asker_run_id=HOP, doc=T_DOC)
        run = _run()
        svc._auto_resume[GROUP] = _pending()
        svc._maybe_auto_resume_hop(run)
        assert gate_chain["handoff"]["paused"].rows[GROUP]["stop_code"] == "question_pending"

        gate_chain["questions"].answer(pk, at=ANSWERED_AFTER)
        assert q_service.auto_resume_answered_chain(doc_id=T_DOC, api_base_url=API) is None

        # Neither approved nor moved past: the row is the human's card, chain intact.
        assert gate_chain["settle"] == [] and gate_chain["started"] == []
        assert gate_chain["review_hops"] == [] and gate_chain["rework_hops"] == []
        row = gate_chain["handoff"]["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"], row["chain_id"]) == (
            "system", QUESTION_ANSWER_UNVERIFIED_STOP_CODE, HOP, "chain_0688")
        assert row["continuation_target_seq"] == 6
        assert chain_module._paused_row_stop_reason(row).startswith(
            "This step's document was submitted while a query of its own was open")
        # Not a question park any more: further answer-side resumes do nothing.
        assert q_service.auto_resume_answered_chain(doc_id=T_DOC, api_base_url=API) is None
        assert gate_chain["settle"] == [] and gate_chain["started"] == []

    def test_a_question_whose_asking_run_is_gone_holds_the_cold_resume(self, last_round):
        # 0688 TR0008 rev1 review: an earlier hop of the slot asked (its run since purged);
        # the rework hop parks on its own question; the answer resumes the gate cold. Whether
        # the purged run's question was about this slot cannot be told -- no approval, no
        # review, no next hop.
        last_round["questions"].add(asker_run_id="aiv_purged_work_hop", doc=T_DOC,
                                    created_at="2026-10-11T00:00:00Z")
        pk = _park_rework_hop(last_round)
        assert _answer_rework_question(last_round, pk) is None

        assert last_round["settle"] == [] and last_round["started"] == []
        assert last_round["review_hops"] == [] and last_round["rework_hops"] == []
        row = last_round["handoff"]["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"], row["chain_id"]) == (
            "system", REVIEW_HISTORY_UNREADABLE_STOP_CODE, REWORK, "chain_0688")
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        assert last_round["settle"] == [] and last_round["started"] == []

    def test_a_question_whose_asking_run_is_gone_parks_the_warm_gate(self, last_round,
                                                                     monkeypatch):
        self._resume_into_the_check(last_round)
        last_round["reviews"].insert(0, {"id": 72, "verdict": "pass", "revision_no": 1,
                                         "created_at": CHECK_REVIEWED_AT, "findings": [],
                                         "review_run_id": "aiv_review"})
        last_round["questions"].add(asker_run_id="aiv_purged_work_hop", doc=T_DOC)
        parked: list = []
        monkeypatch.setattr(svc, "_park_handoff",
                            lambda run, bundle, stop_code: parked.append(stop_code))

        assert _REAL_RUN_REVIEW_GATE(GROUP, dict(svc.peek_auto_resume(GROUP)),
                                     _review_run()) is False
        assert parked == [REVIEW_HISTORY_UNREADABLE_STOP_CODE]
        assert last_round["settle"] == [] and last_round["started"] == []

    def test_an_unreadable_answer_after_the_check_parks_the_warm_gate(self, last_round,
                                                                      monkeypatch):
        # The answer-check review passed, but the answers cannot be read when its hand-off
        # runs the gate: parked on the unreadable history, not approved, no next hop.
        pk, _ = self._resume_into_the_check(last_round)
        last_round["reviews"].insert(0, {"id": 72, "verdict": "pass", "revision_no": 1,
                                         "created_at": CHECK_REVIEWED_AT, "findings": [],
                                         "review_run_id": "aiv_review"})
        parked: list = []
        monkeypatch.setattr(svc, "_park_handoff",
                            lambda run, bundle, stop_code: parked.append(stop_code))

        def _down(item_id):
            raise RuntimeError("answers unavailable")

        monkeypatch.setattr(review_answers_module(), "list_by_question_item", _down)

        assert _REAL_RUN_REVIEW_GATE(GROUP, dict(svc.peek_auto_resume(GROUP)),
                                     _review_run()) is False
        assert parked == [REVIEW_HISTORY_UNREADABLE_STOP_CODE]
        assert last_round["settle"] == [] and last_round["started"] == []
        assert len(last_round["review_hops"]) == 1 and last_round["rework_hops"] == []

    def test_a_cold_check_review_that_cannot_be_given_its_answers_restores_the_park(
            self, last_round, monkeypatch):
        # The answer resumes the gate cold into the check round, but its mention cannot read
        # the answers: nothing is launched, the question park is restored (no approval, no
        # next hop); once readable, the next resume launches the briefed round.
        real = review.answered_questions_on_slot
        monkeypatch.setattr(review, "answered_questions_on_slot",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("questions down")))
        pk = _park_rework_hop(last_round)
        assert _answer_rework_question(last_round, pk) is None

        assert last_round["review_hops"] == [] and last_round["ledger"]["rows"] == []
        assert last_round["settle"] == [] and last_round["started"] == []
        row = last_round["handoff"]["paused"].rows[GROUP]
        assert (row["stop_code"], row["stop_run_id"]) == ("question_pending", REWORK)
        assert svc.peek_auto_resume(GROUP) is None

        monkeypatch.setattr(review, "answered_questions_on_slot", real)
        trace = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)
        assert trace is not None and trace["answer_check_item_ids"] == [pk]
        assert len(last_round["review_hops"]) == 1
        assert last_round["ledger"]["rows"][0]["run_id"] == "aiv_review"
        assert last_round["settle"] == [] and last_round["started"] == []

    def test_the_hold_code_is_a_human_review_stop(self):
        assert QUESTION_ANSWER_UNVERIFIED_STOP_CODE in svc.REVIEW_STOP_CODES
        assert svc.is_resumable(QUESTION_ANSWER_UNVERIFIED_STOP_CODE) is False
        assert QUESTION_ANSWER_UNVERIFIED_STOP_CODE not in svc.PARK_NOTIFY_STOP_CODES
        text = svc._stop_reason_text(QUESTION_ANSWER_UNVERIFIED_STOP_CODE, {})
        assert "A human must decide" in text


# ═══════════════════════════ submission → hand-off → gate, open-Q listing unreadable ═══════════════════════════

class TestHandoffProbeToGate:
    def test_an_unreadable_open_question_listing_never_reaches_an_approval(
            self, gate_chain, monkeypatch):
        # 0688 TR0008 rev1 review: count=0, the work hop submitted TR on an assumption its
        # own unanswered question states; only the group's open-container listing fails --
        # containers and items read fine.
        from modules.flow_gate.db import questions as db_questions

        gate_chain["count"] = 0
        gate_chain["doc"].update(doc_review_status="pending_review", revision_no=0)
        gate_chain["reviews"] = []
        records = {HOP: _record(HOP, mode="continuous", doc_ref=SPINE, hop_item_seq=5)}
        monkeypatch.setattr(db_runs, "get", lambda run_id: records.get(run_id))
        pk = gate_chain["questions"].add(asker_run_id=HOP, doc=T_DOC)

        def _listing_down(group_id):
            raise RuntimeError("open-question listing unavailable")

        monkeypatch.setattr(db_questions, "list_open_doc_ids_by_group", _listing_down)

        # 1. Submission: the inbox's probe cannot read -- not approved, handed to the engine.
        paused = gate_chain["handoff"]["paused"]
        envelope, queued, approvals, stops = _inbox_submit(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", paused.get_by_group)
        assert approvals == [] and envelope["continuation_review_pending"] is True

        # 2. Hand-off: the engine's probe cannot read either -- parked, the gate never runs.
        run = _run()
        svc._auto_resume[GROUP] = _pending()
        svc._maybe_auto_resume_hop(run)
        row = paused.rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
            "system", REVIEW_HISTORY_UNREADABLE_STOP_CODE, HOP)
        assert gate_chain["handoff"]["gate"] == [] and gate_chain["handoff"]["dispatched"] == []
        assert approvals == [] and gate_chain["started"] == []

        # 3. The real gate, however it is reached afterwards (a person's [이어서 진행], a
        #    replay): the unanswered question of the hop that submitted keeps it unapproved.
        gate = review.resolve_review_gate({**_pending(), "chain_id": "chain_0688"})
        assert (gate["stage"], gate["stop_code"], gate["answer_check_item_ids"]) == (
            "stop", QUESTION_ANSWER_UNVERIFIED_STOP_CODE, [pk])
        assert _REAL_RUN_REVIEW_GATE(GROUP, {**_pending(), "chain_id": "chain_0688"},
                                     _run(run_id="aiv_resumed")) is False
        assert paused.rows[GROUP]["stop_code"] == QUESTION_ANSWER_UNVERIFIED_STOP_CODE
        assert approvals == [] and gate_chain["started"] == []
        assert gate_chain["review_hops"] == [] and gate_chain["rework_hops"] == []


# ═══════════════════════════ unrelated open questions do not hold the resume ═══════════════════════════

class TestUnrelatedQuestions:
    def test_a_person_s_or_another_run_s_open_question_does_not_hold_the_chain(
            self, gate_chain):
        gate_chain["count"] = 2                       # the next round is an ordinary review
        pk = _park_rework_hop(gate_chain)
        questions = gate_chain["questions"]
        questions.add(asker_run_id=None, kind="human", doc=TR_DOC)       # same container
        questions.add(asker_run_id="aiv_other_run", doc=T_DOC)           # another container
        second = questions.add(asker_run_id=REWORK, doc=TR_DOC)          # its own second Q

        questions.answer(pk, at=ANSWERED_AFTER)
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        questions.answer(second, at=ANSWERED_AFTER)
        first = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)
        again = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)

        assert first is not None and first["resumed_run_id"] == "aiv_review"
        assert again is None
        assert len(gate_chain["review_hops"]) == 1
        assert questions.list_open_doc_ids_by_group(GROUP) == [T_DOC, TR_DOC]   # still open

    def test_without_a_stop_run_the_group_wide_rule_stays(self, monkeypatch):
        row = {"group_id": GROUP, "stop_run_id": None}
        assert q_service._park_waits_on_open_question(row, [T_DOC]) is True
        questions = FakeQuestions().install(monkeypatch)
        questions.add(asker_run_id="aiv_other_run")
        row["stop_run_id"] = HOP
        assert q_service._park_waits_on_open_question(row, [T_DOC]) is False
        assert q_service._park_waits_on_open_question(row, ["missing-doc"]) is True

        def _broken(qpk):
            raise RuntimeError("down")

        monkeypatch.setattr(q_service.db_question_items, "list_unanswered", _broken)
        assert q_service._park_waits_on_open_question(row, [T_DOC]) is True


# ═══════════════════════════ the answer path reaches the resume ═══════════════════════════

def _fake_store():
    store = MagicMock()

    @contextmanager
    def _transaction():
        yield store

    store.transaction.side_effect = _transaction
    return store


class TestRegisterAnswerTrigger:
    @pytest.mark.parametrize(("asker_kind", "expected_calls"), [("ai", 1), (None, 0)])
    def test_an_ai_asked_item_reaches_the_resume_while_others_stay_open(
            self, monkeypatch, asker_kind, expected_calls):
        calls: list = []
        monkeypatch.setattr(q_service, "get_store", lambda: _fake_store())
        monkeypatch.setattr(q_service.db_questions, "get_container_by_doc",
                            lambda doc_id: {"id": 1, "status": "pending",
                                            "project_id": "flowgate"})
        monkeypatch.setattr(q_service.db_question_items, "get_by_pk", lambda item_id: {
            "id": item_id, "question_id": 1, "options": "[]", "asker_kind": asker_kind})
        monkeypatch.setattr(q_service.db_answers, "insert", lambda **kw: None)
        monkeypatch.setattr(q_service.db_question_items, "increment_answer_count",
                            lambda **kw: True)
        monkeypatch.setattr(q_service.db_question_items, "list_unanswered",
                            lambda question_id: [{"id": 11}])     # an unrelated open item
        monkeypatch.setattr(q_service.db_questions, "update_status", lambda *a: None)
        monkeypatch.setattr(q_service.db_answers, "list_by_question_item",
                            lambda item_id: [{"id": 91}])
        monkeypatch.setattr(q_service, "_notify_q_answered", lambda **kw: None)
        monkeypatch.setattr(q_service, "auto_resume_answered_chain",
                            lambda **kw: calls.append(kw) or {"resumed_run_id": "aiv_x"})

        result = q_service.register_answer(doc_id=TR_DOC, item_id=10, body="answer",
                                           author_kind="human", author_id="u-human",
                                           auto_resume_api_base_url=API)

        assert len(calls) == expected_calls
        assert result["status"] == "pending"
        assert ("question_resume_trace" in result) is bool(expected_calls)


# ═══════════════════════════ the inbox keeps an unreviewed assumption unapproved ═══════════════════════════

class _FakeRequest:
    headers = {"x-locale": "ko"}
    base_url = "http://flowgate.test/"


_REAL_PROBE = object()


def _inbox_submit(monkeypatch, open_items=_REAL_PROBE):
    """The work hop HOP submits TR (item_seq 5, unreviewed) through the inbox self-chain.

    ``open_items`` stubs its own-question probe; left out, the probe already installed (the
    real lookup in the gate_chain harness) runs. Every approval -- the inbox's and, since
    `_settle_gate_pass` calls the same helper, the gate's -- lands in the returned list.
    """
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.api.v1.events import publisher

    queued: list = []
    settled: list = []
    stops: list = []
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id",
                        lambda doc_id: {"item_seq": 5, "type": "TR"})
    monkeypatch.setattr(db_paused, "get_by_group", lambda group_id: None)
    monkeypatch.setattr(svc, "has_active_run", lambda group_id: group_id == GROUP)
    monkeypatch.setattr(svc, "is_active_run_to_end", lambda group_id: False)
    monkeypatch.setattr(svc, "get_active_status", lambda group_id: {})
    monkeypatch.setattr(svc, "request_auto_resume",
                        lambda group_id, payload: queued.append(payload))
    monkeypatch.setattr(svc, "active_review_selection", lambda group_id: (None, None))
    monkeypatch.setattr(svc, "resolve_review_count", lambda counts, seq, doc: 0)
    monkeypatch.setattr(svc, "stamp_chain_stop", lambda envelope, stop_code, **kw: (
        stops.append(stop_code) or {**envelope, "stop_code": stop_code}))
    if open_items is not _REAL_PROBE:
        monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                            lambda group_id, run_id: list(open_items) if run_id == HOP else [])
    monkeypatch.setattr(inbox_routes, "settle_completed_step",
                        lambda **kw: settled.append(kw) or {"outcome": "continue"})
    monkeypatch.setattr(inbox_routes, "_inbox_api_base", lambda request: API)
    monkeypatch.setattr(publisher, "broadcast_event_threadsafe", lambda event: None)
    envelope = inbox_routes._continuation_self_chain(
        request=_FakeRequest(),
        token_rec={"doc_ref": SPINE, "issued_to": "usr_admin", "group_id": GROUP,
                   "continuation_target_seq": 6, "continuation_review_mode": 0,
                   "continuation_locale": "ko", "ai_run_id": HOP, "token_id": "tok"},
        project="flowgate", canonical_doc_id=TR_DOC, doc_type="TR",
    )
    return envelope, queued, settled, stops


class TestInboxDefersApproval:
    def test_its_own_open_question_keeps_the_document_unapproved_for_the_engine(
            self, monkeypatch):
        envelope, queued, settled, stops = _inbox_submit(
            monkeypatch, [{"id": 501, "asker_ai_run_id": HOP}])
        assert settled == []                                # not auto-approved
        assert len(queued) == 1 and queued[0]["doc_ref"] == SPINE
        assert envelope["continuation_review_pending"] is True
        assert stops == ["hop_handoff"]

    def test_without_one_the_unreviewed_step_is_approved_as_before(self, monkeypatch):
        envelope, queued, settled, stops = _inbox_submit(monkeypatch, [])
        assert len(settled) == 1 and settled[0]["doc_id"] == TR_DOC
        assert len(queued) == 1 and "continuation_review_pending" not in envelope
        assert envelope["continuation_next_item_seq"] == 6

    def test_an_unreadable_probe_hands_the_document_to_the_engine_unapproved(
            self, monkeypatch):
        class _Unreadable:
            def __iter__(self):
                raise RuntimeError("questions table unavailable")

        envelope, queued, settled, stops = _inbox_submit(monkeypatch, _Unreadable())
        assert settled == []                                # not approved on submission
        assert len(queued) == 1 and envelope["continuation_review_pending"] is True
        assert stops == ["hop_handoff"]
        assert svc.hop_left_own_question_open(GROUP, HOP) is True
        assert svc.hop_left_own_question_open(GROUP, None) is False
