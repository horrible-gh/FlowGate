"""flowgate.default.0661 T0004: Q auto-answer — reviewer baseline, sequential Q, failure
visibility, user-decision hand-off, provenance and the self-answer guard.

Every test drives the production functions (review.resolve_question_responder_with_source,
finalize._dispatch_question_responder / _continue_question_responder_chain,
q_answer_invoke_service.dispatch_answer_run, q_service.*, q_tapi_routes) and never a
reimplementation of them. Two harnesses:

  * in-memory `FakeItems` for the finalize dispatch/continue wiring (the same
    function-local-import monkeypatch style as test_q_responder_auto_dispatch_0551);
  * a real sqlite file with EVERY migration applied (incl. 142) for q_service / routes,
    because the CAS writes (`_execute_affected`) and the migration columns are the point.

T0004 "이관 오동작 방지 필수 테스트" and "필수 비발동 회귀 테스트" map onto the classes below:
  TestResolveQuestionResponderBaseline    F1 — reviewer tiers, review_count=0 vs unset
  TestDispatchBoundaries                  non-trigger: old / human / other-run Qs never dispatch
  TestSequentialQuestions                 F2 — Q1→Q2 one at a time, from the responder's own
                                               finalization, stop on failure/escalation/user pause
  TestResponderOutcomes                   F3/F5 — failed ≠ answered ≠ user_decision
  TestQServiceGuards                      F5/F6 — escalate ownership, self-answer block, provenance
  TestRoutes                              F5/F6 — session cannot forge 'ai', responder cannot ask,
                                               escalate route
  TestDispatchAnswerRunGuards             F2/F3 — live-responder refusal, admission failure recorded,
                                               claim refusal mints no token
  TestStartupSweep                        F3 — 'dispatched' rows of a dead process become visible
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import question_items as db_question_items  # noqa: E402
from modules.flow_gate.db import questions as db_questions  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402
from modules.flow_gate.services import q_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import finalize  # noqa: E402
from modules.flow_gate.services.ai_invoke import review  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402

_REAL_MARK_RESPONDER_FAILED = q_service.mark_responder_failed

_QUERIES: dict = {}
for _section, _entries in _json.loads(_QUERIES_JSON.read_text(encoding="utf-8")).items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


# ═══════════════════════════ shared fixtures ═══════════════════════════

def _provider(pid, name):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": "noop", "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


REVIEWER = _provider("aip_reviewer", "Reviewer Claude")
HEADER = _provider("aip_header", "Header Codex")
CHAIN = [REVIEWER, HEADER]

GROUP = "flowgate.default.0661"
SPINE = "flowgate.default.0661.0001-B"
ANCHOR = "flowgate.default.0661.0004-T"
API = "http://127.0.0.1:8089/flowgate/api/v1"
DOC = {"doc_id": ANCHOR, "project_id": "flowgate", "group_id": GROUP, "title": "작업지시",
       "file_path": None}
REQUESTER = "aiv_requester"
RESPONDER = "aiv_responder_1"


class FakeItems:
    """In-memory question_items: exactly the surface finalize/dispatch touch."""

    def __init__(self, rows):
        self.rows = {r["id"]: {"question_id": 1, "answer_count": 0, **r} for r in rows}
        self.claims: list[tuple] = []
        self.states: list[tuple] = []

    def list_unanswered(self, qpk):
        return sorted(
            (dict(r) for r in self.rows.values()
             if r["question_id"] == qpk and int(r.get("answer_count") or 0) == 0),
            key=lambda r: r["seq"],
        )

    def get_by_pk(self, pk):
        r = self.rows.get(pk)
        return dict(r) if r else None

    def claim_responder_dispatch(self, pk, run_id, requested_provider_id=None, provider_source=None):
        r = self.rows[pk]
        if r.get("answer_count") or r.get("responder_run_id") == run_id:
            return False
        r.update(responder_state="dispatched", responder_run_id=run_id,
                 responder_requested_provider_id=requested_provider_id,
                 responder_provider_source=provider_source,
                 responder_attempts=int(r.get("responder_attempts") or 0) + 1)
        self.claims.append((pk, run_id, requested_provider_id, provider_source))
        return True

    def set_responder_state(self, pk, state, error_code=None, error_message=None, *, run_id=None):
        r = self.rows.get(pk)
        if r is None or (run_id is not None and r.get("responder_run_id") != run_id):
            return False
        r.update(responder_state=state, responder_error_code=error_code,
                 responder_error_message=error_message)
        self.states.append((pk, state, error_code, run_id))
        return True

    def fail_responder(self, pk, error_code, error_message, *, run_id=None,
                       observed_state=None, observed_run_id=None):
        r = self.rows.get(pk)
        if r is None or int(r.get("answer_count") or 0) > 0:
            return False
        if run_id is None:
            # Same WHERE as queries.json question_items.fail_responder.
            if r.get("responder_state") not in (None, "failed"):
                return False
            if ((r.get("responder_state") or "") != (observed_state or "")
                    or (r.get("responder_run_id") or "") != (observed_run_id or "")):
                return False
        elif r.get("responder_run_id") != run_id or r.get("responder_state") != "dispatched":
            return False
        r.update(responder_state="failed", responder_error_code=error_code,
                 responder_error_message=error_message)
        self.states.append((pk, "failed", error_code, run_id))
        return True

    def list_by_responder_run(self, run_id):
        return [dict(r) for r in self.rows.values() if r.get("responder_run_id") == run_id]

    def list_dispatched(self):
        return [dict(r) for r in self.rows.values() if r.get("responder_state") == "dispatched"]

    def install(self, monkeypatch):
        for name in ("list_unanswered", "get_by_pk", "claim_responder_dispatch",
                     "set_responder_state", "fail_responder", "list_by_responder_run",
                     "list_dispatched"):
            monkeypatch.setattr(db_question_items, name, getattr(self, name))
        return self


def _item(pk, seq, *, asker=REQUESTER, kind="ai", **extra):
    return {"id": pk, "seq": seq, "asker_kind": kind, "asker_ai_run_id": asker, **extra}


@pytest.fixture
def wiring(monkeypatch):
    """The finalize dispatch collaborators, every one patched at its own source module."""
    calls: list[dict] = []
    failed: list[tuple] = []
    monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
    monkeypatch.setattr(db_questions, "get_container_by_doc",
                        lambda doc_id: {"id": 1, "status": "pending", "doc_id": ANCHOR})
    monkeypatch.setattr(finalize.db_docs, "get_by_id",
                        lambda doc_id: dict(DOC) if doc_id == ANCHOR else None)
    monkeypatch.setattr(finalize.admission, "continuation_hop_item_seq", lambda *a, **kw: 3)
    monkeypatch.setattr(
        q_answer_invoke_service, "resolve_item",
        lambda doc_id, item_id: {"id": item_id, "seq": item_id, "title": "Q", "body": "b",
                                 "options": []},
    )
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                        lambda pid: {"providers": CHAIN})
    monkeypatch.setattr(review, "_stored_review_policy_for_item_seq",
                        lambda doc_ref, item_seq: (0, None))

    def _dispatch(**kw):
        calls.append(kw)
        return {"run_id": "aiv_new_responder", "status": "running"}

    monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _dispatch)

    def _mark_failed(item_id, code, message, **kw):
        failed.append((item_id, code, message, kw.get("run_id")))
        return True

    monkeypatch.setattr(q_service, "mark_responder_failed", _mark_failed)
    monkeypatch.setattr(db_paused, "get_by_group", lambda gid: None)
    monkeypatch.setattr(db_runs, "get", lambda run_id: None)
    return {"calls": calls, "failed": failed}


def _requester_run(**overrides):
    row = {
        "run_id": REQUESTER, "group_id": GROUP, "doc_ref": SPINE, "project_id": "flowgate",
        "issued_to": "u-worker", "api_base_url": API, "mode": "continuous",
        "stop_code": "question_pending",
        "continuation_reviewer_overrides": None, "continuation_review_count_overrides": None,
        "continuation_base_provider_id": "aip_header",
        "continuation_instruction_mode": None, "continuation_auto_approve_item_seqs": None,
    }
    row.update(overrides)
    return row


def _responder_run(**overrides):
    row = {
        "run_id": RESPONDER, "group_id": GROUP, "doc_ref": ANCHOR, "project_id": "flowgate",
        "issued_to": "u-worker", "api_base_url": API, "mode": "single", "action_scope": "edit",
        "completion_oracle": lambda: True, "scope_oracle_run": False,
        "end_reason": "exited", "stop_code": None, "outcome": "complete", "last_message": None,
    }
    row.update(overrides)
    return row


def _paused_row(**overrides):
    row = {
        "group_id": GROUP, "doc_ref": SPINE, "paused_by": "u-requester",
        "stop_kind": "system", "stop_code": "question_pending", "stop_run_id": REQUESTER,
        "continuation_reviewer_overrides": '{"3": "aip_reviewer"}',
        "continuation_review_count_overrides": None,
        "continuation_base_provider_id": None, "continuation_instruction_mode": None,
        "continuation_auto_approve_item_seqs": None,
    }
    row.update(overrides)
    return row


# ═══════════════════════════ F1 — reviewer baseline ═══════════════════════════

class TestResolveQuestionResponderBaseline:
    @pytest.fixture(autouse=True)
    def _chain(self, monkeypatch):
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                            lambda pid: {"providers": CHAIN})

    def _stored(self, monkeypatch, count, reviewer):
        monkeypatch.setattr(review, "_stored_review_policy_for_item_seq",
                            lambda doc_ref, item_seq: (count, reviewer))

    def test_sequence_only_reviewer_answers_its_step(self, monkeypatch):
        # 0003-NR §2.2: the reviewer lives only on the stored workflow sequence (no runtime
        # override) — before 0661 the header pick silently won here.
        self._stored(monkeypatch, 1, "aip_reviewer")
        assert review.resolve_question_responder_with_source(
            None, 3, "aip_header", "flowgate", doc_ref=SPINE,
        ) == ("aip_reviewer", "sequence_reviewer")
        assert review.resolve_question_responder(None, 3, "aip_header", "flowgate",
                                                 doc_ref=SPINE) == "aip_reviewer"

    def test_review_count_zero_step_has_no_reviewer_and_falls_to_header(self, monkeypatch):
        # review_count=0 means "this step is not reviewed" (normalize_review_count), even
        # if a stale reviewer id is still on the row — distinct from "reviewer unset".
        self._stored(monkeypatch, 0, "aip_reviewer")
        assert review.resolve_question_responder_with_source(
            None, 3, "aip_header", "flowgate", doc_ref=SPINE,
        ) == ("aip_header", "header")

    def test_reviewer_unset_with_count_falls_to_header_not_project_default(self, monkeypatch):
        # review_count>0 / reviewer NULL = "general gate uses the project default"; a
        # question keeps the header pick ahead of that (0551 T#1 tier order).
        self._stored(monkeypatch, 1, None)
        assert review.resolve_question_responder_with_source(
            None, 3, "aip_header", "flowgate", doc_ref=SPINE,
        ) == ("aip_header", "header")

    def test_runtime_count_override_zero_switches_the_stored_reviewer_off(self, monkeypatch):
        self._stored(monkeypatch, 1, "aip_reviewer")
        assert review.resolve_question_responder_with_source(
            None, 3, "aip_header", "flowgate", doc_ref=SPINE, review_count_overrides={"3": 0},
        ) == ("aip_header", "header")

    def test_runtime_reviewer_override_wins_even_when_the_step_is_unreviewed(self, monkeypatch):
        self._stored(monkeypatch, 0, None)
        assert review.resolve_question_responder_with_source(
            {"3": "aip_reviewer"}, 3, "aip_header", "flowgate", doc_ref=SPINE,
        ) == ("aip_reviewer", "reviewer_override")

    def test_a_disabled_stored_reviewer_falls_to_the_header_pick(self, monkeypatch):
        self._stored(monkeypatch, 1, "aip_gone")
        assert review.resolve_question_responder_with_source(
            None, 3, "aip_header", "flowgate", doc_ref=SPINE,
        ) == ("aip_header", "header")

    def test_nothing_resolves_to_the_default_chain(self, monkeypatch):
        self._stored(monkeypatch, 0, None)
        assert review.resolve_question_responder_with_source(
            None, 3, None, "flowgate", doc_ref=SPINE,
        ) == (None, "default")

    def test_no_doc_ref_keeps_the_0551_behaviour(self):
        assert review.resolve_question_responder(
            {"3": "aip_reviewer"}, 3, "aip_header", "flowgate") == "aip_reviewer"
        assert review.resolve_question_responder(None, 3, "aip_header", "flowgate") == "aip_header"


# ═══════════════════════════ non-trigger boundaries ═══════════════════════════

class TestDispatchBoundaries:
    def test_only_items_raised_by_this_run_are_dispatched(self, wiring, monkeypatch):
        FakeItems([
            _item(101, 1, asker="aiv_old_run"),       # another run's question
            _item(102, 2, asker=None, kind="human"),  # a person's question
            _item(103, 3, asker=REQUESTER),           # ours
        ]).install(monkeypatch)
        finalize._dispatch_question_responder(_requester_run())
        assert [c["item"]["id"] for c in wiring["calls"]] == [103]

    def test_a_group_with_only_foreign_pending_questions_dispatches_nothing(self, wiring, monkeypatch):
        FakeItems([_item(101, 1, asker="aiv_old_run"), _item(102, 2, asker=None, kind="human")]
                  ).install(monkeypatch)
        finalize._dispatch_question_responder(_requester_run())
        assert wiring["calls"] == []
        assert wiring["failed"] == []

    def test_items_a_responder_already_touched_are_never_redispatched(self, wiring, monkeypatch):
        FakeItems([
            _item(101, 1, responder_state="failed", responder_run_id="aiv_dead"),
            _item(102, 2, responder_state="user_decision", responder_run_id="aiv_dead"),
            _item(103, 3),
        ]).install(monkeypatch)
        finalize._dispatch_question_responder(_requester_run())
        assert [c["item"]["id"] for c in wiring["calls"]] == [103]

    def test_the_resolver_receives_the_spine_and_both_review_maps(self, wiring, monkeypatch):
        FakeItems([_item(201, 1)]).install(monkeypatch)
        seen: dict = {}

        def _resolver(reviewer_overrides, item_seq, base_provider_id, project_id,
                      doc_ref=None, review_count_overrides=None):
            seen.update(reviewer_overrides=reviewer_overrides, item_seq=item_seq,
                        base_provider_id=base_provider_id, project_id=project_id,
                        doc_ref=doc_ref, review_count_overrides=review_count_overrides)
            return "aip_reviewer", "sequence_reviewer"

        monkeypatch.setattr(review, "resolve_question_responder_with_source", _resolver)
        finalize._dispatch_question_responder(_requester_run(
            continuation_reviewer_overrides={"5": "aip_x"},
            continuation_review_count_overrides={"3": 1},
        ))
        assert seen == {
            "reviewer_overrides": {"5": "aip_x"}, "item_seq": 3, "base_provider_id": "aip_header",
            "project_id": "flowgate", "doc_ref": SPINE, "review_count_overrides": {"3": 1},
        }
        assert wiring["calls"][0]["provider_id"] == "aip_reviewer"
        assert wiring["calls"][0]["provider_source"] == "sequence_reviewer"

    def test_a_responder_shaped_run_that_owns_no_item_is_ignored(self, wiring, monkeypatch):
        FakeItems([_item(201, 1)]).install(monkeypatch)
        finalize._continue_question_responder_chain(_responder_run(run_id="aiv_unrelated"))
        assert wiring["calls"] == []

    def test_a_continuous_run_never_enters_the_continuation_path(self, wiring, monkeypatch):
        fake = FakeItems([_item(201, 1, responder_state="dispatched", responder_run_id=REQUESTER)]
                         ).install(monkeypatch)
        finalize._continue_question_responder_chain(_requester_run(mode="continuous"))
        assert wiring["calls"] == [] and fake.states == []


# ═══════════════════════════ F2 — sequential questions ═══════════════════════════

class TestSequentialQuestions:
    def _answered_q1_open_q2(self):
        return FakeItems([
            _item(201, 1, answer_count=1, responder_state="dispatched", responder_run_id=RESPONDER),
            _item(202, 2),
            _item(203, 3, asker=None, kind="human"),
        ])

    def test_an_answered_responder_dispatches_exactly_the_next_item(self, wiring, monkeypatch):
        fake = self._answered_q1_open_q2().install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        seen: dict = {}

        def _resolver(reviewer_overrides, item_seq, base_provider_id, project_id,
                      doc_ref=None, review_count_overrides=None):
            seen.update(reviewer_overrides=reviewer_overrides, doc_ref=doc_ref,
                        base_provider_id=base_provider_id)
            return "aip_reviewer", "reviewer_override"

        monkeypatch.setattr(review, "resolve_question_responder_with_source", _resolver)
        monkeypatch.setattr(db_runs, "get", lambda run_id: {"provider_id": "aip_header"}
                            if run_id == REQUESTER else None)

        finalize._continue_question_responder_chain(_responder_run())

        assert (201, "answered", None, RESPONDER) in fake.states
        assert [c["item"]["id"] for c in wiring["calls"]] == [202]
        # The next dispatch is rebuilt from the parked chain's durable row (JSON maps
        # parsed, spine doc_ref, requester's provider as the header pick).
        assert seen == {"reviewer_overrides": {"3": "aip_reviewer"}, "doc_ref": SPINE,
                        "base_provider_id": "aip_header"}
        assert wiring["calls"][0]["provider_source"] == "reviewer_override"
        assert wiring["calls"][0]["issued_to"] == "u-worker"

    def test_the_last_answer_dispatches_nothing_more(self, wiring, monkeypatch):
        FakeItems([
            _item(201, 1, answer_count=1, responder_state="dispatched", responder_run_id=RESPONDER),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        finalize._continue_question_responder_chain(_responder_run())
        assert wiring["calls"] == []

    @pytest.mark.parametrize("row", [
        None,
        _paused_row(stop_kind="user", stop_code=None),
        _paused_row(stop_code="no_output_exhausted"),
        _paused_row(stop_code="hop_handoff_interrupted"),
    ])
    def test_a_chain_not_parked_on_question_pending_is_never_continued(self, wiring, monkeypatch, row):
        # Boundary 3: a user pause / another system stop / an already resumed chain.
        self._answered_q1_open_q2().install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: dict(row) if row else None)
        finalize._continue_question_responder_chain(_responder_run())
        assert wiring["calls"] == []

    def test_the_next_item_must_belong_to_the_parked_chain_s_own_stop_run(self, wiring, monkeypatch):
        self._answered_q1_open_q2().install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group",
                            lambda gid: _paused_row(stop_run_id="aiv_some_other_hop"))
        finalize._continue_question_responder_chain(_responder_run())
        assert wiring["calls"] == []

    def test_a_responder_that_registered_nothing_stops_the_sequence(self, wiring, monkeypatch):
        fake = FakeItems([
            _item(201, 1, responder_state="dispatched", responder_run_id=RESPONDER),
            _item(202, 2),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        finalize._continue_question_responder_chain(_responder_run(
            end_reason="timeout", stop_code="timeout", stop_reason="deadline",
        ))
        assert wiring["failed"] == [(201, "timeout", "deadline", RESPONDER)]
        assert wiring["calls"] == []
        assert fake.states == []  # the failure write is q_service.mark_responder_failed's

    def test_an_escalated_responder_stops_the_sequence(self, wiring, monkeypatch):
        FakeItems([
            _item(201, 1, responder_state="user_decision", responder_run_id=RESPONDER),
            _item(202, 2),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        finalize._continue_question_responder_chain(_responder_run())
        assert wiring["calls"] == [] and wiring["failed"] == []

    def test_a_replayed_finalization_does_not_dispatch_twice(self, wiring, monkeypatch):
        fake = self._answered_q1_open_q2().install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        real_dispatch = q_answer_invoke_service.dispatch_answer_run

        def _dispatch_and_claim(**kw):
            # What the real dispatch does inside its issue builder: claim the item.
            fake.claim_responder_dispatch(kw["item"]["id"], "aiv_new_responder",
                                          kw.get("provider_id"), kw.get("provider_source"))
            return real_dispatch(**kw)

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _dispatch_and_claim)
        finalize._continue_question_responder_chain(_responder_run())
        finalize._continue_question_responder_chain(_responder_run())
        assert [c["item"]["id"] for c in wiring["calls"]] == [202]

    def test_a_human_answer_that_landed_meanwhile_counts_as_answered(self, wiring, monkeypatch):
        fake = FakeItems([
            _item(201, 1, answer_count=1, responder_state="answered", responder_run_id=RESPONDER),
            _item(202, 2),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        finalize._continue_question_responder_chain(_responder_run(
            end_reason="timeout", stop_code="timeout",
        ))
        assert wiring["failed"] == []
        assert [c["item"]["id"] for c in wiring["calls"]] == [202]
        assert (201, "answered", None, RESPONDER) in fake.states

    # -- review of rev2 finding 1: a failure CAS lost to a person's answer --

    @staticmethod
    def _human_answers_inside_the_failure_write(fake, monkeypatch):
        """The person's answer commits after settle's read (answer_count 0, 'dispatched')
        and right before the failure CAS -- the window the run-owned CAS must lose."""
        monkeypatch.setattr(q_service, "mark_responder_failed", _REAL_MARK_RESPONDER_FAILED)
        announced: list = []
        monkeypatch.setattr(q_service, "_announce_responder_state",
                            lambda *a, **kw: announced.append(kw))
        real_fail = fake.fail_responder

        def _fail(pk, *a, **kw):
            fake.rows[pk].update(answer_count=1, responder_state="answered")
            return real_fail(pk, *a, **kw)

        monkeypatch.setattr(db_question_items, "fail_responder", _fail)
        return announced

    def test_a_failure_cas_lost_to_a_human_answer_continues_with_the_next_item(self, wiring, monkeypatch):
        fake = FakeItems([
            _item(201, 2, responder_state="dispatched", responder_run_id=RESPONDER),
            _item(202, 3),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        announced = self._human_answers_inside_the_failure_write(fake, monkeypatch)
        finalize._continue_question_responder_chain(_responder_run(
            end_reason="timeout", stop_code="timeout", stop_reason="deadline",
        ))
        # Not reported as failed: Q2 is answered, so exactly Q3 is dispatched next.
        row = fake.rows[201]
        assert (row["answer_count"], row["responder_state"]) == (1, "answered")
        assert (201, "answered", None, RESPONDER) in fake.states
        assert not any(s[1] == "failed" for s in fake.states)
        assert announced == []
        assert [c["item"]["id"] for c in wiring["calls"]] == [202]

    def test_a_failure_cas_refused_without_an_answer_still_stops_the_sequence(self, wiring, monkeypatch):
        FakeItems([
            _item(201, 1, responder_state="dispatched", responder_run_id=RESPONDER),
            _item(202, 2),
        ]).install(monkeypatch)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row())
        monkeypatch.setattr(q_service, "mark_responder_failed", lambda *a, **kw: False)
        finalize._continue_question_responder_chain(_responder_run(
            end_reason="timeout", stop_code="timeout",
        ))
        assert wiring["calls"] == []

    def test_the_settled_outcome_follows_the_reread_row(self, monkeypatch):
        fake = FakeItems([
            _item(201, 1, responder_state="dispatched", responder_run_id=RESPONDER),
        ]).install(monkeypatch)
        self._human_answers_inside_the_failure_write(fake, monkeypatch)
        assert finalize._settle_question_responder_run(
            _responder_run(outcome="none"), [fake.get_by_pk(201)],
        ) == db_question_items.RESPONDER_ANSWERED


# ═══════════════════════════ F3/F5 — outcome classification ═══════════════════════════

class TestResponderOutcomes:
    @pytest.mark.parametrize("run, code", [
        (_responder_run(end_reason="timeout", stop_code="timeout"), "timeout"),
        (_responder_run(end_reason="cancelled", stop_code="cancelled"), "cancelled"),
        (_responder_run(end_reason="all_providers_failed", stop_code="providers_exhausted"),
         "provider_failed"),
        (_responder_run(stop_code=finalize.PROVIDER_FAILED_STOP_CODE), "provider_failed"),
        (_responder_run(lease_denied_code="GROUP_AI_RUN_OWNER_MISMATCH"), "group_lease_denied"),
        (_responder_run(outcome="none", last_message="I could not decide"), "no_answer_registered"),
    ])
    def test_failure_codes_keep_technical_outcomes_apart(self, run, code):
        got_code, message = finalize.responder_failure_of(run)
        assert got_code == code
        assert message
        assert "user_decision" not in got_code

    def test_an_empty_worker_output_is_a_failure_not_a_hand_off(self, wiring, monkeypatch):
        FakeItems([_item(201, 1, responder_state="dispatched", responder_run_id=RESPONDER)]
                  ).install(monkeypatch)
        finalize._continue_question_responder_chain(_responder_run(outcome="none"))
        assert wiring["failed"][0][:2] == (201, "no_answer_registered")


# ═══════════════════════════ sqlite-backed q_service / routes ═══════════════════════════

class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        cur = self._conn.execute(sql, params or [])
        self._conn.commit()
        return cur

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql: str, params=None):
        # Returns the cursor so FlowGateStore._execute_affected can read rowcount — the
        # exact contract the real sqlite adapter honours.
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur is not None else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur is not None else []


PROJECT = "p661"
SQL_GROUP = "p661.none.0001"
SQL_DOC = "p661.none.0001.0001-T"
USER = "u661"


@pytest.fixture()
def store(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mock_db = _MockDB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    # Migration 142 must have applied cleanly — the columns ARE the subject here.
    cols = {r["name"] for r in mock_db._conn.execute("PRAGMA table_info(question_items)")}
    assert {"responder_state", "responder_run_id", "responder_attempts"} <= cols
    acols = {r["name"] for r in mock_db._conn.execute("PRAGMA table_info(answers)")}
    assert {"author_requested_provider_id", "author_provider_source", "author_fallback_used"} <= acols

    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()

    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    projects.create({"project_id": PROJECT, "project_name": "P661"})
    users.create({"user_id": USER, "username": "u661", "email": "u661@e", "password": "x"})
    groups.create({"group_id": SQL_GROUP, "project_id": PROJECT, "module": "none", "title": "G"})
    now = "2026-10-10T00:00:00Z"
    conn_mod.STORE._execute(
        "INSERT OR IGNORE INTO document_types (project_id,type_code,type_name,series,is_system,"
        "is_active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "T", "Task", "instruction", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": SQL_DOC, "project_id": PROJECT, "type_code": "T", "seq": 1,
        "title": "Doc", "group_id": SQL_GROUP, "module": "none",
        "owner_id": USER, "status": "open",
    })
    try:
        yield mock_db
    finally:
        conn_mod.STORE = original
        mock_db.close()
        os.unlink(path)


def _ai_question(asker_run="aiv_asker"):
    res = q_service.add_questions(
        SQL_DOC, [{"title": "범위", "body": "scope?"}], asker_kind="ai",
        asker_provenance={"ai_run_id": asker_run, "actual_provider_id": "aip_asker",
                          "actual_provider_name": "Asker"},
    )
    return res["added_item_ids"][0]


def _item_row(item_id):
    return db_question_items.get_by_pk(item_id)


class TestQServiceGuards:
    def test_claim_is_cas_once_per_run_and_refused_once_answered(self, store):
        item_id = _ai_question()
        assert db_question_items.claim_responder_dispatch(item_id, RESPONDER, "aip_reviewer",
                                                          "sequence_reviewer") is True
        assert db_question_items.claim_responder_dispatch(item_id, RESPONDER) is False
        row = _item_row(item_id)
        assert row["responder_state"] == "dispatched"
        assert row["responder_run_id"] == RESPONDER
        assert row["responder_requested_provider_id"] == "aip_reviewer"
        assert row["responder_provider_source"] == "sequence_reviewer"
        assert row["responder_attempts"] == 1
        # A second run may take it over (a manual re-dispatch after a failure) …
        assert db_question_items.claim_responder_dispatch(item_id, "aiv_r2") is True
        assert _item_row(item_id)["responder_attempts"] == 2
        # … but an answered item is closed to every claim.
        q_service.register_answer(SQL_DOC, item_id, "done", author_kind="human", author_id=USER)
        assert db_question_items.claim_responder_dispatch(item_id, "aiv_r3") is False

    def test_set_responder_state_with_run_id_is_a_cas_on_the_owner(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        assert db_question_items.set_responder_state(item_id, "failed", "timeout", "x",
                                                     run_id="aiv_stale") is False
        assert _item_row(item_id)["responder_state"] == "dispatched"
        assert db_question_items.set_responder_state(item_id, "failed", "timeout", "x",
                                                     run_id=RESPONDER) is True
        assert _item_row(item_id)["responder_error_code"] == "timeout"

    def test_the_asker_run_cannot_answer_its_own_question(self, store):
        item_id = _ai_question(asker_run="aiv_asker")
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                SQL_DOC, item_id, "I answer myself", author_kind="ai",
                author_provenance={"ai_run_id": "aiv_asker"}, writer_ai_run_id="aiv_asker",
            )
        assert exc.value.status_code == 403
        assert _item_row(item_id)["answer_count"] == 0
        assert db_questions.get_container_by_doc(SQL_DOC)["status"] == "pending"
        # A different run (the responder) may, and its requested-provider evidence persists.
        result = q_service.register_answer(
            SQL_DOC, item_id, "reviewer answer", author_kind="ai",
            author_provenance={
                "ai_run_id": RESPONDER, "requested_provider_id": "aip_reviewer",
                "actual_provider_id": "aip_header", "actual_provider_name": "Header Codex",
                "provider_source": "fallback", "fallback_used": True,
            },
            writer_ai_run_id=RESPONDER,
        )
        assert result["status"] == "done"
        detail = q_service.get_qa_detail(SQL_DOC)
        item = next(it for it in detail["items"] if it["id"] == item_id)
        answer = item["answers"][0]
        assert answer["author_provider"] == {
            "ai_run_id": RESPONDER, "ai_provider_id": "aip_header", "ai_provider_name": "Header Codex",
        }
        assert answer["author_provenance"] == {
            "requested_provider_id": "aip_reviewer", "provider_source": "fallback",
            "fallback_used": True,
        }
        assert "author_requested_provider_id" not in answer
        assert item["responder"] is None  # never dispatched in-app
        assert "responder_state" not in item

    def test_a_copy_mention_answer_stays_unconfirmed(self, store):
        item_id = _ai_question()
        q_service.register_answer(SQL_DOC, item_id, "pasted", author_kind="ai",
                                  author_provenance={}, writer_ai_run_id=None)
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert item["answers"][0]["author_provider"] is None
        assert item["answers"][0]["author_provenance"] is None

    def test_any_answer_closes_the_responder_state(self, store, monkeypatch):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        q_service.mark_responder_failed(item_id, "timeout", "deadline", run_id=RESPONDER)
        assert _item_row(item_id)["responder_state"] == "failed"
        q_service.register_answer(SQL_DOC, item_id, "human closes it", author_kind="human",
                                  author_id=USER)
        row = _item_row(item_id)
        assert row["responder_state"] == "answered"
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert item["responder"]["state"] == "answered"
        assert item["responder"]["run_id"] == RESPONDER

    def test_mark_failed_never_flags_an_answered_item_or_a_live_responder(self, store, monkeypatch):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: run_id == RESPONDER)
        # A late admission error (no run id) must not erase the live responder's claim.
        assert q_service.mark_responder_failed(item_id, "run_in_progress", "x") is False
        assert _item_row(item_id)["responder_state"] == "dispatched"
        q_service.register_answer(SQL_DOC, item_id, "a", author_kind="human", author_id=USER)
        assert q_service.mark_responder_failed(item_id, "timeout", "x", run_id=RESPONDER) is False
        assert _item_row(item_id)["responder_state"] == "answered"

    def test_mark_failed_records_code_and_announces_once(self, store, monkeypatch):
        from modules.flow_gate.workflow import event_logger

        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER, "aip_reviewer", "header")
        notices: list[dict] = []
        events: list[dict] = []
        monkeypatch.setattr(q_service, "_notify_responder_state",
                            lambda *a, **kw: notices.append(kw))
        monkeypatch.setattr(event_logger, "log_question_responder_event",
                            lambda **kw: events.append(kw) or {})
        assert q_service.mark_responder_failed(
            item_id, "provider_failed", "Selected model is at capacity", run_id=RESPONDER,
            actor_user_id=USER, notify_audience=USER,
        ) is True
        row = _item_row(item_id)
        assert (row["responder_state"], row["responder_error_code"]) == ("failed", "provider_failed")
        assert row["responder_error_message"] == "Selected model is at capacity"
        assert notices[0]["state"] == "failed" and notices[0]["error_code"] == "provider_failed"
        assert events[0]["event_type"] == event_logger.EVT_QNA_RESPONDER_FAILED
        assert events[0]["doc_id"] == SQL_DOC and events[0]["item_id"] == item_id
        assert events[0]["requested_provider_id"] == "aip_reviewer"
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert item["responder"] == {
            "state": "failed", "run_id": RESPONDER, "requested_provider_id": "aip_reviewer",
            "provider_source": "header", "error_code": "provider_failed",
            "error_message": "Selected model is at capacity", "attempts": 1,
            "updated_at": item["responder"]["updated_at"],
        }

    def test_escalate_is_owned_by_the_dispatched_run_and_writes_no_answer(self, store, monkeypatch):
        from modules.flow_gate.workflow import event_logger

        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        events: list[dict] = []
        monkeypatch.setattr(event_logger, "log_question_responder_event",
                            lambda **kw: events.append(kw) or {})
        for bad in (None, "aiv_asker", "aiv_other"):
            with pytest.raises(HTTPException) as exc:
                q_service.escalate_to_user(SQL_DOC, item_id, "needs a human", writer_ai_run_id=bad)
            assert exc.value.status_code == 403
        result = q_service.escalate_to_user(SQL_DOC, item_id, "Which vendor? Business call.",
                                            writer_ai_run_id=RESPONDER, actor_user_id=USER)
        assert result["state"] == "user_decision" and result["already"] is False
        row = _item_row(item_id)
        assert row["answer_count"] == 0
        assert row["responder_state"] == "user_decision"
        assert row["responder_error_code"] == q_service.RESPONDER_ERROR_USER_DECISION
        assert row["responder_error_message"] == "Which vendor? Business call."
        assert db_questions.get_container_by_doc(SQL_DOC)["status"] == "pending"
        assert db_questions.list_open_doc_ids_by_group(SQL_GROUP) == [SQL_DOC]
        assert events[0]["event_type"] == event_logger.EVT_QNA_USER_DECISION_REQUIRED
        # Repeating the signal is harmless; answering afterwards is refused.
        assert q_service.escalate_to_user(SQL_DOC, item_id, "again", writer_ai_run_id=RESPONDER)["already"] is True
        q_service.register_answer(SQL_DOC, item_id, "the human decides", author_kind="human",
                                  author_id=USER)
        with pytest.raises(HTTPException) as exc:
            q_service.escalate_to_user(SQL_DOC, item_id, "late", writer_ai_run_id=RESPONDER)
        assert exc.value.status_code == 409

    # -- review rej_01M4HRWSVP3M0BSH findings 3/4: one answer per item, none after escalate --

    def test_an_escalated_responder_cannot_answer_afterwards(self, store, monkeypatch):
        from modules.flow_gate.workflow import event_logger

        monkeypatch.setattr(event_logger, "log_question_responder_event", lambda **kw: {})
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        q_service.escalate_to_user(SQL_DOC, item_id, "Business call.", writer_ai_run_id=RESPONDER)
        resumed: list = []
        monkeypatch.setattr(q_service, "auto_resume_answered_chain",
                            lambda **kw: resumed.append(kw))
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                SQL_DOC, item_id, "I changed my mind", author_kind="ai",
                author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
                auto_resume_api_base_url=API,
            )
        assert exc.value.status_code == 409
        row = _item_row(item_id)
        assert row["answer_count"] == 0
        assert row["responder_state"] == "user_decision"
        assert db_questions.get_container_by_doc(SQL_DOC)["status"] == "pending"
        assert resumed == []
        # The person the item was handed to still answers it normally.
        result = q_service.register_answer(SQL_DOC, item_id, "vendor A", author_kind="human",
                                           author_id=USER)
        assert result["status"] == "done"
        assert _item_row(item_id)["responder_state"] == "answered"

    def test_a_responder_cannot_answer_an_item_a_person_answered_first(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        q_service.register_answer(SQL_DOC, item_id, "human first", author_kind="human",
                                  author_id=USER)
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                SQL_DOC, item_id, "ai second", author_kind="ai",
                author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
            )
        assert exc.value.status_code == 409
        assert _item_row(item_id)["answer_count"] == 1
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert [a["body"] for a in item["answers"]] == ["human first"]

    def test_the_cas_holds_when_the_pre_check_read_is_stale(self, store, monkeypatch):
        # A person's answer lands between register_answer's item read and its write: the
        # pre-check sees answer_count 0, the CAS increment in the transaction must not.
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        stale = dict(_item_row(item_id))
        q_service.register_answer(SQL_DOC, item_id, "human first", author_kind="human",
                                  author_id=USER)
        real_get = db_question_items.get_by_pk
        reads = {"n": 0}

        def _stale_once(pk):
            reads["n"] += 1
            return dict(stale) if reads["n"] == 1 else real_get(pk)

        monkeypatch.setattr(db_question_items, "get_by_pk", _stale_once)
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                SQL_DOC, item_id, "ai racing", author_kind="ai",
                author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
            )
        assert exc.value.status_code == 409
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        assert _item_row(item_id)["answer_count"] == 1
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert [a["body"] for a in item["answers"]] == ["human first"]

    def test_the_responder_answers_exactly_once(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        result = q_service.register_answer(
            SQL_DOC, item_id, "reviewer answer", author_kind="ai",
            author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
        )
        assert result["status"] == "done"
        assert _item_row(item_id)["answer_count"] == 1
        assert _item_row(item_id)["responder_state"] == "answered"
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                SQL_DOC, item_id, "again", author_kind="ai",
                author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
            )
        assert exc.value.status_code == 409
        assert _item_row(item_id)["answer_count"] == 1

    def test_responder_cas_sql_refuses_answered_escalated_and_foreign(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        assert db_question_items.increment_answer_count_for_responder(item_id, "aiv_other") is False
        db_question_items.set_responder_state(item_id, "user_decision", "user_decision_required",
                                              "x", run_id=RESPONDER)
        assert db_question_items.increment_answer_count_for_responder(item_id, RESPONDER) is False
        db_question_items.set_responder_state(item_id, "dispatched", None, None, run_id=RESPONDER)
        assert db_question_items.increment_answer_count_for_responder(item_id, RESPONDER) is True
        assert db_question_items.increment_answer_count_for_responder(item_id, RESPONDER) is False
        assert _item_row(item_id)["answer_count"] == 1

    # -- review of rev1: the person's path and the failure/hand-off writes are CAS too --

    @staticmethod
    def _stale_first_read(monkeypatch, stale_row):
        """The service's first item read returns ``stale_row`` (as if the read happened
        before a concurrent commit); later reads are real."""
        real_get = db_question_items.get_by_pk
        reads = {"n": 0}

        def _get(pk):
            reads["n"] += 1
            return dict(stale_row) if reads["n"] == 1 else real_get(pk)

        monkeypatch.setattr(db_question_items, "get_by_pk", _get)
        return real_get

    def _responder_answers(self, item_id):
        return q_service.register_answer(
            SQL_DOC, item_id, "reviewer answer", author_kind="ai",
            author_provenance={"ai_run_id": RESPONDER}, writer_ai_run_id=RESPONDER,
        )

    def test_a_person_cannot_stack_an_answer_on_the_responder_s_answer(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        self._responder_answers(item_id)
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(SQL_DOC, item_id, "human second", author_kind="human",
                                      author_id=USER)
        assert exc.value.status_code == 409
        assert _item_row(item_id)["answer_count"] == 1
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert [a["body"] for a in item["answers"]] == ["reviewer answer"]

    def test_the_person_s_cas_holds_when_the_responder_answered_after_the_read(self, store, monkeypatch):
        # responder: claim -> CAS answer commits; person: read (stale, answer_count 0) -> write.
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        stale = dict(_item_row(item_id))
        self._responder_answers(item_id)
        resumed: list = []
        monkeypatch.setattr(q_service, "auto_resume_answered_chain",
                            lambda **kw: resumed.append(kw))
        real_get = self._stale_first_read(monkeypatch, stale)
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(SQL_DOC, item_id, "human racing", author_kind="human",
                                      author_id=USER, auto_resume_api_base_url=API)
        assert exc.value.status_code == 409
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        assert _item_row(item_id)["answer_count"] == 1
        assert _item_row(item_id)["responder_state"] == "answered"
        item = next(it for it in q_service.get_qa_detail(SQL_DOC)["items"] if it["id"] == item_id)
        assert [a["body"] for a in item["answers"]] == ["reviewer answer"]
        assert resumed == []  # the refused write resumes nothing a second time

    def test_an_item_no_responder_touched_keeps_multiple_answers(self, store):
        item_id = _ai_question()
        q_service.register_answer(SQL_DOC, item_id, "first", author_kind="human", author_id=USER)
        q_service.register_answer(SQL_DOC, item_id, "second", author_kind="human", author_id=USER)
        assert _item_row(item_id)["answer_count"] == 2
        assert _item_row(item_id)["responder_state"] is None

    def test_a_late_failure_does_not_overwrite_an_answer_landed_after_the_read(self, store, monkeypatch):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        stale = dict(_item_row(item_id))
        q_service.register_answer(SQL_DOC, item_id, "human first", author_kind="human",
                                  author_id=USER)
        announced: list = []
        monkeypatch.setattr(q_service, "_announce_responder_state",
                            lambda *a, **kw: announced.append(kw))
        real_get = self._stale_first_read(monkeypatch, stale)
        assert q_service.mark_responder_failed(item_id, "timeout", "late", run_id=RESPONDER) is False
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        row = _item_row(item_id)
        assert (row["responder_state"], row["responder_error_code"]) == ("answered", None)
        assert announced == []
        # The run-less form (admission error / restart sweep) is held the same way.
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        self._stale_first_read(monkeypatch, stale)
        assert q_service.mark_responder_failed(item_id, "run_in_progress", "late") is False
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        assert _item_row(item_id)["responder_state"] == "answered"

    def test_a_late_escalation_does_not_overwrite_an_answer_landed_after_the_read(self, store, monkeypatch):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        stale = dict(_item_row(item_id))
        q_service.register_answer(SQL_DOC, item_id, "human first", author_kind="human",
                                  author_id=USER)
        announced: list = []
        monkeypatch.setattr(q_service, "_announce_responder_state",
                            lambda *a, **kw: announced.append(kw))
        real_get = self._stale_first_read(monkeypatch, stale)
        with pytest.raises(HTTPException) as exc:
            q_service.escalate_to_user(SQL_DOC, item_id, "needs a human", writer_ai_run_id=RESPONDER)
        assert exc.value.status_code == 409
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        row = _item_row(item_id)
        assert (row["responder_state"], row["responder_error_code"]) == ("answered", None)
        assert row["answer_count"] == 1
        assert announced == []

    def test_failure_and_escalation_sql_are_cas_on_answer_count_and_state(self, store):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        # a foreign run changes nothing
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id="aiv_other") is False
        assert db_question_items.escalate_responder(item_id, "aiv_other", "c", "m") is False
        # escalated: neither a run-owned nor a run-less failure replaces the hand-off
        assert db_question_items.escalate_responder(item_id, RESPONDER, "c", "m") is True
        assert db_question_items.escalate_responder(item_id, RESPONDER, "c", "m") is False
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id=RESPONDER) is False
        assert db_question_items.fail_responder(item_id, "dispatch_error", "x") is False
        assert _item_row(item_id)["responder_state"] == "user_decision"
        # the owner's 'dispatched' claim fails once and is not escalated from 'failed'
        db_question_items.set_responder_state(item_id, "dispatched", None, None, run_id=RESPONDER)
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id=RESPONDER) is True
        assert db_question_items.escalate_responder(item_id, RESPONDER, "c", "m") is False
        # answered: every form is refused, whatever the state column says
        q_service.register_answer(SQL_DOC, item_id, "a", author_kind="human", author_id=USER)
        db_question_items.set_responder_state(item_id, "dispatched", None, None, run_id=RESPONDER)
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id=RESPONDER) is False
        assert db_question_items.fail_responder(item_id, "dispatch_error", "x") is False
        assert db_question_items.escalate_responder(item_id, RESPONDER, "c", "m") is False
        assert _item_row(item_id)["responder_state"] == "dispatched"
        assert _item_row(item_id)["answer_count"] == 1

    # -- review of rev2 finding 2: the run-less failure is a CAS on the observed claim --

    def test_a_runless_failure_does_not_overwrite_a_claim_made_after_the_read(self, store, monkeypatch):
        item_id = _ai_question()
        stale = dict(_item_row(item_id))  # untouched: state NULL, no responder run
        db_question_items.claim_responder_dispatch(item_id, RESPONDER, "aip_reviewer", "header")
        # The claiming run is not registered live yet (the claim is written inside the issue
        # builder before admission records the run) -- liveness cannot be what protects it.
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        announced: list = []
        monkeypatch.setattr(q_service, "_announce_responder_state",
                            lambda *a, **kw: announced.append(kw))
        real_get = self._stale_first_read(monkeypatch, stale)
        assert q_service.mark_responder_failed(item_id, "run_in_progress", "stale admission") is False
        monkeypatch.setattr(db_question_items, "get_by_pk", real_get)
        row = _item_row(item_id)
        assert (row["responder_state"], row["responder_run_id"]) == ("dispatched", RESPONDER)
        assert row["responder_error_code"] is None
        assert announced == []

    def test_a_runless_failure_never_touches_a_dispatched_claim(self, store, monkeypatch):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        assert q_service.mark_responder_failed(item_id, "run_in_progress", "x") is False
        assert _item_row(item_id)["responder_state"] == "dispatched"

    def test_a_runless_failure_records_on_an_untouched_or_failed_item(self, store, monkeypatch):
        monkeypatch.setattr(q_service, "_announce_responder_state", lambda *a, **kw: None)
        item_id = _ai_question()
        assert q_service.mark_responder_failed(item_id, "no_enabled_provider", "x") is True
        assert _item_row(item_id)["responder_error_code"] == "no_enabled_provider"
        # a later admission refusal replaces the earlier failure code
        assert q_service.mark_responder_failed(item_id, "provider_unavailable", "y") is True
        assert _item_row(item_id)["responder_error_code"] == "provider_unavailable"

    def test_runless_failure_sql_is_a_cas_on_the_observed_state_and_run(self, store):
        item_id = _ai_question()
        # observed NULL/NULL, but a responder claimed since -> refused
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        assert db_question_items.fail_responder(item_id, "c", "m") is False
        # even when the caller observed exactly this claim: 'dispatched' is never a target
        assert db_question_items.fail_responder(
            item_id, "c", "m", observed_state="dispatched", observed_run_id=RESPONDER) is False
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id=RESPONDER) is True
        # 'failed' by RESPONDER, re-claimed and failed again by a newer run -> stale observation
        db_question_items.claim_responder_dispatch(item_id, "aiv_r2")
        assert db_question_items.fail_responder(item_id, "timeout", "x", run_id="aiv_r2") is True
        assert db_question_items.fail_responder(
            item_id, "c", "m", observed_state="failed", observed_run_id=RESPONDER) is False
        assert db_question_items.fail_responder(
            item_id, "c", "m", observed_state="failed", observed_run_id="aiv_r2") is True
        row = _item_row(item_id)
        assert (row["responder_state"], row["responder_run_id"], row["responder_error_code"]) == (
            "failed", "aiv_r2", "c")

    def test_escalation_does_not_resume_the_parked_chain(self, store, monkeypatch):
        # The group-wide open-Q gate of auto_resume_answered_chain is what keeps the chain
        # parked; an escalated item is still open, so resume_chain is never reached.
        from modules.flow_gate.services import ai_invoke_service

        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        q_service.escalate_to_user(SQL_DOC, item_id, "decide", writer_ai_run_id=RESPONDER)
        monkeypatch.setattr(db_paused, "get_by_group", lambda gid: _paused_row(group_id=SQL_GROUP))
        resumed: list = []
        monkeypatch.setattr(ai_invoke_service, "resume_chain", lambda **kw: resumed.append(kw) or {})
        assert q_service.auto_resume_answered_chain(doc_id=SQL_DOC, api_base_url=API) is None
        assert resumed == []

    def test_oracle_is_satisfied_by_this_run_s_escalation_only(self, store):
        item_id = _ai_question()
        holder = {"run_id": RESPONDER}
        oracle = q_answer_invoke_service._make_oracle(item_id, 0, holder)
        assert oracle() is False
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        assert oracle() is False
        db_question_items.set_responder_state(item_id, "user_decision", "user_decision_required",
                                              "r", run_id=RESPONDER)
        assert oracle() is True
        assert q_answer_invoke_service._make_oracle(item_id, 0, {"run_id": "aiv_other"})() is False


class TestRoutes:
    @pytest.fixture(autouse=True)
    def _perm(self):
        with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
            yield

    def _client(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from modules.flow_gate.api.v1 import q_tapi_routes
        app = FastAPI()
        app.include_router(q_tapi_routes.router)
        return TestClient(app, raise_server_exceptions=True)

    def _edit_token(self, tmp_path, *, ai_run_id=None, doc_ref=SQL_DOC):
        from modules.flow_gate.services import token_service
        # token_scratch.create refuses a scratch path outside the storage root (pytest's
        # tmp_path always is); the on-disk scratch is irrelevant to these route tests.
        with (
            patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"),
            patch.object(token_service.token_scratch, "create", lambda *a, **kw: None),
        ):
            return token_service.issue(
                project=PROJECT, group_id=SQL_GROUP, action_scope="edit",
                doc_ref=doc_ref, issued_to=USER, ai_run_id=ai_run_id,
            )["raw_token"]

    def _session(self):
        from modules.flow_gate.auth.jwt_service import create_access_token
        from modules.flow_gate.db.connection import get_store
        get_store()._db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", [USER])
        return create_access_token(USER, "u661", [])[0]

    def test_a_login_session_cannot_forge_an_ai_answer(self, store):
        item_id = _ai_question()
        resp = self._client().post(
            f"/api/v1/q/{SQL_DOC}/items/{item_id}/answers",
            json={"author_kind": "ai", "body": "pretending"},
            headers={"Authorization": f"Bearer {self._session()}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["author_kind"] == "human"
        row = store.fetch_one("SELECT author_kind, author_id FROM answers WHERE question_item_id = ?",
                              [item_id])
        assert row == {"author_kind": "human", "author_id": USER}

    def test_the_asker_s_own_token_is_refused_and_the_chain_stays_parked(self, store, tmp_path):
        item_id = _ai_question(asker_run="aiv_asker")
        resp = self._client().post(
            f"/api/v1/q/{SQL_DOC}/items/{item_id}/answers",
            json={"body": "answering myself"},
            headers={"Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id='aiv_asker')}"},
        )
        assert resp.status_code == 403, resp.text
        assert "registered itself" in resp.json()["error_message"]
        assert _item_row(item_id)["answer_count"] == 0

    def test_a_token_bound_run_keeps_its_run_id_even_without_a_live_record(self, store, tmp_path):
        item_id = _ai_question()
        with patch("modules.flow_gate.services.ai_invoke.runtime.get_run_record", return_value=None):
            resp = self._client().post(
                f"/api/v1/q/{SQL_DOC}/items/{item_id}/answers",
                json={"body": "answer"},
                headers={"Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id=RESPONDER)}"},
            )
        assert resp.status_code == 200, resp.text
        row = store.fetch_one(
            "SELECT author_ai_run_id, author_actual_provider_id FROM answers WHERE question_item_id = ?",
            [item_id])
        assert row == {"author_ai_run_id": RESPONDER, "author_actual_provider_id": None}

    def test_a_responder_run_may_not_register_questions(self, store, tmp_path):
        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        resp = self._client().post(
            f"/api/v1/q/{SQL_DOC}/questions",
            json={"asker_kind": "ai", "questions": [{"body": "a question about the question?"}]},
            headers={"Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id=RESPONDER)}"},
        )
        assert resp.status_code == 403, resp.text
        assert "escalate" in resp.json()["error_message"]
        assert len(q_service.get_qa_detail(SQL_DOC)["items"]) == 1

    def test_escalate_route_requires_the_dispatched_responder_token(self, store, tmp_path, monkeypatch):
        from modules.flow_gate.workflow import event_logger

        item_id = _ai_question()
        db_question_items.claim_responder_dispatch(item_id, RESPONDER)
        monkeypatch.setattr(event_logger, "log_question_responder_event", lambda **kw: {})
        client = self._client()
        url = f"/api/v1/q/{SQL_DOC}/items/{item_id}/escalate"
        body = {"reason": "Requirement choice between A and B — business decision."}
        assert client.post(url, json=body,
                           headers={"Authorization": f"Bearer {self._session()}"}).status_code == 403
        assert client.post(url, json=body,
                           headers={"Authorization": f"Bearer {self._edit_token(tmp_path)}"}).status_code == 403
        assert client.post(url, json=body, headers={
            "Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id='aiv_other')}",
        }).status_code == 403
        assert client.post(url, json={"reason": "  "}, headers={
            "Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id=RESPONDER)}",
        }).status_code == 422
        resp = client.post(url, json=body, headers={
            "Authorization": f"Bearer {self._edit_token(tmp_path, ai_run_id=RESPONDER)}",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "user_decision"
        row = _item_row(item_id)
        assert row["answer_count"] == 0 and row["responder_state"] == "user_decision"
        assert db_questions.get_container_by_doc(SQL_DOC)["status"] == "pending"


# ═══════════════════════════ dispatch_answer_run guards ═══════════════════════════

class TestDispatchAnswerRunGuards:
    @pytest.fixture(autouse=True)
    def _wire(self, monkeypatch):
        self.fake = FakeItems([_item(201, 1)]).install(monkeypatch)
        self.failed: list[tuple] = []
        monkeypatch.setattr(q_service, "mark_responder_failed",
                            lambda item_id, code, message, **kw: self.failed.append(
                                (item_id, code, message)) or True)
        monkeypatch.setattr(q_answer_invoke_service, "_ai_answer_count", lambda item_id: 0)

    def _dispatch(self, **kw):
        return q_answer_invoke_service.dispatch_answer_run(
            doc=dict(DOC), item={"id": 201, "seq": 1, "body": "b", "options": []},
            issued_to="u-worker", api_base_url=API, **kw,
        )

    def test_a_live_responder_on_the_item_is_refused_before_admission(self, monkeypatch):
        self.fake.rows[201].update(responder_state="dispatched", responder_run_id=RESPONDER)
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: run_id == RESPONDER)
        started: list = []
        monkeypatch.setattr(q_answer_invoke_service.ai_invoke_service, "start_run",
                            lambda **kw: started.append(kw) or {"run_id": "x"})
        with pytest.raises(HTTPException) as exc:
            self._dispatch()
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == q_answer_invoke_service.RESPONDER_IN_PROGRESS_CODE
        assert exc.value.detail["run_id"] == RESPONDER
        assert started == [] and self.failed == []

    def test_a_dead_dispatched_claim_does_not_block_a_new_dispatch(self, monkeypatch):
        self.fake.rows[201].update(responder_state="dispatched", responder_run_id="aiv_dead")
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)

        def _start_run(**kw):
            kw["issue_builder"]("aiv_fresh")
            return {"run_id": "aiv_fresh", "status": "running"}

        monkeypatch.setattr(q_answer_invoke_service.ai_invoke_service, "start_run", _start_run)
        monkeypatch.setattr(q_answer_invoke_service, "issue_answer_token", lambda **kw: {"raw_token": "t"})
        assert self._dispatch(provider_id="aip_reviewer", provider_source="sequence_reviewer")["run_id"] == "aiv_fresh"
        assert self.fake.claims == [(201, "aiv_fresh", "aip_reviewer", "sequence_reviewer")]
        assert self.fake.rows[201]["responder_run_id"] == "aiv_fresh"

    def test_an_admission_refusal_is_recorded_on_the_item_and_re_raised(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)

        def _refuse(**kw):
            raise HTTPException(status_code=409, detail={
                "code": "no_enabled_provider", "message": "No enabled provider."})

        monkeypatch.setattr(q_answer_invoke_service.ai_invoke_service, "start_run", _refuse)
        with pytest.raises(HTTPException) as exc:
            self._dispatch()
        assert exc.value.detail["code"] == "no_enabled_provider"
        assert self.failed == [(201, "no_enabled_provider", "No enabled provider.")]

    # -- review rej_01M4HRWSVP3M0BSH finding 3: the claim gates the token --

    def _start_run_calling_builder(self, monkeypatch, run_id="aiv_fresh", calls=1):
        tokens: list = []
        monkeypatch.setattr(q_answer_invoke_service, "issue_answer_token",
                            lambda **kw: tokens.append(kw) or {"raw_token": "t", "mention": "m"})

        def _start_run(**kw):
            for _ in range(calls):
                kw["issue_builder"](run_id)
            return {"run_id": run_id, "status": "running"}

        monkeypatch.setattr(q_answer_invoke_service.ai_invoke_service, "start_run", _start_run)
        return tokens

    def test_a_claim_lost_to_a_person_s_answer_mints_no_token(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        # Answered between dispatch_answer_run's checks and the builder's claim.
        self.fake.rows[201]["answer_count"] = 1
        tokens = self._start_run_calling_builder(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            self._dispatch()
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == q_answer_invoke_service.RESPONDER_CLAIM_REFUSED_CODE
        assert tokens == []
        assert self.fake.claims == []

    def test_a_claim_write_error_mints_no_token(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)

        def _boom(*a, **kw):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(db_question_items, "claim_responder_dispatch", _boom)
        tokens = self._start_run_calling_builder(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            self._dispatch()
        assert exc.value.detail["code"] == q_answer_invoke_service.RESPONDER_CLAIM_REFUSED_CODE
        assert tokens == []
        # The refusal is visible on the item (the real mark_responder_failed skips answered items).
        assert self.failed and self.failed[0][1] == q_answer_invoke_service.RESPONDER_CLAIM_REFUSED_CODE

    def test_an_item_claimed_by_another_run_mints_no_token(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        monkeypatch.setattr(db_question_items, "claim_responder_dispatch",
                            lambda *a, **kw: False)
        self.fake.rows[201].update(responder_state="dispatched", responder_run_id="aiv_other")
        tokens = self._start_run_calling_builder(monkeypatch)
        with pytest.raises(HTTPException):
            self._dispatch()
        assert tokens == []

    def test_a_token_reissue_inside_the_claiming_run_is_allowed(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        tokens = self._start_run_calling_builder(monkeypatch, calls=2)
        assert self._dispatch()["run_id"] == "aiv_fresh"
        assert len(tokens) == 2
        assert self.fake.claims == [(201, "aiv_fresh", None, None)]

    def test_a_failure_after_the_claim_is_left_to_the_run_s_own_finalization(self, monkeypatch):
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: False)
        monkeypatch.setattr(q_answer_invoke_service, "issue_answer_token", lambda **kw: {"raw_token": "t"})

        def _claim_then_fail(**kw):
            kw["issue_builder"]("aiv_fresh")
            raise HTTPException(status_code=500, detail={"code": "launch_failed"})

        monkeypatch.setattr(q_answer_invoke_service.ai_invoke_service, "start_run", _claim_then_fail)
        with pytest.raises(HTTPException):
            self._dispatch()
        assert self.failed == []
        assert self.fake.rows[201]["responder_state"] == "dispatched"


# ═══════════════════════════ restart sweep ═══════════════════════════

class TestStartupSweep:
    def test_dead_dispatched_items_become_visible_failures(self, monkeypatch):
        fake = FakeItems([
            _item(201, 1, responder_state="dispatched", responder_run_id="aiv_dead"),
            _item(202, 2, responder_state="dispatched", responder_run_id="aiv_dead2", answer_count=1),
            _item(203, 3, responder_state="dispatched", responder_run_id="aiv_live"),
            _item(204, 4),
        ]).install(monkeypatch)
        monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: run_id == "aiv_live")
        failed: list[tuple] = []
        monkeypatch.setattr(q_service, "mark_responder_failed",
                            lambda item_id, code, message, **kw: failed.append(
                                (item_id, code, kw.get("run_id"))) or True)
        assert q_service.startup_recover_question_responders() == 2
        assert failed == [(201, "interrupted", "aiv_dead")]
        assert (202, "answered", None, "aiv_dead2") in fake.states
        assert fake.rows[203]["responder_state"] == "dispatched"
