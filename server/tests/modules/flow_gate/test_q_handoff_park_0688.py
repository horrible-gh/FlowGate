"""flowgate.default.0688 T#1: a hop that SUBMITTED its document but left a question of its own
open is held at its hand-off on question_pending, the responder is dispatched (the asking
step's reviewer first, else the header pick, else the default chain), and the answer resumes
the chain exactly once through resume_chain's paused-row CAS.

0688 NR0003 §0 (H1): the responder dispatch only ever saw a hop that stopped WITHOUT its
document. A worker that followed the help text -- register the Q, state the assumption,
submit -- completed, handed off, and its question reached nobody but a person.

Every test drives the production functions (chain._maybe_auto_resume_hop /
_park_on_own_open_question, finalize.open_questions_raised_by_run /
dispatch_responder_for_open_questions / _continue_question_responder_chain,
q_service.auto_resume_answered_chain, chain.resume_chain / _paused_row_resume_state /
_system_pause_row_is_stale). Two harnesses:

  * 0359's engine harness (`env`): real admission, worker, finalize and hand-off, with a
    scripted provider that registers the Q, lands the document and queues the inbox
    hand-off exactly as the inbox self-chain does;
  * plain monkeypatched collaborators for the branch-by-branch cases.

  TestEndToEnd                 park → reviewer dispatch → answer → one resume (R1/R2/R4)
  TestHandoffPark              which hops park, which hand off unchanged, user pause, an
                               unreadable probe (parked, not handed off),
                               a replayed terminal cleanup, answer race, dispatch failure
  TestOpenQuestionLookup       group-wide lookup by asker_ai_run_id, container/item_seq routing
  TestNextQuestion             Q2 of a hand-off park still reaches its own container
  TestResumeOfHandoffPark      the park row is not "stale"; a last reviewed step is resumable
  TestReworkHopQuestionPark    a rework hop's own Q: no approval / review / next hop before the
                               answer (last finite round and a following round), then the gate
                               it held exactly once -- approve+continue, complete, review, stop
  TestQuestionParkServerTest   ...and when that gate reaches a TSR waiting on its TS's server
                               run: test_run_pending, no hop / token, then PASS / FAIL / entry
                               / an already ended run continue exactly once
  TestQuestionParkGateProgress the review/rework progress a cold resume re-derives
  TestGateHopRuntimePicks      a rework hop admitted by the real admission (mode="single", so
                               its run holds no [검수] maps): the runtime reviewer and review
                               count still choose who answers Q1, Q2 and after a restart

0688 T#2 added an answer check to the gate's approvals (review.resolve_review_gate). These
cases replay the gate T#1 held, so the module's autouse `_t1_gate_without_answer_check` passes
that check through (`review._answer_check_gate` returns the gate it was given).
test_q_answer_check_0688.py drives the same harness with the real check -- the answer
delivery ledger, the answer-check round, its holds and an unreadable record holding instead
of approving.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import answers as db_answers  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases  # noqa: E402
from modules.flow_gate.db import question_items as db_question_items  # noqa: E402
from modules.flow_gate.db import test_runs as db_test_runs  # noqa: E402
from modules.flow_gate.db import questions as db_questions  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402
from modules.flow_gate.services import q_service  # noqa: E402
from modules.flow_gate.services import test_run_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import chain as chain_module  # noqa: E402
from modules.flow_gate.services.ai_invoke import finalize  # noqa: E402
from modules.flow_gate.services.ai_invoke import review  # noqa: E402
from modules.flow_gate.services.ai_invoke import terminal  # noqa: E402
from modules.flow_gate.services.ai_invoke.runtime import (  # noqa: E402
    REVIEW_HOP_KIND,
    REWORK_HOP_KIND,
)

import test_ai_invoke_no_output_retry_0359 as base  # noqa: E402
from test_ai_invoke_no_output_retry_0359 import env  # noqa: E402,F401


GROUP = base.GROUP
SPINE = base.DOC_REF
T_DOC = f"{GROUP}.0004-T"      # where the hop's question was anchored when it asked
TR_DOC = f"{GROUP}.0005-TR"    # what the same hop then submitted (the anchor moves here)
API = "http://127.0.0.1:1/flowgate/api/v1"
HOP = "aiv_0688_hop"


class FakeQuestions:
    """question containers/items keyed by the document they are bound to."""

    _QPK = {T_DOC: 1, TR_DOC: 2}

    def __init__(self):
        self.items: dict[int, dict] = {}
        self.states: list[tuple] = []
        self.answers: dict[int, list[dict]] = {}
        self._next = 500
        self._next_answer = 9000

    def add(self, *, asker_run_id, doc=T_DOC, kind="ai", **extra) -> int:
        pk = self._next
        self._next += 1
        seq = 1 + sum(1 for r in self.items.values() if r["doc"] == doc)
        self.items[pk] = {
            "id": pk, "seq": seq, "doc": doc, "question_id": self._QPK[doc],
            "asker_kind": kind, "asker_ai_run_id": asker_run_id, "answer_count": 0,
            "responder_state": None, "responder_run_id": None, **extra,
        }
        return pk

    def answer(self, pk, *, at=None, body="answered", author_kind="ai") -> int:
        """Add an answer row (``at`` is its created_at) and return its id. The T#2 answer
        check keys what a review was given to check on the answer id, never on ``at``
        (test_q_answer_check_0688.py)."""
        self._next_answer += 1
        self.items[pk]["answer_count"] = len(self.answers.get(pk, [])) + 1
        self.answers.setdefault(pk, []).append({
            "id": self._next_answer, "question_item_id": pk, "body": body,
            "author_kind": author_kind, "created_at": at})
        return self._next_answer

    def list_by_question(self, qpk):
        return sorted((dict(r) for r in self.items.values() if r["question_id"] == qpk),
                      key=lambda r: r["seq"])

    def list_by_question_item(self, item_id):
        return [dict(a) for a in self.answers.get(item_id, [])]

    def _open(self, doc):
        return [r for r in self.items.values() if r["doc"] == doc and not r["answer_count"]]

    def list_open_doc_ids_by_group(self, group_id):
        return sorted({r["doc"] for r in self.items.values() if not r["answer_count"]})

    def get_container_by_doc(self, doc_id):
        if not any(r["doc"] == doc_id for r in self.items.values()):
            return None
        return {"id": self._QPK[doc_id], "doc_id": doc_id,
                "status": "pending" if self._open(doc_id) else "done"}

    def list_unanswered(self, qpk):
        return sorted((dict(r) for r in self.items.values()
                       if r["question_id"] == qpk and not r["answer_count"]),
                      key=lambda r: r["seq"])

    def get_by_pk(self, pk):
        row = self.items.get(pk)
        return dict(row) if row else None

    def list_by_responder_run(self, run_id):
        return [dict(r) for r in self.items.values() if r.get("responder_run_id") == run_id]

    def set_responder_state(self, pk, state, error_code=None, error_message=None, *, run_id=None):
        self.items[pk]["responder_state"] = state
        self.states.append((pk, state, run_id))
        return True

    def install(self, monkeypatch):
        monkeypatch.setattr(db_questions, "list_open_doc_ids_by_group",
                            self.list_open_doc_ids_by_group)
        monkeypatch.setattr(db_questions, "get_container_by_doc", self.get_container_by_doc)
        for name in ("list_unanswered", "get_by_pk", "list_by_responder_run",
                     "set_responder_state", "list_by_question"):
            monkeypatch.setattr(db_question_items, name, getattr(self, name))
        monkeypatch.setattr(db_answers, "list_by_question_item", self.list_by_question_item)
        return self


@pytest.fixture(autouse=True)
def _t1_gate_without_answer_check(monkeypatch):
    """This module replays the gate T#1 held; the 0688 T#2 answer check on its approvals is
    covered by test_q_answer_check_0688.py (see the module docstring)."""
    monkeypatch.setattr(review, "_answer_check_gate", lambda gate, bundle: gate)


@pytest.fixture
def responder(monkeypatch):
    """dispatch_answer_run / resolve_item / the responder resolver, all recorded."""
    state = {"calls": [], "resolved": [], "raise": None}

    def _resolve_item(doc_id, item_id):
        return {"id": item_id, "doc_id": doc_id, "title": "Q", "body": "b", "options": []}

    def _dispatch(**kw):
        state["calls"].append(kw)
        if state["raise"] is not None:
            raise state["raise"]
        return {"run_id": f"aiv_responder_{len(state['calls'])}", "status": "running"}

    real_resolver = review.resolve_question_responder_with_source

    def _resolver(reviewer_overrides, item_seq, base_provider_id, project_id,
                  doc_ref=None, review_count_overrides=None, asker_provider_id=None):
        picked = real_resolver(reviewer_overrides, item_seq, base_provider_id, project_id,
                               doc_ref=doc_ref, review_count_overrides=review_count_overrides,
                               asker_provider_id=asker_provider_id)
        state["resolved"].append({"item_seq": item_seq, "doc_ref": doc_ref, "picked": picked})
        return picked

    monkeypatch.setattr(q_answer_invoke_service, "resolve_item", _resolve_item)
    monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _dispatch)
    monkeypatch.setattr(review, "resolve_question_responder_with_source", _resolver)
    # The asking step (item_seq 5 in the unit cases) is reviewed by aip_2: tier 2.
    monkeypatch.setattr(review, "_stored_review_policy_for_item_seq",
                        lambda doc_ref, item_seq: (2, "aip_2"))
    return state


# ═══════════════════════════ engine: park → dispatch → answer → resume ═══════════════════════════

class TestEndToEnd:
    def test_completed_hop_with_its_own_open_question_parks_and_resumes_exactly_once(
            self, env, monkeypatch, responder):
        questions = FakeQuestions().install(monkeypatch)
        # The hop submitted the TR, so the spine's anchor now points at it (0059 anchoring).
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        gate_calls: list = []
        monkeypatch.setattr(review, "run_review_gate",
                            lambda *a, **kw: gate_calls.append(a) or True)
        writes: list = []
        real_upsert = env["paused"].upsert

        def _upsert(**kw):
            writes.append(kw.get("stop_code"))
            return real_upsert(**kw)

        monkeypatch.setattr(db_paused, "upsert", _upsert)

        def _worker(provider, prompt, run):
            # The help text's contract: register the Q, state the assumption, submit.
            questions.add(asker_run_id=run["run_id"])
            env["docs"].docs.append({"doc_id": TR_DOC, "seq": 5, "status": "open"})
            # What inbox_routes._continuation_self_chain._hand_off_to_engine queues.
            svc.request_auto_resume(GROUP, {
                "doc_ref": SPINE, "target_seq": 2, "to_end": False, "review_mode": False,
                "instruction_mode": None, "auto_approve_item_seqs": [], "locale": "ko",
                "issued_to": "usr_admin", "api_base_url": API,
            })
            run["exit_code"] = 0
            run["last_message"] = "TR submitted; the assumption is recorded as a Q"
            run["last_message_received"] = True
            return "started_ok", None

        monkeypatch.setattr(svc, "_cli_execute", _worker)

        started = base._start(env, provider_id="aip_1")
        run_id = started["run_id"]
        base._wait_finished(run_id)
        base._wait_until(lambda: len(responder["calls"]) == 1, message="responder dispatch")
        base._wait_until(
            lambda: (svc.get_run_record(run_id) or {}).get("stop_code") == "question_pending",
            message="question_pending resettle",
        )
        base._wait_until(lambda: "lease_release" in (svc.get_run_record(run_id) or {}).get(
            "_terminal_done", ()), message="lease release")

        # Held BEFORE the gate: no review hop, no next hop, nothing left in the queue.
        assert gate_calls == []
        assert svc.peek_auto_resume(GROUP) is None
        assert "hop_handoff_failed" not in writes and writes[-1] == "question_pending"
        row = env["paused"].rows[GROUP]
        record = svc.get_run_record(run_id)
        assert row["stop_kind"] == "system" and row["stop_code"] == "question_pending"
        assert row["stop_run_id"] == run_id and row["doc_ref"] == SPINE
        assert row["paused_by"] == "usr_admin" and row["continuation_target_seq"] == 2
        assert row["chain_id"] == record["chain_id"]
        assert record["resumable"] is True
        assert record["stop_reason"].startswith("This hop submitted its document")
        assert env["signals"] == []          # a question is not a failure

        # The reviewer of the step answers the Q that lives on the T (not the moved anchor).
        call = responder["calls"][0]
        assert call["item"]["id"] == next(iter(questions.items))
        assert call["doc"]["doc_id"] == T_DOC
        assert (call["provider_id"], call["provider_source"]) == ("aip_2", "sequence_reviewer")
        if record.get("hop_item_seq") is not None:
            assert responder["resolved"][0]["item_seq"] == record["hop_item_seq"]

        # The answer lands: exactly one resume through resume_chain's CAS.
        resumed: list = []
        monkeypatch.setattr(svc, "start_run", lambda **kw: resumed.append(kw) or {
            "ok": True, "run_id": "aiv_resumed", "chain_id": kw.get("chain_id"),
            "status": "running"})
        questions.answer(next(iter(questions.items)))
        first = q_service.auto_resume_answered_chain(
            doc_id=T_DOC, api_base_url=API, responder_run={"run_id": "aiv_responder_1"})
        second = q_service.auto_resume_answered_chain(doc_id=T_DOC, api_base_url=API)
        assert first is not None and first["requester_run_id"] == run_id
        assert second is None
        assert len(resumed) == 1
        assert resumed[0]["chain_id"] == record["chain_id"]
        assert resumed[0]["doc_ref"] == SPINE and resumed[0]["mode"] == "continuous"
        assert resumed[0]["continuation_target_seq"] == 2
        assert GROUP not in env["paused"].rows


# ═══════════════════════════ unit: the hand-off branch ═══════════════════════════

# The real helpers, captured before any test patches them out of the way.
_REAL_DISPATCH = finalize.dispatch_responder_for_open_questions
_REAL_RESUME = finalize._auto_resume_answered_question_chain


def _run(**over):
    run = {
        "run_id": HOP, "group_id": GROUP, "doc_ref": SPINE, "project_id": "flowgate",
        "provider_id": "aip_worker",
        "issued_to": "usr_admin", "api_base_url": API, "continuation_locale": "ko",
        "mode": "continuous", "hop_kind": None, "status": "finished",
        "end_reason": "exited", "outcome": "complete", "stop_code": "hop_handoff",
        "cancel_event": threading.Event(), "chain_id": "chain_0688",
        "chain_docs_target": 3, "chain_docs_reached": 1, "docs_target": 1, "docs_reached": 1,
        "hop_item_seq": 5, "continuation_base_provider_id": "aip_header",
        "continuation_reviewer_overrides": {"5": "aip_2"},
        "continuation_review_count_overrides": {"5": 2},
        "continuation_instruction_mode": "auto_approved",
        "continuation_auto_approve_item_seqs": [],
    }
    run.update(over)
    return run


def _pending():
    return {"doc_ref": SPINE, "target_seq": 6, "to_end": False, "review_mode": False,
            "instruction_mode": "auto_approved", "locale": "ko", "issued_to": "usr_admin",
            "api_base_url": API}


@pytest.fixture
def handoff(monkeypatch):
    paused = base.FakePausedStore()
    for name in ("upsert", "get_by_group", "delete_by_group", "release_owned",
                 "delete_system_stop"):
        monkeypatch.setattr(db_paused, name, getattr(paused, name))
    state = {"paused": paused, "released": [], "gate": [], "dispatched": [], "resumed": [],
             "dispatch_kw": [], "probe": lambda group_id, run_id: []}
    monkeypatch.setattr(svc, "_auto_resume", {})
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(db_group_ai_leases, "release",
                        lambda *a, **kw: state["released"].append(a))
    monkeypatch.setattr(db_group_ai_leases, "get", lambda group_id: None)
    monkeypatch.setattr(svc, "_persist_run_record", lambda run: True)
    monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)
    monkeypatch.setattr(svc, "finished_payload", lambda run: {})
    monkeypatch.setattr(review, "run_review_gate",
                        lambda group_id, bundle, run: state["gate"].append(bundle) or True)
    monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                        lambda group_id, run_id: state["probe"](group_id, run_id))
    monkeypatch.setattr(finalize, "dispatch_responder_for_open_questions",
                        lambda run, items, **kw: (state["dispatched"].append(items),
                                                  state["dispatch_kw"].append(kw)))
    monkeypatch.setattr(finalize, "_auto_resume_answered_question_chain",
                        lambda run: state["resumed"].append(run["run_id"]))
    return state


def _queue(run):
    svc._auto_resume[run["group_id"]] = _pending()


OWN_Q = [{"id": 501, "seq": 1, "asker_kind": "ai", "asker_ai_run_id": HOP,
          "anchor_doc_id": T_DOC}]


class TestHandoffPark:
    def test_a_hop_without_a_question_of_its_own_hands_off_unchanged(self, handoff):
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)
        assert len(handoff["gate"]) == 1
        assert handoff["dispatched"] == [] and handoff["resumed"] == []
        assert run["_handoff_succeeded"] is True
        assert GROUP not in handoff["paused"].rows      # the hop_handoff row was cleared

    def test_its_own_open_question_parks_the_chain_instead_of_the_gate(self, handoff):
        handoff["probe"] = lambda group_id, run_id: list(OWN_Q)
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)

        assert handoff["gate"] == []
        assert svc.peek_auto_resume(GROUP) is None
        assert handoff["released"] == [(GROUP, HOP)]
        row = handoff["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
            "system", "question_pending", HOP)
        # The whole hand-off bundle survives on the row resume_chain consumes.
        assert row["continuation_target_seq"] == 6 and row["chain_id"] == "chain_0688"
        assert row["continuation_reviewer_overrides"] == {"5": "aip_2"}
        assert row["continuation_review_count_overrides"] == {"5": 2}
        assert row["continuation_instruction_mode"] == "auto_approved"
        assert handoff["dispatched"] == [OWN_Q] and handoff["resumed"] == [HOP]
        assert run["stop_code"] == "question_pending" and run["resumable"] is True
        assert not run.get("_handoff_succeeded")

    def test_a_plain_single_run_is_never_probed(self, handoff):
        def _probe(group_id, run_id):
            raise AssertionError("a run outside the chain must not even be probed")

        handoff["probe"] = _probe
        run = _run(mode="single", hop_kind=None)
        _queue(run)
        svc._maybe_auto_resume_hop(run)
        assert len(handoff["gate"]) == 1 and handoff["dispatched"] == []

    @pytest.mark.parametrize("hop_kind", [REVIEW_HOP_KIND, REWORK_HOP_KIND])
    def test_a_gate_owned_hop_with_its_own_question_parks_before_the_gate(
            self, handoff, monkeypatch, hop_kind):
        # 0688 TR0006 review: a review/rework hop of the chain is held exactly like a work
        # hop -- its queued gate bundle must not reach the gate (approve / next hop) first.
        handoff["probe"] = lambda group_id, run_id: list(OWN_Q)
        monkeypatch.setattr(review, "_pending_review_slot", lambda doc_ref: {
            "item_seq": 5, "doc_id": TR_DOC, "doc_type": "TR", "revision_no": 1,
            "review_status": "revised", "rejection_history": []})
        # What admission.start_run stores for a single-mode hop: no continuous-only field.
        run = _run(mode="single", hop_kind=hop_kind, doc_ref=TR_DOC, hop_item_seq=None,
                   continuation_base_provider_id=None, continuation_reviewer_overrides=None,
                   continuation_review_count_overrides=None)
        svc._auto_resume[GROUP] = {**_pending(), "last_stage": hop_kind, "revision_before": 0,
                                   "rounds_before": 0, "base_provider_id": "aip_header",
                                   "chain_id": "chain_0688",
                                   "reviewer_overrides": {"5": "aip_3"},
                                   "review_count_overrides": {"5": 1}}
        svc._maybe_auto_resume_hop(run)

        assert handoff["gate"] == []
        assert svc.peek_auto_resume(GROUP) is None
        row = handoff["paused"].rows[GROUP]
        assert (row["stop_code"], row["stop_run_id"], row["doc_ref"]) == (
            "question_pending", HOP, SPINE)
        assert row["chain_id"] == "chain_0688" and row["continuation_target_seq"] == 6
        # The step that asked is the slot the hop worked on; the header pick and the run's
        # [검수] picks ride the bundle (the run holds None for both maps).
        assert handoff["dispatched"] == [OWN_Q]
        assert handoff["dispatch_kw"] == [{
            "item_seq": 5, "base_provider_id": "aip_header",
            "reviewer_overrides": {"5": "aip_3"}, "review_count_overrides": {"5": 1}}]
        assert row["continuation_reviewer_overrides"] == {"5": "aip_3"}
        assert row["continuation_review_count_overrides"] == {"5": 1}
        assert run["stop_code"] == "question_pending"

    def test_an_unreadable_probe_parks_instead_of_handing_off(self, handoff):
        # 0688 T#2 (TR0008 rev1 review): whether the hop left a question open cannot be told,
        # and the gate past the hand-off may approve -- parked on the gate's unreadable stop.
        def _probe(group_id, run_id):
            raise RuntimeError("questions table unavailable")

        handoff["probe"] = _probe
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)
        assert handoff["gate"] == [] and handoff["dispatched"] == []
        assert handoff["resumed"] == [] and svc.peek_auto_resume(GROUP) is None
        row = handoff["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
            "system", review.REVIEW_HISTORY_UNREADABLE_STOP_CODE, HOP)
        assert row["chain_id"] == "chain_0688" and row["continuation_target_seq"] == 6
        assert "questions table unavailable" in run["review_reject_detail"]
        assert not run.get("_handoff_succeeded")

    def test_a_user_pause_outranks_the_park_and_starts_no_responder(self, handoff):
        handoff["probe"] = lambda group_id, run_id: list(OWN_Q)
        handoff["paused"].rows[GROUP] = {
            "group_id": GROUP, "doc_ref": SPINE, "paused_by": "usr_admin",
            "paused_at": "2026-10-11T08:00:00+09:00", "stop_kind": "user",
            "stop_code": None, "stop_run_id": None,
        }
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)
        assert handoff["gate"] == []
        assert handoff["paused"].rows[GROUP]["stop_kind"] == "user"
        assert handoff["dispatched"] == [] and handoff["resumed"] == []
        assert handoff["released"] == [(GROUP, HOP)]

    def test_a_replayed_terminal_cleanup_neither_reparks_nor_redispatches(self, handoff,
                                                                         monkeypatch):
        handoff["probe"] = lambda group_id, run_id: list(OWN_Q)
        upserts: list = []
        real_upsert = handoff["paused"].upsert
        monkeypatch.setattr(db_paused, "upsert",
                            lambda **kw: upserts.append(kw["stop_code"]) or real_upsert(**kw))
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)
        before = list(upserts)

        # The worker's finally block, then a second tail call (a replayed hop end).
        terminal.abandon_handoff(run, stop_code=run["stop_code"])
        svc._maybe_auto_resume_hop(run)

        assert upserts == before and before[-1] == "question_pending"
        assert len(handoff["dispatched"]) == 1 and handoff["gate"] == []
        assert handoff["paused"].rows[GROUP]["stop_code"] == "question_pending"

    def test_an_answer_that_raced_the_park_resumes_once_and_dispatches_nothing(
            self, handoff, monkeypatch, responder):
        questions = FakeQuestions().install(monkeypatch)
        pk = questions.add(asker_run_id=HOP)
        stale_probe = [{**questions.items[pk], "anchor_doc_id": T_DOC}]
        questions.answer(pk)                       # a person answered after the probe read
        handoff["probe"] = lambda group_id, run_id: list(stale_probe)
        # Real responder dispatch and real answer-side resume from here on.
        monkeypatch.setattr(finalize, "dispatch_responder_for_open_questions", _REAL_DISPATCH)
        monkeypatch.setattr(finalize, "_auto_resume_answered_question_chain", _REAL_RESUME)
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        monkeypatch.setattr(q_service.db_documents, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP})
        monkeypatch.setattr(db_runs, "get", lambda run_id: None)
        resumes: list = []

        def _resume(**kw):
            resumes.append(kw)
            row = handoff["paused"].rows.pop(GROUP, None)
            if row is None:
                raise HTTPException(409, {"code": "resume_conflict"})
            return {"ok": True, "run_id": "aiv_resumed", "chain_id": row["chain_id"]}

        monkeypatch.setattr(svc, "resume_chain", _resume)
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)

        assert responder["calls"] == []
        assert len(resumes) == 1 and resumes[0]["user_id"] == "usr_admin"
        # The person's own answer path arriving second finds the row consumed.
        assert q_service.auto_resume_answered_chain(doc_id=T_DOC, api_base_url=API) is None
        assert len(resumes) == 1
        assert handoff["gate"] == []

    def test_a_refused_responder_dispatch_leaves_the_chain_parked(self, handoff, monkeypatch,
                                                                  responder):
        questions = FakeQuestions().install(monkeypatch)
        pk = questions.add(asker_run_id=HOP)
        handoff["probe"] = lambda group_id, run_id: [{**questions.items[pk],
                                                      "anchor_doc_id": T_DOC}]
        monkeypatch.setattr(finalize, "dispatch_responder_for_open_questions", _REAL_DISPATCH)
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP})
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": [base.P1, base.P2]})
        responder["raise"] = HTTPException(409, {"code": "run_in_progress"})
        run = _run()
        _queue(run)
        svc._maybe_auto_resume_hop(run)        # must not raise

        assert len(responder["calls"]) == 1
        assert handoff["paused"].rows[GROUP]["stop_code"] == "question_pending"
        assert handoff["gate"] == []



# ═══════════════════════════ finalize: lookup and routing ═══════════════════════════

class TestOpenQuestionLookup:
    def test_only_this_run_s_unanswered_ai_items_across_the_group_oldest_first(self,
                                                                               monkeypatch):
        questions = FakeQuestions().install(monkeypatch)
        ours_t = questions.add(asker_run_id=HOP)
        questions.add(asker_run_id=None, kind="human")
        questions.add(asker_run_id="aiv_older_hop")
        answered = questions.add(asker_run_id=HOP)
        questions.answer(answered)
        ours_tr = questions.add(asker_run_id=HOP, doc=TR_DOC)

        found = finalize.open_questions_raised_by_run(GROUP, HOP)

        assert [(r["id"], r["anchor_doc_id"]) for r in found] == [
            (ours_t, T_DOC), (ours_tr, TR_DOC)]
        assert finalize.open_questions_raised_by_run(GROUP, None) == []

    def test_dispatch_targets_the_container_the_question_lives_on_and_the_asking_step(
            self, monkeypatch, responder):
        questions = FakeQuestions().install(monkeypatch)
        touched = questions.add(asker_run_id=HOP, responder_state="failed",
                                responder_run_id="aiv_dead")
        fresh = questions.add(asker_run_id=HOP)
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP})
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": [base.P1, base.P2]})
        monkeypatch.setattr(finalize.admission, "continuation_hop_item_seq",
                            lambda *a, **kw: 6)        # the spine already moved on
        items = finalize.open_questions_raised_by_run(GROUP, HOP)

        finalize.dispatch_responder_for_open_questions(
            _run(continuation_reviewer_overrides=None, continuation_review_count_overrides=None),
            items)

        assert [c["item"]["id"] for c in responder["calls"]] == [fresh]   # not `touched`
        assert responder["calls"][0]["doc"]["doc_id"] == T_DOC
        assert responder["resolved"][0]["item_seq"] == 5      # the step that asked
        assert responder["calls"][0]["provider_id"] == "aip_2"

    def test_without_any_reviewer_the_header_pick_answers(self, monkeypatch, responder):
        questions = FakeQuestions().install(monkeypatch)
        questions.add(asker_run_id=HOP)
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP})
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": [base.P1, base.P2]})
        monkeypatch.setattr(review, "_stored_review_policy_for_item_seq",
                            lambda doc_ref, item_seq: (0, None))
        run = _run(continuation_reviewer_overrides=None, continuation_review_count_overrides=None,
                   continuation_base_provider_id="aip_1")

        finalize.dispatch_responder_for_open_questions(
            run, finalize.open_questions_raised_by_run(GROUP, HOP))

        assert (responder["calls"][0]["provider_id"],
                responder["calls"][0]["provider_source"]) == ("aip_1", "header")

    def test_the_stop_reason_names_the_hand_off_park(self):
        parked = finalize._stop_reason_text("question_pending", {"outcome": "complete"})
        q_only = finalize._stop_reason_text("question_pending", {"outcome": "none"})
        assert parked.startswith("This hop submitted its document")
        assert q_only.startswith("This hop registered a query and is waiting")

    def test_the_help_text_no_longer_promises_an_unheld_chain(self):
        for locale in ("ko", "en", "ja"):
            assert help_catalog.QUESTION_HELP_COPY[locale]["continue_note"]
        assert "held before the next step" in help_catalog.QUESTION_HELP_COPY["en"][
            "continue_note"]


# ═══════════════════════════ finalize: the next question of a hand-off park ═══════════════════════════

def _responder_run(**over):
    run = {
        "run_id": "aiv_responder_1", "group_id": GROUP, "doc_ref": T_DOC,
        "project_id": "flowgate", "issued_to": "usr_admin", "api_base_url": API,
        "mode": "single", "action_scope": "edit", "completion_oracle": lambda: True,
        "scope_oracle_run": False, "end_reason": "exited", "stop_code": None,
        "outcome": "complete", "last_message": None,
    }
    run.update(over)
    return run


class TestNextQuestion:
    @pytest.fixture
    def parked(self, monkeypatch, responder):
        questions = FakeQuestions().install(monkeypatch)
        q1 = questions.add(asker_run_id=HOP, responder_state="dispatched",
                           responder_run_id="aiv_responder_1")
        questions.answer(q1)
        q2 = questions.add(asker_run_id=HOP)
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP})
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": [base.P1, base.P2]})
        monkeypatch.setattr(finalize.admission, "continuation_hop_item_seq",
                            lambda *a, **kw: 6)
        monkeypatch.setattr(db_paused, "get_by_group", lambda group_id: {
            "group_id": GROUP, "doc_ref": SPINE, "paused_by": "usr_admin",
            "stop_kind": "system", "stop_code": "question_pending", "stop_run_id": HOP,
            "continuation_reviewer_overrides": None, "continuation_review_count_overrides": None,
            "continuation_base_provider_id": None, "continuation_instruction_mode": None,
            "continuation_auto_approve_item_seqs": None,
        })
        return {"q1": q1, "q2": q2, "questions": questions}

    def test_q2_of_a_hand_off_park_is_found_beside_q1_for_the_asking_step(
            self, parked, monkeypatch, responder):
        monkeypatch.setattr(db_runs, "get", lambda run_id: {
            "run_id": run_id, "provider_id": "aip_1", "outcome": "complete", "hop_item_seq": 5})

        finalize._continue_question_responder_chain(_responder_run())

        assert (parked["q1"], "answered", "aiv_responder_1") in parked["questions"].states
        assert [c["item"]["id"] for c in responder["calls"]] == [parked["q2"]]
        assert responder["calls"][0]["doc"]["doc_id"] == T_DOC
        assert responder["resolved"][0]["item_seq"] == 5

    def test_q2_of_a_rework_hop_park_is_routed_to_the_slot_it_reworked(
            self, parked, monkeypatch, responder):
        # A gate-owned hop is single-mode: no hop_item_seq, so its document names the step.
        monkeypatch.setattr(db_runs, "get", lambda run_id: {
            "run_id": run_id, "provider_id": "aip_1", "outcome": "complete",
            "hop_item_seq": None, "mode": "single", "doc_ref": TR_DOC})
        monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_ref: {"id": 1})
        monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sid: [
            {"item_seq": 4, "type": "T", "result_doc_id": T_DOC},
            {"item_seq": 5, "type": "TR", "result_doc_id": TR_DOC},
        ])

        finalize._continue_question_responder_chain(_responder_run())

        assert [c["item"]["id"] for c in responder["calls"]] == [parked["q2"]]
        assert responder["resolved"][0]["item_seq"] == 5

    def test_a_q_only_park_keeps_reading_the_spine_s_current_step(self, parked, monkeypatch,
                                                                    responder):
        monkeypatch.setattr(db_runs, "get", lambda run_id: {
            "run_id": run_id, "provider_id": "aip_1", "outcome": "none", "hop_item_seq": 5})

        finalize._continue_question_responder_chain(_responder_run())

        assert [c["item"]["id"] for c in responder["calls"]] == [parked["q2"]]
        assert responder["resolved"][0]["item_seq"] == 6


# ═══════════════════════════ chain: resuming a hand-off park ═══════════════════════════

def _park_row(**over):
    row = {"group_id": GROUP, "doc_ref": SPINE, "paused_by": "usr_admin",
           "paused_at": "2026-10-11T08:00:00+09:00", "stop_kind": "system",
           "stop_code": "question_pending", "stop_run_id": HOP,
           "continuation_target_seq": 5, "continuation_review_count_overrides": None,
           "continuation_instruction_mode": None, "continuation_auto_approve_item_seqs": None,
           "continuation_base_provider_id": None, "continuation_provider_pinned": False}
    row.update(over)
    return row


class TestResumeOfHandoffPark:
    @pytest.mark.parametrize(("stop_code", "outcome", "stale"), [
        ("question_pending", "complete", False),   # the hand-off park: head past it by design
        ("question_pending", "none", True),        # a Q-only park keeps the old head test
        ("no_output_exhausted", "complete", True),
    ])
    def test_the_head_moving_past_a_hand_off_park_is_not_staleness(
            self, monkeypatch, stop_code, outcome, stale):
        monkeypatch.setattr(chain_module, "_group_workflow_finished", lambda group_id: False)
        monkeypatch.setattr(chain_module, "_sequence_completion_state",
                            lambda doc_ref, **kw: (True, 6))
        monkeypatch.setattr(db_runs, "get",
                            lambda run_id: {"hop_item_seq": 5, "outcome": outcome})
        row = _park_row(stop_code=stop_code, continuation_target_seq=None)
        assert chain_module._system_pause_row_is_stale(row) is stale

    @pytest.fixture
    def last_step(self, monkeypatch):
        monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_ref: {"id": 1})
        monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sid: [
            {"item_seq": 4, "type": "T", "result_doc_id": T_DOC},
            {"item_seq": 5, "type": "TR", "result_doc_id": TR_DOC},
        ])
        monkeypatch.setattr(review.db_docs, "get_by_id", lambda doc_id: {
            "doc_id": doc_id, "type_code": doc_id.rsplit("-", 1)[-1],
            "doc_review_status": "pending_review" if doc_id == TR_DOC else "approved",
            "revision_no": 0})
        monkeypatch.setattr(chain_module.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": [base.P1]})
        counts = {"value": 2}
        monkeypatch.setattr(review, "resolve_review_count", lambda *a, **kw: counts["value"])
        return counts

    def test_a_park_on_the_last_reviewed_step_is_resumable_into_its_review(self, last_step):
        state = chain_module._paused_row_resume_state("flowgate", _park_row())
        assert state["resume_available"] is True

    def test_a_park_on_an_unreviewed_last_step_is_resumable_into_its_settlement(self,
                                                                                last_step):
        # 0688 T#2: the inbox keeps an unreviewed step's document unapproved while its hop's
        # own question is open, so the answer's resume has that slot to settle (approve it,
        # or hold it for a human when the answer cannot be checked automatically).
        last_step["value"] = 0
        state = chain_module._paused_row_resume_state("flowgate", _park_row())
        assert state["resume_available"] is True

    @pytest.mark.parametrize(("row", "count"), [
        (_park_row(stop_code="no_output_exhausted"), 2),   # other stops: unchanged refusal
        (_park_row(stop_code="no_output_exhausted"), 0),
        (_park_row(continuation_target_seq=4), 2),         # the slot is past the target
    ])
    def test_everything_else_keeps_the_no_pending_refusal(self, last_step, row, count):
        last_step["value"] = count
        state = chain_module._paused_row_resume_state("flowgate", row)
        assert state["resume_available"] is False
        assert state["resume_block_code"] == "no_pending_worker_steps"



# ═══════════════════════════ a rework hop's own question: park → answer → the gate it held ═══════════════════════════
#
# 0688 TR0006 review: a continuous chain's rework hop runs mode="single", hop_kind="rework".
# It used to hand its queued gate bundle straight to run_review_gate with its question open,
# so the LAST round of a finite budget approved the reworked document and moved on (or ended
# the chain) before anybody answered. Now it parks like a work hop, and the answer's resume
# (q_service.auto_resume_answered_chain → resume_chain CAS) replays the gate it held.

REWORK = "aiv_0688_rework"
_REAL_LOOKUP = finalize.open_questions_raised_by_run


@pytest.fixture
def gate_chain(handoff, monkeypatch, responder):
    """A chain standing on TR (item_seq 5) after its rework landed (revision 1 > review 0)."""
    from modules.flow_gate.api import inbox_routes

    questions = FakeQuestions().install(monkeypatch)
    state = {
        "handoff": handoff, "questions": questions, "count": 1,
        "settle": [], "settle_outcome": {"outcome": "continue"}, "started": [],
        "review_hops": [], "rework_hops": [], "order": [],
        # test_run_service.hand_chain_to_server_run: None = the next head is no TSR waiting
        # on a server run, so the ordinary next hop starts.
        "server_test": None, "handed": [],
        "doc": {"doc_id": TR_DOC, "group_id": GROUP, "type_code": "TR",
                "doc_review_status": "revised", "revision_no": 1, "rejection_history": None},
        "reviews": [{"id": 71, "verdict": "issues", "revision_no": 0, "comment": "fix",
                     "findings": [{"locus": "a.py:1", "note": "wrong"}]}],
        "record": {"run_id": REWORK, "group_id": GROUP, "mode": "single",
                   "outcome": "complete", "doc_ref": TR_DOC, "hop_item_seq": None,
                   "provider_id": "aip_1"},
    }
    handoff["probe"] = _REAL_LOOKUP
    monkeypatch.setattr(finalize, "dispatch_responder_for_open_questions", _REAL_DISPATCH)
    monkeypatch.setattr(finalize, "_auto_resume_answered_question_chain", _REAL_RESUME)
    monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)

    def _doc(doc_id):
        if doc_id == TR_DOC:
            return dict(state["doc"])
        return {"doc_id": doc_id, "group_id": GROUP, "type_code": doc_id.rsplit("-", 1)[-1],
                "doc_review_status": "approved", "revision_no": 0}

    monkeypatch.setattr(review.db_docs, "get_by_id", _doc)
    monkeypatch.setattr(q_service.db_documents, "get_by_id", _doc)
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_ref: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sid: [
        {"item_seq": 4, "type": "T", "result_doc_id": T_DOC},
        {"item_seq": 5, "type": "TR", "result_doc_id": TR_DOC},
        {"item_seq": 6, "type": "T", "result_doc_id": None},
    ])
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda doc_id: list(state["reviews"]))
    monkeypatch.setattr(review.db_reviews, "get_latest_by_doc",
                        lambda doc_id: (state["reviews"] or [None])[0])
    monkeypatch.setattr(review, "resolve_review_count", lambda *a, **kw: state["count"])
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                        lambda pid: {"providers": [base.P1, base.P2]})
    monkeypatch.setattr(db_runs, "get",
                        lambda run_id: dict(state["record"]) if run_id == REWORK else None)
    # resume_chain's own collaborators that are not under test here.
    monkeypatch.setattr(svc, "_active_run_for_group", lambda group_id: None)
    monkeypatch.setattr(svc, "_paused_row_resume_state",
                        lambda project_id, row, include_target=False, **kw: {
                            "resume_available": True, "resume_block_code": None,
                            "resume_block_reason": None, "resume_provider_name": None,
                            "_resume_target_seq": 6})
    monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
    monkeypatch.setattr(review, "_materialize_work_plan_instruction_before_gate",
                        lambda bundle: [])
    monkeypatch.setattr(review, "_resumable_reviewer_overrides", lambda pid, overrides: overrides)

    def _settle(**kw):
        state["settle"].append(kw)
        state["order"].append("approve")
        outcome = dict(state["settle_outcome"])
        if outcome.get("outcome") in ("continue", "completed"):
            state["doc"]["doc_review_status"] = "approved"
        if outcome.pop("_user_pauses", False):
            handoff["paused"].rows[GROUP] = {
                "group_id": GROUP, "doc_ref": SPINE, "paused_by": "usr_admin",
                "paused_at": "2026-10-11T09:00:00+09:00", "stop_kind": "user",
                "stop_code": None, "stop_run_id": None}
        return outcome

    monkeypatch.setattr(inbox_routes, "settle_completed_step", _settle)

    def _hand_chain(doc_ref, **kw):
        state["handed"].append({"doc_ref": doc_ref, **kw})
        state["order"].append("bind_server_test")
        return dict(state["server_test"]) if state["server_test"] else None

    monkeypatch.setattr(test_run_service, "hand_chain_to_server_run", _hand_chain)

    def _start(**kw):
        state["started"].append(kw)
        state["order"].append("start_hop")
        return {"ok": True, "run_id": "aiv_next", "chain_id": kw.get("chain_id"),
                "status": "running"}

    monkeypatch.setattr(svc, "start_run", _start)
    monkeypatch.setattr(svc, "_spawn_review_hop", lambda group_id, bundle, gate: (
        state["review_hops"].append((bundle, gate))
        or {"ok": True, "run_id": "aiv_review", "chain_id": bundle.get("chain_id")}))
    monkeypatch.setattr(svc, "_spawn_rework_hop", lambda group_id, bundle, gate: (
        state["rework_hops"].append((bundle, gate))
        or {"ok": True, "run_id": "aiv_rework", "chain_id": bundle.get("chain_id")}))
    return state


def _park_rework_hop(state, **bundle_over) -> int:
    """The rework hop registers its Q, lands revision 1 and finishes; its gate bundle is queued.

    The run is what admission.start_run stores for a single-mode hop (no [검수] maps, no
    header pin); the chain's picks ride the queued gate bundle only.
    """
    count = state["count"]
    pk = state["questions"].add(asker_run_id=REWORK, doc=TR_DOC)
    run = _run(run_id=REWORK, mode="single", hop_kind=REWORK_HOP_KIND, doc_ref=TR_DOC,
               hop_item_seq=None, continuation_base_provider_id=None,
               continuation_reviewer_overrides=None,
               continuation_review_count_overrides=None,
               continuation_instruction_mode=None)
    svc._auto_resume[GROUP] = {
        **_pending(), "last_stage": REWORK_HOP_KIND, "revision_before": 0,
        "base_provider_id": "aip_header", "chain_id": "chain_0688", "chain_docs_target": 3,
        "chain_docs_reached": 1, "review_count_overrides": {"5": count},
        "reviewer_overrides": None, **bundle_over,
    }
    svc._maybe_auto_resume_hop(run)
    return pk


def _assert_held_unanswered(state, responder, source="sequence_reviewer"):
    """Nothing past the hop happened: no gate, no approval, no review hop, no next hop."""
    assert state["handoff"]["gate"] == []
    assert state["settle"] == [] and state["started"] == [] and state["handed"] == []
    assert state["review_hops"] == [] and state["rework_hops"] == []
    assert state["doc"]["doc_review_status"] == "revised"
    row = state["handoff"]["paused"].rows[GROUP]
    assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
        "system", "question_pending", REWORK)
    assert row["chain_id"] == "chain_0688" and row["doc_ref"] == SPINE
    assert row["continuation_review_count_overrides"] == {"5": state["count"]}
    # The step's reviewer answers, for the slot the rework worked on (item_seq 5).
    assert len(responder["calls"]) == 1
    assert responder["calls"][0]["doc"]["doc_id"] == TR_DOC
    assert (responder["calls"][0]["provider_id"],
            responder["calls"][0]["provider_source"]) == ("aip_2", source)
    assert responder["resolved"][0]["item_seq"] == 5
    # An answer-side resume attempt while the Q is still open changes nothing.
    assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
    assert state["settle"] == [] and state["started"] == []
    assert state["handoff"]["paused"].rows[GROUP]["stop_code"] == "question_pending"


class TestReworkHopQuestionPark:
    def test_last_finite_round_is_not_approved_before_the_answer_then_continues_once(
            self, gate_chain, responder):
        gate_chain["count"] = 1                      # review → rework → advance
        pk = _park_rework_hop(gate_chain)
        _assert_held_unanswered(gate_chain, responder)

        gate_chain["questions"].answer(pk)
        first = q_service.auto_resume_answered_chain(
            doc_id=TR_DOC, api_base_url=API, responder_run={"run_id": "aiv_responder_1"})
        second = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)

        assert first is not None and first["requester_run_id"] == REWORK
        assert second is None
        # The gate the hand-off held: approve the reworked revision, then the next hop.
        assert len(gate_chain["settle"]) == 1
        settle = gate_chain["settle"][0]
        assert (settle["doc_id"], settle["completed_seq"], settle["target_seq"]) == (TR_DOC, 5, 6)
        assert len(gate_chain["started"]) == 1
        started = gate_chain["started"][0]
        assert (started["mode"], started["doc_ref"], started["chain_id"]) == (
            "continuous", SPINE, "chain_0688")
        assert gate_chain["review_hops"] == [] and gate_chain["rework_hops"] == []
        assert GROUP not in gate_chain["handoff"]["paused"].rows

    def test_last_finite_round_on_the_target_completes_the_chain_only_after_the_answer(
            self, gate_chain, responder):
        gate_chain["count"] = 1
        gate_chain["settle_outcome"] = {"outcome": "completed"}
        pk = _park_rework_hop(gate_chain)
        _assert_held_unanswered(gate_chain, responder)

        gate_chain["questions"].answer(pk)
        first = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)

        assert first is not None and first["resumed_run_id"] is None
        assert len(gate_chain["settle"]) == 1 and gate_chain["started"] == []
        assert GROUP not in gate_chain["handoff"]["paused"].rows
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        assert len(gate_chain["settle"]) == 1

    def test_a_following_round_starts_its_review_only_after_the_answer(self, gate_chain,
                                                                       responder):
        gate_chain["count"] = 2                      # one more review+rework pair remains
        pk = _park_rework_hop(gate_chain)
        _assert_held_unanswered(gate_chain, responder)

        gate_chain["questions"].answer(pk)
        first = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)
        second = q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)

        assert first is not None and first["resumed_run_id"] == "aiv_review"
        assert second is None
        assert len(gate_chain["review_hops"]) == 1
        bundle, gate = gate_chain["review_hops"][0]
        assert (gate["stage"], gate["round_no"]) == (REVIEW_HOP_KIND, 2)
        assert bundle["chain_id"] == "chain_0688" and bundle["last_stage"] == REVIEW_HOP_KIND
        assert gate_chain["settle"] == [] and gate_chain["started"] == []

    def test_the_rework_s_gate_progress_survives_the_park(self, gate_chain, responder):
        # Two identical `issues` digests after a rework = review_stalled in the live gate.
        # A cold resume (no last_stage) would instead approve the budget-spent step.
        gate_chain["count"] = 2
        gate_chain["doc"]["revision_no"] = 2
        same = {"verdict": "issues", "comment": "fix",
                "findings": [{"locus": "a.py:1", "note": "wrong"}]}
        gate_chain["reviews"] = [{**same, "id": 72, "revision_no": 1},
                                 {**same, "id": 71, "revision_no": 0}]
        pk = _park_rework_hop(gate_chain)
        _assert_held_unanswered(gate_chain, responder)

        gate_chain["questions"].answer(pk)
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None

        assert gate_chain["settle"] == [] and gate_chain["started"] == []
        assert gate_chain["review_hops"] == [] and gate_chain["rework_hops"] == []
        row = gate_chain["handoff"]["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
            "system", "review_stalled", REWORK)

    def test_a_failed_approval_re_parks_on_its_own_stop(self, gate_chain, responder):
        gate_chain["count"] = 1
        gate_chain["settle_outcome"] = {"outcome": "stopped", "stop_code": "approve_failed",
                                        "detail": "transition refused"}
        pk = _park_rework_hop(gate_chain)
        gate_chain["questions"].answer(pk)

        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        assert len(gate_chain["settle"]) == 1 and gate_chain["started"] == []
        row = gate_chain["handoff"]["paused"].rows[GROUP]
        assert (row["stop_code"], row["stop_run_id"], row["chain_id"]) == (
            "approve_failed", REWORK, "chain_0688")

    def test_a_user_pause_taken_during_the_approval_keeps_its_own_row(self, gate_chain,
                                                                      responder):
        gate_chain["count"] = 1
        gate_chain["settle_outcome"] = {"outcome": "paused", "stop_code": "user_paused",
                                        "_user_pauses": True}
        pk = _park_rework_hop(gate_chain)
        gate_chain["questions"].answer(pk)

        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        assert gate_chain["started"] == []
        assert gate_chain["handoff"]["paused"].rows[GROUP]["stop_kind"] == "user"

    def test_repeated_answer_side_resumes_settle_and_start_exactly_once(self, gate_chain,
                                                                        responder):
        gate_chain["count"] = 1
        pk = _park_rework_hop(gate_chain)
        gate_chain["questions"].answer(pk)
        outcomes = [q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)
                    for _ in range(3)]
        assert sum(1 for o in outcomes if o is not None) == 1
        assert len(gate_chain["settle"]) == 1 and len(gate_chain["started"]) == 1


class TestQuestionParkGateProgress:
    def test_a_continuous_work_hop_keeps_the_cold_derivation(self):
        assert chain_module._question_park_gate_progress(
            {"doc_ref": SPINE}, {"mode": "continuous", "outcome": "complete"}) == {}

    def test_a_review_hop_is_held_to_the_row_it_added(self, monkeypatch):
        monkeypatch.setattr(review, "_pending_review_slot", lambda doc_ref: {
            "item_seq": 5, "doc_id": TR_DOC, "revision_no": 1})
        monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda doc_id: [
            {"verdict": "issues", "revision_no": 1}, {"verdict": "issues", "revision_no": 0}])
        assert chain_module._question_park_gate_progress(
            {"doc_ref": SPINE}, {"mode": "single", "doc_ref": TR_DOC}) == {
            "last_stage": REVIEW_HOP_KIND, "rounds_before": 1}

    def test_a_slot_that_is_not_the_hop_s_document_is_left_cold(self, monkeypatch):
        monkeypatch.setattr(review, "_pending_review_slot", lambda doc_ref: {
            "item_seq": 6, "doc_id": "other", "revision_no": 1})
        assert chain_module._question_park_gate_progress(
            {"doc_ref": SPINE}, {"mode": "single", "doc_ref": TR_DOC}) == {}



# ═══════════════════════════ the answered resume hands the chain to a waiting server test ═══════════════════════════
#
# 0688 TR0006 rev1 review: a specification TS's rework hop parks on its own Q; the answer's
# resume approves the TS (approve_first) and the next head is the TSR whose server run IS the
# next step. run_review_gate binds the chain to that run and waits on test_run_pending
# (park_for_server_test_run); the resume used to fall through to start_run instead, starting
# an AI hop and issuing a test_run token while the server test was still running.

TSR_DOC = f"{GROUP}.0006-TSR"


class TestQuestionParkServerTest:
    @pytest.fixture
    def ts_chain(self, gate_chain, monkeypatch):
        """TR_DOC is the specification TS at item_seq 5, the TSR waits at 6, target 7."""
        gate_chain["count"] = 1
        gate_chain["doc"]["type_code"] = "TS"
        monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sid: [
            {"item_seq": 4, "type": "T", "result_doc_id": T_DOC},
            {"item_seq": 5, "type": "TS", "result_doc_id": TR_DOC},
            {"item_seq": 6, "type": "TSR", "result_doc_id": None},
            {"item_seq": 7, "type": "T", "result_doc_id": None},
        ])
        monkeypatch.setattr(svc, "_paused_row_resume_state",
                            lambda project_id, row, include_target=False, **kw: {
                                "resume_available": True, "resume_block_code": None,
                                "resume_block_reason": None, "resume_provider_name": None,
                                "_resume_target_seq": 7})
        gate_chain["server_test"] = {"ts_doc_id": TR_DOC, "run_id": "tr_0688", "finished": False,
                                     "tsr_doc_id": TSR_DOC, "token_id": "tok_test_run"}
        test_runs = {"tr_0688": {"run_id": "tr_0688", "status": "running"}}
        gate_chain["test_runs"] = test_runs
        monkeypatch.setattr(db_test_runs, "get_run",
                            lambda run_id: dict(test_runs[run_id]) if run_id in test_runs else None)
        gate_chain["latest"] = {"run_id": "tr_0688", "status": "running"}
        monkeypatch.setattr(db_test_runs, "latest_by_doc",
                            lambda doc_id: dict(gate_chain["latest"]))
        # The chain context the TS run carries is the one bound by hand_chain_to_server_run.
        monkeypatch.setattr(test_run_service, "_continuation_token_for_doc",
                            lambda doc, run=None: {
                                "issued_to": gate_chain["handed"][-1]["issued_to"],
                                "continuation_target_seq":
                                    gate_chain["handed"][-1]["continuation_target_seq"],
                                "continuation_locale": "ko"} if gate_chain["handed"] else None)
        monkeypatch.setattr(test_run_service, "_tsr_slot_item",
                            lambda doc, wfseq: {"item_seq": 6, "result_doc_id": TSR_DOC})
        monkeypatch.setattr(test_run_service, "tsr_gate_state",
                            lambda tsr_doc, **kw: {"applies": True, "passed": True})
        monkeypatch.setattr(test_run_service, "_awaits_result_entry", lambda doc, latest: False)
        monkeypatch.setattr(chain_module, "_next_incomplete_item_seq", lambda doc_ref: 6)
        paused = gate_chain["handoff"]["paused"]

        def _mark(group_id, stop_code, *, stop_run_id=None):
            row = paused.rows.get(group_id)
            if row is not None and row.get("stop_run_id") == stop_run_id:
                row["stop_code"] = stop_code

        monkeypatch.setattr(db_paused, "mark_stop_code", _mark, raising=False)
        return gate_chain

    @staticmethod
    def _park_and_answer(chain, responder):
        pk = _park_rework_hop(
            chain, target_seq=7, reviewer_overrides={"5": "aip_2"}, provider_pinned=True,
            step_timeout_sec=900, restart_max_attempts=3, default_note="carry this note")
        _assert_held_unanswered(chain, responder, source="reviewer_override")
        chain["questions"].answer(pk)
        return q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API)

    @staticmethod
    def _ts_doc():
        return {"doc_id": TR_DOC, "group_id": GROUP, "type_code": "TS", "project_id": "flowgate"}

    def test_a_running_server_test_holds_the_chain_without_a_hop_or_token(self, ts_chain,
                                                                           responder):
        trace = self._park_and_answer(ts_chain, responder)

        assert trace is not None and trace["requester_run_id"] == REWORK
        assert trace["resumed_run_id"] is None
        # Approve the TS, then bind the chain to its server run -- and nothing else.
        assert ts_chain["order"] == ["approve", "bind_server_test"]
        assert ts_chain["started"] == []
        assert ts_chain["review_hops"] == [] and ts_chain["rework_hops"] == []
        handed = ts_chain["handed"][0]
        assert (handed["doc_ref"], handed["issued_to"], handed["continuation_target_seq"],
                handed["ai_run_id"], handed["api_base_url"]) == (
            SPINE, "usr_admin", 7, REWORK, API)
        # The row the CAS consumed is back whole, now waiting on the test gate.
        row = ts_chain["handoff"]["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == (
            "system", "test_run_pending", REWORK)
        assert (row["doc_ref"], row["paused_by"], row["continuation_target_seq"]) == (
            SPINE, "usr_admin", 7)
        assert (row["chain_id"], row["chain_docs_target"], row["chain_docs_reached"]) == (
            "chain_0688", 3, 1)
        assert (row["continuation_base_provider_id"], row["continuation_provider_pinned"]) == (
            "aip_header", True)
        assert (row["continuation_step_timeout_sec"], row["continuation_restart_max_attempts"],
                row["continuation_default_note"]) == (900, 3, "carry this note")
        assert row["continuation_reviewer_overrides"] == {"5": "aip_2"}
        assert row["continuation_review_count_overrides"] == {"5": 1}
        # A second answer-side resume finds no question park; the running test keeps waiting.
        assert q_service.auto_resume_answered_chain(doc_id=TR_DOC, api_base_url=API) is None
        waiting = test_run_service.continue_chain_after_test_gate(
            self._ts_doc(), None, api_base_url=API, locale="ko")
        assert waiting["action"] == "waiting_gate"
        assert ts_chain["started"] == []
        assert ts_chain["handoff"]["paused"].rows[GROUP]["stop_code"] == "test_run_pending"

    def test_pass_resumes_the_chain_exactly_once_with_its_picks(self, ts_chain, responder):
        self._park_and_answer(ts_chain, responder)
        ts_chain["latest"] = {"run_id": "tr_0688", "status": "passed", "overall": "PASS"}

        first = test_run_service.continue_chain_after_test_gate(
            self._ts_doc(), None, api_base_url=API, locale="ko")
        second = test_run_service.continue_chain_after_test_gate(
            self._ts_doc(), None, api_base_url=API, locale="ko")

        assert first["action"] == "resumed" and first["run_id"] == "aiv_next"
        assert second["action"] == "deferred"            # the row is consumed: no second hop
        assert ts_chain["order"] == ["approve", "bind_server_test", "start_hop"]
        started = ts_chain["started"][0]
        assert (started["mode"], started["doc_ref"], started["chain_id"]) == (
            "continuous", SPINE, "chain_0688")
        assert (started["continuation_target_seq"], started["provider_id"],
                started["provider_pinned"]) == (7, "aip_header", True)
        assert (started["continuation_step_timeout_sec"],
                started["continuation_restart_max_attempts"],
                started["continuation_default_note"]) == (900, 3, "carry this note")
        assert started["continuation_reviewer_overrides"] == {"5": "aip_2"}
        assert (started["chain_docs_target"], started["chain_docs_reached"]) == (3, 1)
        assert GROUP not in ts_chain["handoff"]["paused"].rows

    def test_fail_goes_to_failure_origin_review_once_and_starts_no_hop(self, ts_chain,
                                                                       responder, monkeypatch):
        from modules.flow_gate.services import failure_origin_review_service

        self._park_and_answer(ts_chain, responder)
        dispatched: list = []
        monkeypatch.setattr(db_test_runs, "set_failure_origin_pending", lambda run_id: None)
        monkeypatch.setattr(failure_origin_review_service, "dispatch_failure_origin_review",
                            lambda **kw: dispatched.append(kw) or {"ok": True})
        finished = {"run_id": "tr_0688", "status": "failed", "overall": "FAIL"}

        routed = test_run_service._handle_spec_failure(self._ts_doc(), finished)

        assert routed == {"routed": "failure_origin_review", "run_id": "tr_0688"}
        assert len(dispatched) == 1 and dispatched[0]["issued_to"] == "usr_admin"
        assert ts_chain["started"] == []
        assert ts_chain["handoff"]["paused"].rows[GROUP]["stop_code"] == "test_run_pending"

    def test_blocked_relabels_the_wait_and_starts_no_hop(self, ts_chain, responder):
        self._park_and_answer(ts_chain, responder)
        ts_chain["latest"] = {"run_id": "tr_0688", "status": "passed", "overall": "BLOCKED",
                              "contract_version": test_run_service.test_spec_service.CONTRACT_SPEC}

        for _ in range(2):
            blocked = test_run_service.continue_chain_after_test_gate(
                self._ts_doc(), None, api_base_url=API, locale="ko")
            assert blocked["action"] == "gate_blocked"
        assert ts_chain["started"] == []
        row = ts_chain["handoff"]["paused"].rows[GROUP]
        assert (row["stop_code"], row["stop_run_id"]) == ("test_gate_blocked", REWORK)

    def test_manual_or_external_entry_resumes_the_chain_once(self, ts_chain, responder,
                                                              monkeypatch):
        self._park_and_answer(ts_chain, responder)
        ts_chain["latest"] = {"run_id": "tr_0688", "status": "passed", "overall": "NOT_RUN",
                              "contract_version": test_run_service.test_spec_service.CONTRACT_SPEC}
        monkeypatch.setattr(test_run_service, "_awaits_result_entry", lambda doc, latest: True)

        first = test_run_service.continue_chain_after_test_gate(
            self._ts_doc(), None, api_base_url=API, locale="ko")
        second = test_run_service.continue_chain_after_test_gate(
            self._ts_doc(), None, api_base_url=API, locale="ko")

        assert first["action"] == "resumed_for_result_entry" and first["run_id"] == "aiv_next"
        assert second["action"] == "gate_blocked"         # no row left to resume twice
        assert len(ts_chain["started"]) == 1
        assert ts_chain["started"][0]["chain_id"] == "chain_0688"

    def test_a_run_that_already_ended_resumes_after_the_lock_is_released(self, ts_chain,
                                                                         responder,
                                                                         monkeypatch):
        from modules.flow_gate.services.ai_invoke.runtime import group_resume_lock

        ts_chain["server_test"]["finished"] = True
        ts_chain["test_runs"]["tr_0688"]["status"] = "passed"
        ts_chain["latest"] = {"run_id": "tr_0688", "status": "passed", "overall": "PASS"}
        real_resume = test_run_service.resume_test_gate_chain_for_hop
        lock_free: list = []

        def _resume_for_hop(doc_id, **kw):
            # The resume lock is not reentrant: holding it here would deadlock resume_chain.
            lock = group_resume_lock(GROUP)
            free = lock.acquire(blocking=False)
            if free:
                lock.release()
            lock_free.append(free)
            ts_chain["order"].append("resume_ended_test")
            return real_resume(doc_id, **kw) if free else {"action": "lock_held"}

        monkeypatch.setattr(test_run_service, "resume_test_gate_chain_for_hop", _resume_for_hop)

        trace = self._park_and_answer(ts_chain, responder)

        assert trace is not None and trace["requester_run_id"] == REWORK
        assert lock_free == [True]
        assert ts_chain["order"] == [
            "approve", "bind_server_test", "resume_ended_test", "start_hop"]
        assert len(ts_chain["started"]) == 1
        assert ts_chain["started"][0]["chain_id"] == "chain_0688"
        assert GROUP not in ts_chain["handoff"]["paused"].rows

    def test_no_waiting_server_test_starts_the_next_hop_as_before(self, ts_chain, responder):
        ts_chain["server_test"] = None              # the next head is no TSR waiting on a run
        self._park_and_answer(ts_chain, responder)
        assert ts_chain["order"] == ["approve", "bind_server_test", "start_hop"]
        assert len(ts_chain["started"]) == 1
        assert GROUP not in ts_chain["handoff"]["paused"].rows


# ═══════════════════════════ engine: the runtime [검수] picks of a real single-mode hop ═══════════════════════════
#
# 0688 TR0006 rev1 review: admission.start_run stores None for continuation_reviewer_overrides
# and continuation_review_count_overrides when mode="single" -- every review/rework hop. The
# first responder of such a hop's own question read both maps off that run, so the reviewer
# named for THIS run lost to the stored reviewer (or the header pick), and the step's review
# count was judged without its override. The maps ride the queued gate bundle instead.

class TestGateHopRuntimePicks:
    ITEM_SEQ = 5

    def _launch_rework(self, env, monkeypatch, questions, *, reviewer_overrides,
                       review_count_overrides) -> str:
        """The REAL gate spawns the rework hop through the REAL admission (0622's harness);
        its worker registers two Qs and lands a revision."""
        from modules.flow_gate.services import invoke_mention_service

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda seq_id: [
            {"item_seq": self.ITEM_SEQ, "type": "TR", "result_doc_id": SPINE},
            {"item_seq": self.ITEM_SEQ + 1, "type": "TR", "result_doc_id": None},
        ])
        get_doc = env["docs"].get_by_id
        monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {
            **get_doc(doc_id), "doc_review_status": "rejected", "type_code": "TR"})
        env["reviews"].add_verdict(SPINE, verdict="issues", revision_no=env["docs"].revision_no)
        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "raw", "token_id": "tok_rework",
            "scratch_dir": str(env["tmp"] / "rework"), "mention": "Fix the review findings"})
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: TR_DOC)

        def _worker(provider, prompt, run):
            questions.add(asker_run_id=run["run_id"], doc=TR_DOC)
            questions.add(asker_run_id=run["run_id"], doc=TR_DOC)
            env["docs"].revision_no += 1
            run["exit_code"] = 0
            run["last_message"] = "Revised; the assumptions are recorded as Qs"
            run["last_message_received"] = True
            return "started_ok", None

        monkeypatch.setattr(svc, "_cli_execute", _worker)
        bundle = {
            "doc_ref": SPINE, "issued_to": "usr_admin", "chain_id": "chain_0688_rt",
            "target_seq": self.ITEM_SEQ + 1, "base_provider_id": "aip_1",
            "provider_pinned": True, "api_base_url": API,
            "review_count_overrides": review_count_overrides,
            "reviewer_overrides": reviewer_overrides,
        }
        assert svc.resolve_review_gate(bundle)["stage"] == REWORK_HOP_KIND
        assert svc.run_review_gate(GROUP, bundle, {"run_id": "run_chain_0688"}) is True
        run_id = next(iter(svc._runs))
        base._wait_finished(run_id)
        base._wait_until(
            lambda: env["paused"].rows.get(GROUP, {}).get("stop_code") == "question_pending",
            message="the rework hop parking on its own question")
        return run_id

    @pytest.mark.parametrize(("reviewer_overrides", "review_count_overrides", "stored",
                              "expected"), [
        # The reviewer named for this run outranks the stored step reviewer.
        ({"5": "aip_3"}, {"5": 1}, (2, "aip_2"), ("aip_3", "reviewer_override")),
        # The run's review count makes the stored reviewer eligible (stored count 0 would
        # have dropped the question to the header pick, aip_1).
        (None, {"5": 1}, (0, "aip_2"), ("aip_2", "sequence_reviewer")),
    ])
    def test_q1_q2_and_a_restart_answer_with_the_runtime_picks(
            self, env, monkeypatch, responder, reviewer_overrides, review_count_overrides,
            stored, expected):
        questions = FakeQuestions().install(monkeypatch)
        monkeypatch.setattr(review, "_stored_review_policy_for_item_seq",
                            lambda doc_ref, item_seq: stored)
        gate_spawned: list = []
        real_gate = review.run_review_gate

        def _gate(group_id, bundle, run):
            if run.get("run_id") != "run_chain_0688":     # not the launch below: the hop's own
                gate_spawned.append(run.get("run_id"))
            return real_gate(group_id, bundle, run)

        monkeypatch.setattr(review, "run_review_gate", _gate)

        run_id = self._launch_rework(env, monkeypatch, questions,
                                     reviewer_overrides=reviewer_overrides,
                                     review_count_overrides=review_count_overrides)
        base._wait_until(lambda: len(responder["calls"]) == 1, message="Q1 responder")

        # The hop really went through the single-mode admission: neither map is on its run.
        record = svc.get_run_record(run_id)
        assert (record["mode"], record.get("hop_kind")) == ("single", REWORK_HOP_KIND)
        assert record.get("continuation_reviewer_overrides") is None
        assert record.get("continuation_review_count_overrides") is None
        assert gate_spawned == []                     # parked before the gate
        # Q1: the runtime picks decide, for the step the rework worked on.
        q1, q2 = sorted(questions.items)
        assert responder["calls"][0]["item"]["id"] == q1
        assert (responder["calls"][0]["provider_id"],
                responder["calls"][0]["provider_source"]) == expected
        assert responder["resolved"][0]["item_seq"] == self.ITEM_SEQ
        row = env["paused"].rows[GROUP]
        assert row["continuation_reviewer_overrides"] == reviewer_overrides
        assert row["continuation_review_count_overrides"] == review_count_overrides

        # Q1 answered, then a restart: nothing in memory, only the durable row and records.
        questions.items[q1].update(responder_state="dispatched",
                                   responder_run_id="aiv_responder_1")
        questions.answer(q1)
        persisted = {r["run_id"]: r for r in env["records"]}
        monkeypatch.setattr(svc, "_runs", {})
        monkeypatch.setattr(db_runs, "get",
                            lambda rid: dict(persisted[rid]) if rid in persisted else None)
        finalize._continue_question_responder_chain(_responder_run(doc_ref=TR_DOC))

        assert [c["item"]["id"] for c in responder["calls"]] == [q1, q2]
        assert (responder["calls"][1]["provider_id"],
                responder["calls"][1]["provider_source"]) == expected
        assert responder["resolved"][1]["item_seq"] == self.ITEM_SEQ
        assert env["paused"].rows[GROUP]["stop_code"] == "question_pending"
