"""flowgate.default.0359 — 3번 묶음: 워커에게 "여기서 끝"이라고 말하기.

B0001 "연속 실행이 끝까지 안가는거 같은 느낌" → NR0003 → P0006 → L0007 §2.11 / §2.12.

The inbox self-chain has always had eight ways to stop and one way to say so: prose in a
`continuation_reason` field, inside an HTTP response whose only reader is a worker about to
exit. Meanwhile the mention that worker is holding says, four times over, "do NOT stop —
continue with the enclosed token". When no token was enclosed those two instructions
contradict each other, and nothing machine-readable settled it.

This suite fixes that contract in place:

  §2.12  every stop names itself (`continuation_stop_code`), carries the one §4.3 sentence
         for that code, says whether it can be resumed, and hands the worker the fixed
         English sentence (A~D) that OVERRIDES the mention's "keep going".
  §2.11  the four stops a human has to clean up also leave a notification behind, anchored
         on the document that needs the triage — and the engine's three codes are disjoint
         from the inbox's four, so nothing is ever announced twice.
  §4.1   the live engine run is tagged with the inbox's code, so the run record and the
         miniplayer card end up saying the same thing the worker was told.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402

GROUP = "flowgate.default.0359"
SPINE = "flowgate.default.0359.0001-B"
SUBMITTED = "flowgate.default.0359.0013-TR"
TARGET_SEQ = 14
RUN_ID = "aiv_0359_1"


class _FakeRequest:
    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://h/"


def _token_rec(**over) -> dict:
    rec = {
        "doc_ref": SPINE,
        "issued_to": "pm",
        "group_id": GROUP,
        "token_id": "tok_20260731_000017",
        "ai_run_id": RUN_ID,
        "continuation_target_seq": TARGET_SEQ,
        "continuation_review_mode": 0,
        "continuation_instruction_mode": "auto_approved",
    }
    rec.update(over)
    return rec


@pytest.fixture
def env(monkeypatch):
    """One rig for both self-chaining paths: every outbound effect captured, none real."""
    from modules.flow_gate.db import workflow_sequences as wfseq
    from modules.flow_gate.workflow import event_logger
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    class Rig:
        def __init__(self):
            self.notifications: list[dict] = []
            self.ended: list[dict] = []
            self.tagged: list[tuple] = []
            self.approve = MagicMock()
            self.advance = MagicMock(return_value={
                "token": "raw", "token_id": "tok_next", "mention": "m",
                "expires_at": "t", "continuation_remaining": 1,
            })
            self.completed_seq = TARGET_SEQ - 1
            self.user_paused_row = None
            self.live_run = True

    rig = Rig()

    monkeypatch.setattr(
        wfseq, "get_item_by_result_doc_id",
        lambda _d: (None if rig.completed_seq is None
                    else {"item_seq": rig.completed_seq, "type": "TR"}),
    )
    monkeypatch.setattr(inbox_routes, "_normalize_continuation_target",
                        lambda target, *a, **kw: target)
    monkeypatch.setattr(pipeline_service, "transition_document_review", rig.approve)
    monkeypatch.setattr(wds, "advance_workflow", rig.advance)
    # An admin approver: the permission branch is 0086's, not this bundle's.
    monkeypatch.setattr(
        "modules.flow_gate.workflow.routers.workflow._get_user_permissions",
        lambda _u: {"document.approve"},
    )
    monkeypatch.setattr("modules.flow_gate.db.users.get_by_id",
                        lambda _u: {"user_id": "pm", "is_admin": 1})
    monkeypatch.setattr(db_paused, "get_by_group", lambda _g: rig.user_paused_row)
    monkeypatch.setattr(db_paused, "delete_by_group", lambda _g: None)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda d: {
        "id": 77, "group_id": GROUP, "project_id": "flowgate", "doc_id": d,
    })
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda d: {
        "id": 77, "group_id": GROUP, "project_id": "flowgate", "doc_id": d,
    })
    monkeypatch.setattr(
        event_logger, "log_continuous_work_failed",
        lambda **kw: rig.notifications.append(kw) or {},
    )
    monkeypatch.setattr(
        event_logger, "log_continuous_work_ended",
        lambda **kw: rig.ended.append(kw) or {},
    )

    def _mark(group_id, stop_code, detail=None):
        rig.tagged.append((group_id, stop_code, detail))
        return rig.live_run

    monkeypatch.setattr(svc, "mark_chain_stop", _mark)
    def _mark_user_paused(group_id, run_id):
        row = rig.user_paused_row or {}
        return bool(
            group_id == GROUP
            and run_id == RUN_ID
            and (row.get("stop_kind") or "user") == "user"
            and row.get("stop_run_id") == RUN_ID
        )

    monkeypatch.setattr(svc, "mark_user_paused", _mark_user_paused)
    monkeypatch.setattr(svc, "has_active_run", lambda _g: False)
    monkeypatch.setattr(svc, "get_active_status", lambda _g: {"run_id": "aiv_1"})
    monkeypatch.setattr(svc, "request_auto_resume", lambda *a, **kw: None)
    return rig


def _chain(doc_type="TR", **token_over) -> dict:
    return inbox_routes._continuation_self_chain(
        _FakeRequest(), _token_rec(**token_over), "flowgate", SUBMITTED, doc_type,
    )


# ── §2.12: every stop names itself ───────────────────────────────────────────

class TestStopCodeOnEveryBranch:
    def test_hop_handoff_states_the_next_slot(self, env, monkeypatch):
        # An engine-driven chain: the next hop gets a FRESH worker, so this one is done even
        # though the chain is not. Before 0359 the worker was told none of that.
        monkeypatch.setattr(svc, "has_active_run", lambda _g: True)
        env.completed_seq = 8
        e = _chain()
        assert e["continuation_stop_code"] == "hop_handoff"
        assert e["continuation_stop_reason"] == (
            "This hop produced its document; the next hop starts in a new worker."
        )
        assert e["continuation_resumable"] is False
        # §2.12: stated, not re-queried — the next hop is the slot after the one just filled.
        assert e["continuation_completed_item_seq"] == 8
        assert e["continuation_next_item_seq"] == 9
        assert "next_token" not in e

    def test_chain_completed_names_the_target(self, env):
        env.completed_seq = TARGET_SEQ
        e = _chain()
        assert e["continuation_stop_code"] == "chain_completed"
        assert e["continuation_stop_reason"] == (
            f"Target step {TARGET_SEQ} reached; the chain is complete."
        )
        assert e["continuation_done"] is True
        assert len(env.ended) == 1          # the existing 0125 end signal still fires

    def test_head_slot_mismatch(self, env):
        env.completed_seq = None
        e = _chain(doc_type="M")
        assert e["continuation_stop_code"] == "head_slot_mismatch"
        assert e["continuation_stop_reason"] == (
            "The submitted document did not fill the current workflow head slot; "
            "a human must triage."
        )
        assert e["continuation_paused"] is True
        env.approve.assert_not_called()     # 0226 §5-3 stays: the stray doc is not approved

    def test_approve_denied(self, env, monkeypatch):
        monkeypatch.setattr(
            "modules.flow_gate.workflow.routers.workflow._get_user_permissions",
            lambda _u: set(),
        )
        e = _chain()
        assert e["continuation_stop_code"] == "approve_denied"
        assert "lacks document.approve" in e["continuation_stop_reason"]

    def test_approve_failed_carries_the_detail(self, env):
        env.approve.side_effect = RuntimeError("head already advanced")
        e = _chain()
        assert e["continuation_stop_code"] == "approve_failed"
        assert e["continuation_stop_reason"] == (
            "Auto-approval failed: head already advanced"
        )

    def test_advance_blocked_carries_the_detail(self, env):
        env.advance.side_effect = ValueError("sequence exhausted")
        e = _chain()
        assert e["continuation_stop_code"] == "advance_blocked"
        assert e["continuation_stop_reason"] == (
            "Could not advance to the next step: sequence exhausted"
        )

    def test_review_hold(self, env):
        e = _chain(continuation_review_mode=1)
        assert e["continuation_stop_code"] == "review_hold"
        assert e["continuation_stop_reason"] == "Review mode: waiting for the human go."

    def test_user_paused_is_the_one_stop_a_worker_may_hand_back(self, env):
        env.user_paused_row = {"group_id": GROUP, "stop_kind": "user", "stop_run_id": RUN_ID}
        e = _chain()
        assert e["continuation_stop_code"] == "user_paused"
        assert e["continuation_resumable"] is True

    def test_advancing_chain_gets_no_stop_code(self, env):
        # The semi-manned (copy-mention) path still hands out a token and must be untouched.
        e = _chain()
        assert e["next_token"] == "raw"
        assert "continuation_stop_code" not in e
        assert "continuation_resumable" not in e

    def test_ordinary_token_is_still_none(self, env):
        assert _chain(continuation_target_seq=None) is None

    @pytest.mark.parametrize("code,resumable", [
        ("hop_handoff", False), ("chain_completed", False), ("head_slot_mismatch", False),
        ("approve_denied", False), ("approve_failed", False), ("advance_blocked", False),
        ("review_hold", False), ("user_paused", True),
    ])
    def test_resumable_matches_l0007_4_2(self, code, resumable):
        assert svc.is_resumable(code) is resumable

    def test_existing_reason_prose_is_not_removed(self, env):
        # P0006 부록 D: additive only. 0226's assertion reads continuation_reason.
        env.completed_seq = None
        e = _chain(doc_type="M")
        assert "did not fill the current workflow head slot" in e["continuation_reason"]


# ── §2.12: the sentence that overrides the mention ───────────────────────────

class TestEndSentence:
    def _msg(self, chain):
        return inbox_routes._chain_message(chain, SUBMITTED)

    def test_sentence_a_for_hop_handoff(self):
        m = self._msg({"continuation_stop_code": "hop_handoff"})
        assert m.startswith(f"{SUBMITTED} registered. Your chain step ends here")
        assert "Do NOT wait for a next token" in m
        assert "end this session now" in m

    def test_sentence_b_for_completion(self):
        m = self._msg({"continuation_stop_code": "chain_completed"})
        assert "reached its target step and is COMPLETE" in m
        assert "No further token will be issued" in m

    @pytest.mark.parametrize("code", [
        "head_slot_mismatch", "approve_denied", "approve_failed", "advance_blocked",
    ])
    def test_sentence_c_for_every_triage_stop(self, code):
        m = self._msg({"continuation_stop_code": code})
        assert "STOPPED here. A human must triage." in m
        assert "Do NOT continue and do NOT retry" in m

    def test_sentence_d_for_review_mode(self):
        m = self._msg({"continuation_stop_code": "review_hold"})
        assert "Review mode: the run waits for the human go" in m

    def test_user_pause_also_gets_told_to_stop(self):
        # Not in §2.12's table, which lists only self-inflicted stops. Without a sentence the
        # worker would be left on "You may end the session." while its mention says the
        # opposite — the exact contradiction A~D exist to settle.
        m = self._msg({"continuation_stop_code": "user_paused"})
        assert "PAUSED by the user" in m
        assert "do NOT continue" in m

    def test_a_token_in_hand_outranks_every_stop_sentence(self):
        # A semi-manned chain really does have to keep going; §2.12's rule checks next_token
        # first for exactly this reason.
        m = self._msg({"next_token": "raw", "continuation_stop_code": "hop_handoff"})
        assert "proceed to the next step with next_token/next_mention" in m

    def test_no_sentence_leaves_the_default_alone(self):
        assert self._msg({}) is None
        assert self._msg({"continuation_stop_code": "something_new"}) is None

    def test_every_stop_code_this_module_emits_has_a_sentence(self, env, monkeypatch):
        # A stop with no sentence is a silent regression: the envelope changes, the worker
        # keeps reading "You may end the session." and keeps going.
        emitted = set()
        real = svc.stamp_chain_stop

        def _spy(envelope, stop_code, **kw):
            emitted.add(stop_code)
            return real(envelope, stop_code, **kw)

        monkeypatch.setattr(svc, "stamp_chain_stop", _spy)
        env.completed_seq = None
        _chain(doc_type="M")
        env.completed_seq = TARGET_SEQ
        _chain()
        env.completed_seq = TARGET_SEQ - 1
        _chain(continuation_review_mode=1)
        env.user_paused_row = {
            "group_id": GROUP, "stop_kind": "user", "stop_run_id": RUN_ID,
        }
        _chain()
        env.user_paused_row = None
        env.advance.side_effect = ValueError("x")
        _chain()
        monkeypatch.setattr(svc, "has_active_run", lambda _g: True)
        _chain()
        assert emitted == {
            "head_slot_mismatch", "chain_completed", "review_hold",
            "user_paused", "advance_blocked", "hop_handoff",
        }
        assert emitted <= set(inbox_routes._CHAIN_END_MESSAGES)


# ── §2.11: the human hears about it ──────────────────────────────────────────

class TestHumanSignal:
    @pytest.mark.parametrize("code", [
        "head_slot_mismatch", "approve_denied", "approve_failed", "advance_blocked",
    ])
    def test_triage_stops_leave_a_notification(self, env, monkeypatch, code):
        if code == "head_slot_mismatch":
            env.completed_seq = None
            _chain(doc_type="M")
        elif code == "approve_denied":
            monkeypatch.setattr(
                "modules.flow_gate.workflow.routers.workflow._get_user_permissions",
                lambda _u: set(),
            )
            _chain()
        elif code == "approve_failed":
            env.approve.side_effect = RuntimeError("boom")
            _chain()
        else:
            env.advance.side_effect = ValueError("boom")
            _chain()
        assert len(env.notifications) == 1
        note = env.notifications[0]
        assert note["extra"]["stop_code"] == code
        assert note["group_id"] == GROUP
        assert note["target_seq"] == TARGET_SEQ
        # Anchored on the document that needs the triage, not on a dead hop with no document.
        assert note["doc_id"] == SUBMITTED
        assert note["document_id"] == 77
        assert note["error"]                       # the §4.3 sentence, not an empty field

    @pytest.mark.parametrize("case", ["handoff", "completed", "review", "paused", "advanced"])
    def test_intended_stops_stay_quiet(self, env, monkeypatch, case):
        # P0006: 알림에 올리는 것은 사람이 의도하지 않은 정지뿐. An intended stop that pages a
        # human is how the old notification flood came back.
        if case == "handoff":
            monkeypatch.setattr(svc, "has_active_run", lambda _g: True)
            _chain()
        elif case == "completed":
            env.completed_seq = TARGET_SEQ
            _chain()
        elif case == "review":
            _chain(continuation_review_mode=1)
        elif case == "paused":
            env.user_paused_row = {"group_id": GROUP}
            _chain()
        else:
            _chain()
        assert env.notifications == []

    def test_the_notification_names_the_run_it_stopped(self, env, monkeypatch):
        # NR0003 §4 found 1,346 continuous tokens with no bridge back to their execution. A
        # notification that cannot name its run is that same dead end in a new place.
        monkeypatch.setattr(svc, "_active_run_for_group",
                            lambda _g: {"run_id": "aiv_20260731_000041"})
        env.completed_seq = None
        _chain(doc_type="M")
        assert env.notifications[0]["run_id"] == "aiv_20260731_000041"

    def test_a_copy_mention_stop_still_notifies_without_a_run(self, env):
        # No engine run to name: the notification must still go out — that chain has no other
        # way to reach a human at all.
        env.completed_seq = None
        _chain(doc_type="M")
        assert len(env.notifications) == 1
        assert env.notifications[0]["run_id"] is None

    def test_the_two_speakers_never_overlap(self):
        # L0007 §2.11: a double notification is impossible by construction, not by a flag.
        assert not (svc.ENGINE_NOTIFY_STOP_CODES & svc.INBOX_NOTIFY_STOP_CODES)
        assert svc.NOTIFY_STOP_CODES == (
            svc.ENGINE_NOTIFY_STOP_CODES | svc.INBOX_NOTIFY_STOP_CODES
        )
        assert svc.INBOX_NOTIFY_STOP_CODES == {
            "head_slot_mismatch", "approve_denied", "approve_failed", "advance_blocked",
        }

    def test_a_failed_notification_never_breaks_the_submission(self, env, monkeypatch):
        from modules.flow_gate.workflow import event_logger
        monkeypatch.setattr(event_logger, "log_continuous_work_failed",
                            MagicMock(side_effect=RuntimeError("feed down")))
        env.completed_seq = None
        e = _chain(doc_type="M")
        assert e["continuation_stop_code"] == "head_slot_mismatch"


# ── §4.1: the live run agrees with what the worker was told ──────────────────

class TestRunTagging:
    def test_stop_is_tagged_onto_the_live_run(self, env):
        env.completed_seq = None
        _chain(doc_type="M")
        assert (GROUP, "head_slot_mismatch", None) in env.tagged

    def test_detail_reaches_the_run_for_the_two_codes_that_quote_it(self, env):
        env.advance.side_effect = ValueError("sequence exhausted")
        _chain()
        assert env.tagged[-1] == (GROUP, "advance_blocked", "sequence exhausted")

    def test_a_copy_mention_chain_has_no_run_to_tag(self, env):
        # Semi-manned chains have no engine run at all; tagging is a no-op and the envelope
        # is still fully stamped — that response is the only thing anyone will ever read.
        env.live_run = False
        env.completed_seq = None
        e = _chain(doc_type="M")
        assert e["continuation_stop_code"] == "head_slot_mismatch"
        assert len(env.notifications) == 1

    def test_a_tagging_failure_never_breaks_the_submission(self, env, monkeypatch):
        monkeypatch.setattr(svc, "mark_chain_stop",
                            MagicMock(side_effect=RuntimeError("run vanished")))
        env.completed_seq = None
        e = _chain(doc_type="M")
        assert e["continuation_stop_code"] == "head_slot_mismatch"

    def test_the_inbox_code_loses_to_a_cancel_or_a_deadline(self):
        # L0007 §4.1: 1~4 outrank the inbox tag. A human cancel or a blown deadline is the
        # truth no matter what the self-chain thought it was doing.
        for end_reason, expected in [("cancelled", "cancelled"), ("timeout", "timeout")]:
            run = {"end_reason": end_reason, "inbox_stop_code": "advance_blocked",
                   "mode": "continuous"}
            assert svc._resolve_stop_code(run, False) == expected
        run = {"end_reason": "exited", "inbox_stop_code": "advance_blocked",
               "mode": "continuous"}
        assert svc._resolve_stop_code(run, False) == "advance_blocked"


# ── the decide→first-step handoff is a step boundary too ─────────────────────

class TestDecideKickoff:
    def _kickoff(self, **kw):
        return wds.continuation_kickoff_after_decide(
            doc_id=SPINE, issued_to="pm", api_base_url="http://h",
            continuation_target_seq=TARGET_SEQ, ai_run_id=RUN_ID, **kw,
        )

    @pytest.fixture(autouse=True)
    def _seq(self, env, monkeypatch):
        monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _d: {"id": 1})
        monkeypatch.setattr(wds.db_wfseq, "get_sequence_items",
                            lambda _s: [{"item_seq": TARGET_SEQ, "type": "TR"}])
        monkeypatch.setattr(wds.db_documents, "get_by_id", lambda d: {
            "id": 77, "group_id": GROUP, "project_id": "flowgate", "doc_id": d,
        })
        self.env = env

    def test_advance_failure_stops_and_signals(self, monkeypatch):
        monkeypatch.setattr(wds, "advance_workflow",
                            MagicMock(side_effect=RuntimeError("no head")))
        e = self._kickoff()
        assert e["continuation_stop_code"] == "advance_blocked"
        assert "no head" in e["continuation_stop_reason"]
        assert e["continuation_resumable"] is False
        assert len(self.env.notifications) == 1

    def test_undecided_sequence_stops(self, monkeypatch):
        monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _d: None)
        e = self._kickoff()
        assert e["continuation_stop_code"] == "advance_blocked"
        assert "sequence not decided" in e["continuation_stop_reason"]

    def test_boundary_pause_stops_quietly(self, monkeypatch):
        self.env.user_paused_row = {
            "group_id": GROUP, "stop_kind": "user", "stop_run_id": RUN_ID,
        }
        e = self._kickoff()
        assert e["continuation_stop_code"] == "user_paused"
        assert e["continuation_resumable"] is True
        assert self.env.notifications == []

    def test_a_healthy_kickoff_is_unchanged(self):
        e = self._kickoff()
        assert e["next_token"] == "raw"
        assert "continuation_stop_code" not in e
