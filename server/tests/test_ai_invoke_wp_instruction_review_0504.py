"""flowgate.default.0504 T0004: a reviewed WorkPlan T after a gate-approved step (NR0003 §8/§10).

0504 NR0003: a TR whose review `pass` approved it INSIDE the gate made the next WorkPlan T the
head only after the gate had already decided "work". The plain path's advance_workflow then
created that T as `pending_review` (its step has a review count) and refused to issue past it
(`head_in_progress`): the successor rolled back at admission, the TR's review run was recorded
as `hop_handoff_failed` with no reason, and the T's review hop was never decided. The same
happened when a chain was STARTED on such a head (S0/S1), and a question park's resume shared
the gap. A chain that simply reached its target was also recorded as `hop_handoff_failed`.

The World below is the 0414 gate World (document rows, review rows, the sequence) -- the real
gate derivation, the real `run_review_gate` loop and the real chain helpers run against it.
Only the WorkPlan materializer is a double: it does what the production one does at this
boundary (create the step's T as `pending_review` once the head reaches it), so every
assertion is about what the gate and the chain then DO with that document.

  F1  run_review_gate / the question-park resume derive the gate again after a settle
  F2  start_chain_at_instruction_review starts a chain at the head T's review hop;
      a failed or raced start takes back only its own queued intent (TR0005 rev1)
  F3  a refused advance parks on `advance_blocked` with its reason; the ai_invoke channel
      reaches the app's file log
  F4  a chain completed by the gate is recorded `chain_completed`, never re-parked
"""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

import test_ai_invoke_restart_count_0443 as route_harness
import test_ai_invoke_review_gate_0414 as g
from test_ai_invoke_restart_count_0443 import client  # noqa: F401 -- fixture
from test_ai_invoke_review_gate_0414 import world  # noqa: F401 -- fixture

from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import chain as chain_module
from modules.flow_gate.services.ai_invoke import finalize, review, runtime, terminal

GROUP = g.GROUP
SPINE = g.SPINE
TR_DOC = "flowgate.default.0414.0005-TR"
T_DOC = "flowgate.default.0414.0006-T"
NEXT_TR_DOC = "flowgate.default.0414.0007-TR"

# Item 5 is the TR, item 6 the WorkPlan T after it -- both reviewed twice (v02.0053's shape).
COUNTS = {"5": 2, "6": 2}


def _bundle(**overrides):
    base = g.bundle(review_count_overrides=dict(COUNTS),
                    reviewer_overrides={"5": "aip_rev", "6": "aip_rev"},
                    materialize_instruction_before_gate=True)
    base.update(overrides)
    return base


def _approve(world, doc_id):
    world.docs[doc_id]["doc_review_status"] = "approved"
    for row in world.items:
        if row.get("result_doc_id") == doc_id:
            row["result_doc_review_status"] = "approved"


@pytest.fixture
def wp(monkeypatch, world):
    """The WorkPlan boundary: the T after the TR appears only once the TR is approved."""
    seen = {"materialize": [], "settled": [], "review": [], "rework": [], "work": [],
            "queued": [], "parked": [], "t_status": "pending_review"}

    def materialize(bundle):
        seen["materialize"].append(dict(bundle))
        tr = world.docs.get(TR_DOC) or {}
        if tr.get("doc_review_status") == "approved" and T_DOC not in world.docs:
            # count 0 auto-approves in the same breath (documents.materialize_work_plan_
            # instruction); a reviewed step is submitted for review.
            world.fill(6, T_DOC, status=seen["t_status"])
        return []

    def settle(group_id, slot, bundle, run):
        seen["settled"].append(slot["doc_id"])
        _approve(world, slot["doc_id"])
        return "continue"

    monkeypatch.setattr(review, "_materialize_work_plan_instruction_before_gate", materialize)
    monkeypatch.setattr(svc, "_settle_gate_pass", settle)
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda run, bundle, stop_code: seen["parked"].append(stop_code))
    monkeypatch.setattr(svc, "request_auto_resume",
                        lambda group_id, payload: seen["queued"].append(dict(payload)))
    monkeypatch.setattr(svc, "clear_auto_resume", lambda group_id: seen["queued"].clear())
    monkeypatch.setattr(svc, "_spawn_review_hop",
                        lambda grp, b, gate: seen["review"].append((dict(b), gate))
                        or {"ok": True, "run_id": "run_review_t"})
    monkeypatch.setattr(svc, "_spawn_rework_hop",
                        lambda grp, b, gate: seen["rework"].append((dict(b), gate))
                        or {"ok": True, "run_id": "run_rework_t"})
    monkeypatch.setattr(svc, "_spawn_auto_resume",
                        lambda grp, b: seen["work"].append(dict(b)))
    monkeypatch.setattr(chain_module, "park_for_server_test_run",
                        lambda group_id, bundle, run, **kw: False)
    return seen


def _tr_review_hop_bundle(**overrides):
    """What the TR's second review hop queued: its own progress markers ride along."""
    return _bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1,
                   last_stop_code="hop_handoff", **overrides)


# ── F1 · run_review_gate ─────────────────────────────────────────────────────────────

class TestGateAfterSettledStep:
    def test_1_tr_pass_exposes_the_reviewed_t_and_launches_its_review_hop(self, world, wp):
        """NR §10-1: TR(count 2) `pass` → next head WP T(count 2) gets ITS review hop."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        assert svc.run_review_gate(GROUP, _tr_review_hop_bundle(), g._run()) is True

        assert wp["settled"] == [TR_DOC]
        assert world.docs[T_DOC]["doc_review_status"] == "pending_review", (
            "the T waits for its review, it is not approved on the way past")
        assert wp["work"] == [], "no plain advance (it is what died on head_in_progress)"
        assert wp["parked"] == [], "no hop_handoff_failed / review_no_verdict park"
        assert len(wp["review"]) == 1
        queued, gate = wp["review"][0]
        assert gate["slot"]["doc_id"] == T_DOC and gate["slot"]["item_seq"] == 6
        # Queue first, then launch (L0008 §2.4), with the T's OWN progress, judged cold:
        # the TR's rounds_before=1 must not read as the T's "review left nothing".
        assert wp["queued"] and wp["queued"][-1]["last_stage"] == svc.REVIEW_HOP_KIND
        assert queued["rounds_before"] == 0
        assert queued["last_stage"] == svc.REVIEW_HOP_KIND
        assert "revision_before" not in queued and "last_stop_code" not in queued
        assert queued["target_seq"] == 9 and queued["review_count_overrides"] == COUNTS
        # The materializer ran twice: before the gate (head = TR, nothing) and after the settle.
        assert len(wp["materialize"]) == 2

    def test_2_spent_budget_approval_exposes_the_reviewed_t_too(self, world, wp):
        """NR §10-2: the last rework of a finite budget approves the TR (approve_first)."""
        world.fill(5, TR_DOC, status="rejected")
        world.review(TR_DOC, "issues", revision_no=0)
        world.rework(TR_DOC, 1)
        bundle = _bundle(review_count_overrides={"5": 1, "6": 2},
                         last_stage=svc.REWORK_HOP_KIND, revision_before=0)
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert wp["settled"] == [TR_DOC]
        assert wp["work"] == [] and wp["parked"] == []
        assert [gate["slot"]["doc_id"] for _b, gate in wp["review"]] == [T_DOC]
        assert "revision_before" not in wp["review"][0][0]

    def test_3_t_pass_approves_the_t_and_starts_the_next_work_hop(self, world, wp):
        """NR §10-3: the T's own `pass` approves it and the ordinary next work hop starts."""
        world.fill(5, TR_DOC, status="approved")
        world.fill(6, T_DOC).review(T_DOC, "pass")
        bundle = _bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=0,
                         chain_docs_reached=3)
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert wp["settled"] == [T_DOC]
        assert wp["review"] == [] and wp["parked"] == []
        assert len(wp["work"]) == 1
        work = wp["work"][0]
        assert work["last_stage"] == svc.WORK_HOP_KIND
        assert work["target_seq"] == 9 and work["chain_docs_reached"] == 3

    def test_4a_t_issues_rejects_and_launches_the_rework(self, world, wp, monkeypatch):
        """NR §10-4: T `issues` → T rework hop (with its automatic rejection first)."""
        rejected = []
        monkeypatch.setattr(svc, "_auto_reject",
                            lambda slot, rv, b: rejected.append(slot["doc_id"]) or {"ok": True})
        world.fill(5, TR_DOC, status="approved")
        world.fill(6, T_DOC).review(T_DOC, "issues")
        bundle = _bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=0)
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert rejected == [T_DOC]
        assert [gate["slot"]["doc_id"] for _b, gate in wp["rework"]] == [T_DOC]
        assert wp["work"] == []

    def test_4b_t_hold_still_stops_for_a_human(self, world, wp):
        """NR §10-4: `hold` keeps parking on review_verdict_hold (preserved stop)."""
        world.fill(5, TR_DOC, status="approved")
        world.fill(6, T_DOC).review(T_DOC, "hold")
        bundle = _bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=0)
        assert svc.run_review_gate(GROUP, bundle, g._run()) is False
        assert wp["parked"] == [svc.REVIEW_VERDICT_HOLD_STOP_CODE]
        assert wp["review"] == [] and wp["work"] == []

    def test_4c_a_hold_on_the_exposed_t_parks_on_the_t(self, world, wp, monkeypatch):
        """A stop the re-derived gate reaches parks the chain, like any gate stop."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")

        def materialize(bundle):
            if world.docs[TR_DOC]["doc_review_status"] == "approved" and T_DOC not in world.docs:
                world.fill(6, T_DOC)
                world.review(T_DOC, "hold")
            return []

        monkeypatch.setattr(review, "_materialize_work_plan_instruction_before_gate", materialize)
        assert svc.run_review_gate(GROUP, _tr_review_hop_bundle(), g._run()) is False
        assert wp["parked"] == [svc.REVIEW_VERDICT_HOLD_STOP_CODE]
        assert wp["work"] == [] and wp["review"] == []

    def test_6_count_zero_t_keeps_the_control_group_path(self, world, wp):
        """NR §10-6: a T without review auto-approves when materialized; the plain hop runs.
        (The real path: _auto_complete_instruction_heads carries it inside advance_workflow.)"""
        wp["t_status"] = "approved"
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        bundle = _tr_review_hop_bundle(review_count_overrides={"5": 2},
                                       reviewer_overrides={"5": "aip_rev"})
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert wp["settled"] == [TR_DOC]
        assert wp["review"] == [] and wp["parked"] == []
        assert len(wp["work"]) == 1
        # The plain hop gets the hand-off's own bundle, unchanged (event order invariant).
        assert wp["work"][0]["rounds_before"] == 1

    def test_6b_no_materialize_flag_means_a_single_derivation(self, world, wp):
        """A direct gate call (no chain hand-off flag) keeps exactly one derivation."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        bundle = _tr_review_hop_bundle(materialize_instruction_before_gate=False)
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert len(wp["materialize"]) == 1
        assert len(wp["work"]) == 1 and wp["review"] == []

    def test_7_target_reached_does_not_materialize_the_next_t(self, world, wp, monkeypatch):
        """NR §10-7: a completed chain ends; nothing after the target is created."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        monkeypatch.setattr(svc, "_settle_gate_pass",
                            lambda grp, slot, b, run: wp["settled"].append(slot["doc_id"])
                            or "completed")
        assert svc.run_review_gate(GROUP, _tr_review_hop_bundle(target_seq=5), g._run()) is False
        assert T_DOC not in world.docs
        assert len(wp["materialize"]) == 1
        assert wp["review"] == [] and wp["work"] == []

    def test_8_a_user_pause_at_the_boundary_stops_before_the_t(self, world, wp, monkeypatch):
        """NR §10-8: settle answers `paused` → no T review hop."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        monkeypatch.setattr(svc, "_settle_gate_pass", lambda grp, slot, b, run: "user_paused")
        assert svc.run_review_gate(GROUP, _tr_review_hop_bundle(), g._run()) is False
        assert wp["review"] == [] and wp["work"] == []
        assert len(wp["materialize"]) == 1

    def test_10_tsr_head_still_parks_on_its_server_run(self, world, wp, monkeypatch):
        """NR §10-10: with nothing new to review, the TSR hand-off is unchanged."""
        parked = []
        monkeypatch.setattr(chain_module, "park_for_server_test_run",
                            lambda group_id, bundle, run, **kw: parked.append(group_id) or True)
        wp["t_status"] = "approved"
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        assert svc.run_review_gate(GROUP, _tr_review_hop_bundle(), g._run()) is False
        assert parked == [GROUP]
        assert wp["work"] == [] and wp["review"] == []

    def test_the_re_derivation_happens_at_most_once(self, world, wp, monkeypatch):
        """Two passes at most: the exposed slot's own approval goes to the plain path."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        # The T's count is overridden to 0 for this run: the exposed T settles at once.
        bundle = _tr_review_hop_bundle(review_count_overrides={"5": 2, "6": 0})
        assert svc.run_review_gate(GROUP, bundle, g._run()) is True
        assert wp["settled"] == [TR_DOC, T_DOC]
        assert len(wp["materialize"]) == 2
        assert len(wp["work"]) == 1 and wp["review"] == []


# ── F1 · the question-park resume ───────────────────────────────────────────────────

class TestQuestionParkResume:
    def test_9_resume_settle_exposes_the_reviewed_t(self, world, wp):
        """NR §10-9: the resume's own approval leads to the T's review, not the plain path."""
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        bundle = _bundle()
        gate = svc.resolve_review_gate(bundle)
        assert gate["stage"] == svc.WORK_HOP_KIND and gate["approve_first"]
        _approve(world, TR_DOC)          # what _settle_question_park_gate just did
        restored = []
        next_gate, settled = chain_module._question_park_gate_after_settle(
            GROUP, gate, bundle, lambda stop_code=None: restored.append(stop_code))
        assert settled is None and restored == []
        assert next_gate["stage"] == svc.REVIEW_HOP_KIND
        assert next_gate["slot"]["doc_id"] == T_DOC

    def test_9b_nothing_new_keeps_the_replayed_gate(self, world, wp):
        wp["t_status"] = "approved"
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        bundle = _bundle()
        gate = svc.resolve_review_gate(bundle)
        _approve(world, TR_DOC)
        next_gate, settled = chain_module._question_park_gate_after_settle(
            GROUP, gate, bundle, lambda stop_code=None: None)
        assert next_gate is gate and settled is None

    def test_9c_a_stop_on_the_exposed_t_restores_the_row_on_that_stop(self, world, wp,
                                                                       monkeypatch):
        world.fill(5, TR_DOC).review(TR_DOC, "issues").review(TR_DOC, "pass")
        bundle = _bundle()
        gate = svc.resolve_review_gate(bundle)
        _approve(world, TR_DOC)
        world.fill(6, T_DOC).review(T_DOC, "hold")
        restored = []
        with pytest.raises(HTTPException) as caught:
            chain_module._question_park_gate_after_settle(
                GROUP, gate, bundle, lambda stop_code=None: restored.append(stop_code))
        assert caught.value.status_code == 409
        assert restored == [svc.REVIEW_VERDICT_HOLD_STOP_CODE]

    def test_9d_a_review_or_work_gate_is_not_re_derived(self, world, wp):
        world.fill(5, TR_DOC)
        gate = svc.resolve_review_gate(_bundle())
        assert gate["stage"] == svc.REVIEW_HOP_KIND
        next_gate, settled = chain_module._question_park_gate_after_settle(
            GROUP, gate, _bundle(), lambda stop_code=None: None)
        assert next_gate is gate and settled is None and wp["materialize"] == []


# ── F2 · starting a chain on a reviewed WorkPlan T ─────────────────────────────────────

@pytest.fixture
def start_env(monkeypatch, world, wp):
    """Head = item 6 (the T). The materializer creates it pending_review on first sight."""
    _approve_until = 5
    for row in world.items:
        if row["item_seq"] <= _approve_until:
            doc_id = f"flowgate.default.0414.{row['item_seq']:04d}-{row['type']}"
            world.fill(row["item_seq"], doc_id, status="approved")

    def head(seq_id):
        for row in sorted(world.items, key=lambda r: r["item_seq"]):
            if row.get("result_doc_id") is None or row.get("result_doc_review_status") != "approved":
                return dict(row)
        return None

    def materialize(bundle):
        wp["materialize"].append(dict(bundle))
        if T_DOC not in world.docs:
            world.fill(6, T_DOC, status=wp["t_status"])
        return []

    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", head)
    monkeypatch.setattr(review, "_materialize_work_plan_instruction_before_gate", materialize)
    monkeypatch.setattr(svc, "_active_run_for_group", lambda group_id: None)
    monkeypatch.setattr(chain_module.db_group_ai_leases, "get_active", lambda group_id: None)
    monkeypatch.setattr(db_paused, "get_by_group", lambda group_id: None)
    monkeypatch.setattr(chain_module.admission, "_continuation_docs_target",
                        lambda doc_ref, target, **kw: 3)
    return wp


def _start(**overrides):
    kwargs = dict(project_id="flowgate", group_id=GROUP, doc_ref=SPINE, target_seq=9,
                  issued_to=g.USER, api_base_url=g.API_BASE, locale="ko",
                  instruction_mode="auto_approved", auto_approve_item_seqs=[],
                  review_count_overrides=dict(COUNTS),
                  reviewer_overrides={"6": "aip_rev"},
                  provider_overrides={"7": "aip_step5"}, base_provider_id="aip_default",
                  provider_pinned=True, note_overrides=None, default_note="be terse",
                  step_timeout_sec=3600, restart_max_attempts=2)
    kwargs.update(overrides)
    return svc.start_chain_at_instruction_review(**kwargs)


class TestChainStartOnReviewedInstruction:
    def test_11_start_launches_the_t_review_hop_instead_of_rolling_back(self, world, start_env):
        """NR §10-11 (S0/S1): the start reaches the T's review hop."""
        result = _start()
        assert result == {"ok": True, "run_id": "run_review_t"}
        assert world.docs[T_DOC]["doc_review_status"] == "pending_review"
        assert len(start_env["review"]) == 1 and start_env["work"] == []
        queued, gate = start_env["review"][0]
        assert gate["slot"]["doc_id"] == T_DOC
        assert start_env["queued"] and start_env["queued"][-1]["last_stage"] == "review"
        # Everything the hand-off after the T's verdict reads is in the queued bundle.
        assert queued["target_seq"] == 9 and queued["to_end"] is False
        assert queued["chain_docs_target"] == 3 and queued["chain_docs_reached"] == 0
        assert queued["provider_overrides"] == {"7": "aip_step5"}
        assert queued["base_provider_id"] == "aip_default" and queued["provider_pinned"] is True
        assert queued["default_note"] == "be terse" and queued["restart_max_attempts"] == 2
        assert queued["step_timeout_sec"] == 3600
        assert queued["materialize_instruction_before_gate"] is True
        assert queued["rounds_before"] == 0

    def test_11b_a_t_already_pending_review_is_reviewed_too(self, world, start_env):
        """The state the incident left (0014-T pending_review): starting again reviews it."""
        world.fill(6, T_DOC)
        assert _start() is not None
        assert [gate["slot"]["doc_id"] for _b, gate in start_env["review"]] == [T_DOC]

    def test_11c_an_unreviewed_t_keeps_the_ordinary_first_hop(self, world, start_env):
        start_env["t_status"] = "approved"
        assert _start(review_count_overrides={"6": 0}) is None
        assert start_env["review"] == [] and start_env["queued"] == []

    def test_11d_a_report_head_is_not_touched(self, world, start_env):
        world.fill(6, T_DOC, status="approved")
        assert _start() is None
        assert start_env["materialize"] == [] and start_env["review"] == []

    def test_11e_a_live_owner_is_left_to_ordinary_admission(self, world, start_env, monkeypatch):
        monkeypatch.setattr(chain_module.db_group_ai_leases, "get_active",
                            lambda group_id: {"run_id": "run_other"})
        assert _start() is None
        assert start_env["materialize"] == [] and T_DOC not in world.docs

    def test_11g_a_probe_that_cannot_read_falls_back(self, world, start_env, monkeypatch):
        def broken(doc_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", broken)
        assert _start() is None
        assert start_env["materialize"] == []


class PausedRows:
    """ai_invoke_paused_chains, in memory: the same upsert/read/CAS-delete contract.

    release_owned mirrors the real tri-state (row / None / ReleaseSuperseded) and
    delete_system_stop the real "system row of THAT stop run only" predicate.
    """

    def __init__(self):
        self.rows = {}

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row is not None else None

    def upsert(self, *, group_id, **columns):
        self.rows[group_id] = {"group_id": group_id, **columns}

    def delete_system_stop(self, group_id, stop_run_id):
        row = self.rows.get(group_id)
        if (stop_run_id and row is not None and (row.get("stop_kind") or "user") == "system"
                and row.get("stop_run_id") == stop_run_id):
            self.rows.pop(group_id)

    def release_owned(self, group_id, *, paused_by, paused_at, stop_kind, stop_run_id):
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        row = self.rows.get(group_id)
        if row is None:
            return None
        if (
            row.get("paused_by") != paused_by
            or row.get("paused_at") != paused_at
            or (row.get("stop_kind") or "user") != (stop_kind or "user")
            or row.get("stop_run_id") != stop_run_id
        ):
            return db_paused.ReleaseSuperseded(dict(row))
        return self.rows.pop(group_id)


@pytest.fixture
def durable_start(monkeypatch, start_env):
    """start_env with the REAL queue: request_auto_resume writes the memory queue AND its
    durable hop_handoff row (stop_run_id NULL -- no run of the start's own exists yet)."""
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    store = PausedRows()
    for name in ("get_by_group", "upsert", "delete_system_stop", "release_owned"):
        monkeypatch.setattr(db_paused, name, getattr(store, name))
    monkeypatch.setattr(svc, "request_auto_resume", chain_module.request_auto_resume)
    monkeypatch.setattr(svc, "clear_auto_resume", chain_module.clear_auto_resume)
    monkeypatch.setattr(svc, "_write_handoff_row", chain_module._write_handoff_row)
    svc._auto_resume.pop(GROUP, None)
    start_env["store"] = store
    yield start_env
    svc._auto_resume.pop(GROUP, None)


def _paused_row(group_id):
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    return db_paused.get_by_group(group_id)


def _no_enabled_provider(seen):
    def boom(grp, b, gate):
        seen.append({"queue": svc.peek_auto_resume(grp), "row": _paused_row(grp)})
        raise HTTPException(status_code=409, detail={"code": "no_enabled_provider"})
    return boom


def _pending_for_other_owner():
    return {"doc_ref": SPINE, "target_seq": 9, "review_mode": False,
            "instruction_mode": "auto_approved", "auto_approve_item_seqs": [],
            "locale": "ko", "issued_to": "someone-else", "api_base_url": g.API_BASE}


class TestChainStartIntentOwnership:
    """0504 TR0005 rev1: a failed or raced chain start owns exactly its own intent."""

    def test_11f_a_failed_launch_takes_both_halves_of_its_intent_back(
            self, world, durable_start, monkeypatch):
        seen = []
        monkeypatch.setattr(svc, "_spawn_review_hop", _no_enabled_provider(seen))
        with pytest.raises(HTTPException):
            _start()
        # Queue first, then launch: both halves existed when the launch was attempted ...
        assert seen[0]["queue"]["last_stage"] == svc.REVIEW_HOP_KIND
        row = seen[0]["row"]
        assert row["stop_kind"] == "system" and row["stop_code"] == svc.HOP_HANDOFF_STOP_CODE
        assert row["stop_run_id"] is None and row["chain_id"] is None
        # ... and neither outlives the failed launch. The NULL stop_run_id is exactly what
        # the stop-run-keyed cleanup (_clear_handoff_row / delete_system_stop) never matches.
        assert svc.peek_auto_resume(GROUP) is None
        assert durable_start["store"].rows == {}

    def test_11h_a_retry_after_a_failed_launch_reaches_the_review_hop(
            self, world, durable_start, monkeypatch):
        """rev0 left the row behind: the retry's paused-row probe fell back to the ordinary
        first hop, which dies on head_in_progress at the pending_review T."""
        monkeypatch.setattr(svc, "_spawn_review_hop", _no_enabled_provider([]))
        with pytest.raises(HTTPException):
            _start()
        launched = []
        monkeypatch.setattr(svc, "_spawn_review_hop",
                            lambda grp, b, gate: launched.append((dict(b), gate))
                            or {"ok": True, "run_id": "run_review_t"})
        assert _start() == {"ok": True, "run_id": "run_review_t"}, (
            "the retry starts the T's review hop, not the ordinary first hop")
        queued, gate = launched[0]
        assert gate["slot"]["doc_id"] == T_DOC
        assert svc.peek_auto_resume(GROUP)[chain_module.START_INTENT_KEY] == (
            queued[chain_module.START_INTENT_KEY])
        assert durable_start["store"].rows[GROUP]["stop_code"] == svc.HOP_HANDOFF_STOP_CODE

    def test_11i_a_failed_launch_leaves_another_owners_intent_alone(
            self, world, durable_start, monkeypatch):
        store = durable_start["store"]
        other = {**_pending_for_other_owner(), "last_stage": svc.REVIEW_HOP_KIND}

        def boom(grp, b, gate):
            # Meanwhile another owner queued its own intent and a person paused the group.
            with runtime._auto_resume_lock:
                svc._auto_resume[grp] = other
            store.upsert(group_id=grp, doc_ref=SPINE, paused_by=g.USER,
                         paused_at="2026-10-11T18:00:00+09:00", stop_kind="user",
                         stop_code="paused", stop_run_id="run_other")
            raise HTTPException(status_code=409, detail={"code": "run_in_progress"})

        monkeypatch.setattr(svc, "_spawn_review_hop", boom)
        with pytest.raises(HTTPException):
            _start()
        assert svc.peek_auto_resume(GROUP) is other
        assert store.rows[GROUP]["stop_kind"] == "user"
        assert store.rows[GROUP]["stop_run_id"] == "run_other"

    def test_11j_concurrent_starts_keep_the_winners_intent_and_the_chain_continues(
            self, world, durable_start, monkeypatch):
        """Two starts at once: one launches, the other never reaches the queue, so the
        winner's T verdict still hands off to the next work hop (NR §9 queue identity)."""
        import threading
        import time

        probed = []
        both_probed = threading.Event()

        def docs_target(doc_ref, target, **kw):
            # The last read of the probe. rev0 let both starts reach it before either
            # queued; under the group lock the second start never gets this far.
            probed.append(threading.current_thread().name)
            if len(probed) == 2:
                both_probed.set()
            both_probed.wait(0.5)
            return 3

        state = {"active": None, "lease": None}
        launched = []
        lease_guard = threading.Lock()

        def spawn(grp, b, gate):
            with lease_guard:                  # admission's atomic lease acquisition
                if state["lease"] is not None:
                    raise HTTPException(status_code=409, detail={"code": "run_in_progress"})
                state["lease"] = {"run_id": "run_review_t"}
            launched.append(dict(b))
            time.sleep(0.2)                    # the window the losing start used to race in
            state["active"] = {"run_id": "run_review_t", "group_id": grp}
            return {"ok": True, "run_id": "run_review_t"}

        monkeypatch.setattr(chain_module.admission, "_continuation_docs_target", docs_target)
        monkeypatch.setattr(svc, "_active_run_for_group", lambda grp: state["active"])
        monkeypatch.setattr(chain_module.db_group_ai_leases, "get_active",
                            lambda grp: state["lease"])
        monkeypatch.setattr(svc, "_spawn_review_hop", spawn)

        results, errors = {}, []
        together = threading.Barrier(2)

        def start(name):
            try:
                together.wait(5)
                results[name] = _start()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=start, args=(n,), name=n) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert errors == [], "the losing start is left to the ordinary path, not refused here"
        assert sorted(results.values(), key=lambda r: r is None) == [
            {"ok": True, "run_id": "run_review_t"}, None]
        assert len(probed) == 1 and len(launched) == 1, "the probes never overlapped"
        queued = svc.peek_auto_resume(GROUP)
        assert queued is not None, "the winner's queue survived the other start"
        assert queued[chain_module.START_INTENT_KEY] == (
            launched[0][chain_module.START_INTENT_KEY])
        assert durable_start["store"].rows[GROUP]["stop_code"] == svc.HOP_HANDOFF_STOP_CODE

        # The winner's review hop says `pass`: the queued intent is what
        # _maybe_auto_resume_hop hands the gate; the T is approved, the next work hop starts.
        world.review(T_DOC, "pass")
        assert svc.run_review_gate(GROUP, dict(queued), g._run()) is True
        assert durable_start["settled"] == [T_DOC]
        assert durable_start["parked"] == [] and durable_start["review"] == []
        assert len(durable_start["work"]) == 1
        assert durable_start["work"][0]["last_stage"] == svc.WORK_HOP_KIND
        assert durable_start["work"][0]["target_seq"] == 9


class TestStartRouteWiring:
    """The real POST /api/v1/ai-invoke/start route hands the continuous first hop to F2."""

    @pytest.fixture
    def routed(self, monkeypatch):
        from modules.flow_gate.api.v1 import ai_invoke_routes

        seen = {"helper": [], "start_run": []}

        def start_run(**kw):
            seen["start_run"].append(kw)
            return {"run_id": "aiv_ordinary", "status": "running"}

        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
        return seen

    def _helper(self, monkeypatch, seen, result=None, exc=None):
        from modules.flow_gate.api.v1 import ai_invoke_routes

        def helper(**kw):
            seen["helper"].append(kw)
            if exc is not None:
                raise exc
            return result

        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service,
                            "start_chain_at_instruction_review", helper)

    def test_a_started_review_hop_is_the_response(self, client, monkeypatch, routed):
        self._helper(monkeypatch, routed, result={"ok": True, "run_id": "aiv_review_t"})
        resp = route_harness._post_start(
            client, continuation_restart_max_attempts=2, continuation_step_timeout_sec=3600,
            continuation_default_note="be terse", provider_id="aip_x", provider_pinned=True)
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_id"] == "aiv_review_t"
        assert routed["start_run"] == [], "the ordinary first hop is not started as well"
        picks = routed["helper"][0]
        assert picks["doc_ref"] == route_harness.ROOT_DOC and picks["target_seq"] == 2
        assert picks["group_id"] == "flowgate.default.0443"
        assert picks["restart_max_attempts"] == 2 and picks["step_timeout_sec"] == 3600
        assert picks["default_note"] == "be terse"
        assert picks["base_provider_id"] == "aip_x" and picks["provider_pinned"] is True

    def test_nothing_waiting_starts_the_ordinary_first_hop(self, client, monkeypatch, routed):
        self._helper(monkeypatch, routed, result=None)
        resp = route_harness._post_start(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_id"] == "aiv_ordinary"
        assert len(routed["helper"]) == 1 and len(routed["start_run"]) == 1

    def test_a_refused_start_keeps_its_code(self, client, monkeypatch, routed):
        self._helper(monkeypatch, routed, exc=HTTPException(
            status_code=409, detail={"code": "no_enabled_provider", "message": "none"}))
        resp = route_harness._post_start(client)
        assert resp.status_code == 409
        assert resp.json()["code"] == "no_enabled_provider"
        assert routed["start_run"] == []

    def test_a_materializer_refusal_is_a_409_conflict(self, client, monkeypatch, routed):
        self._helper(monkeypatch, routed,
                     exc=ValueError("instruction_auto_complete_failed:T:broken"))
        resp = route_harness._post_start(client)
        assert resp.status_code == 409
        assert resp.json()["code"] == "workflow_decision_conflict"

    def test_review_mode_never_asks(self, client, monkeypatch, routed):
        self._helper(monkeypatch, routed, result={"ok": True, "run_id": "aiv_review_t"})
        resp = route_harness._post_start(client, continuation_review_mode=True)
        assert resp.status_code == 200, resp.text
        assert routed["helper"] == [] and len(routed["start_run"]) == 1


# ── F3 · a refused advance is recorded, and the channel reaches the file log ────────────

@pytest.fixture
def handoff_env(monkeypatch):
    seen = {"parked": []}
    queued = {"doc_ref": SPINE, "target_seq": 9, "issued_to": g.USER,
              "api_base_url": g.API_BASE, "locale": "ko"}
    monkeypatch.setattr(svc, "_auto_resume", {GROUP: queued})
    monkeypatch.setattr(svc, "_write_handoff_row", lambda *a, **kw: True)
    monkeypatch.setattr(chain_module, "_park_on_own_open_question", lambda run, pending: False)
    monkeypatch.setattr(svc, "_clear_handoff_row", lambda *a, **kw: True)

    def park(run, pending, stop_code):
        seen["parked"].append(stop_code)
        run["stop_code"] = stop_code
        run["stop_reason"] = finalize._stop_reason_text(stop_code, run)

    monkeypatch.setattr(svc, "_park_handoff", park)
    return seen


def _hop_run(**overrides):
    """A finished review hop's run: single-mode, so it carries no continuation target."""
    return g._run(mode="single", hop_kind=svc.REVIEW_HOP_KIND, stop_code="hop_handoff",
                  status="running", end_reason="exited", continuation_target_seq=None,
                  **overrides)


class TestHandoffFailureRecord:
    def test_12_a_head_in_progress_refusal_parks_advance_blocked_with_its_reason(
            self, monkeypatch, handoff_env):
        def gate(group_id, pending, run):
            raise ValueError("head_in_progress:T:T#6")

        monkeypatch.setattr(review, "run_review_gate", gate)
        run = _hop_run()
        svc._maybe_auto_resume_hop(run)
        assert handoff_env["parked"] == ["advance_blocked"]
        assert run["review_reject_detail"] == "head_in_progress:T:T#6"
        assert run["stop_reason"] == (
            "Could not advance to the next step: head_in_progress:T:T#6")
        assert not run.get("_handoff_succeeded")

    def test_12b_an_unexpected_failure_keeps_its_code_but_gains_a_reason(
            self, monkeypatch, handoff_env):
        def gate(group_id, pending, run):
            raise RuntimeError("lease table unavailable")

        monkeypatch.setattr(review, "run_review_gate", gate)
        run = _hop_run()
        svc._maybe_auto_resume_hop(run)
        assert handoff_env["parked"] == [svc.HOP_HANDOFF_FAILED_STOP_CODE]
        assert run["stop_reason"] == (
            "The next hop could not be started: RuntimeError: lease table unavailable")

    def test_12c_a_refused_admission_names_its_code(self, monkeypatch, handoff_env):
        def gate(group_id, pending, run):
            raise HTTPException(status_code=409, detail={
                "code": "run_lease_lost", "message": "The AI run lease could not be activated."})

        monkeypatch.setattr(review, "run_review_gate", gate)
        run = _hop_run()
        svc._maybe_auto_resume_hop(run)
        assert handoff_env["parked"] == [svc.HOP_HANDOFF_FAILED_STOP_CODE]
        assert "run_lease_lost" in run["stop_reason"]

    def test_hop_handoff_failed_without_detail_keeps_its_old_reason(self):
        assert finalize._stop_reason_text(svc.HOP_HANDOFF_FAILED_STOP_CODE, {}) is None

    def test_the_channel_warnings_reach_the_app_file_log(self, tmp_path):
        assert any(isinstance(h, runtime._AppFileLogForwarder) for h in runtime.logger.handlers)
        file_handler = logging.FileHandler(tmp_path / "default.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        app_logger = logging.getLogger("LogAssist.log")
        app_logger.addHandler(file_handler)
        try:
            runtime.logger.warning("0504 file-log probe %s", "warn")
            runtime.logger.info("0504 file-log probe %s", "info")
        finally:
            app_logger.removeHandler(file_handler)
            file_handler.close()
        written = (tmp_path / "default.log").read_text(encoding="utf-8")
        assert "0504 file-log probe warn" in written
        assert "0504 file-log probe info" not in written


# ── F4 · a chain the gate completed is recorded as completed ───────────────────────────

class TestChainCompletedRecord:
    def test_7b_the_gate_completion_marks_the_run(self, monkeypatch):
        from modules.flow_gate.api import inbox_routes

        released = []
        monkeypatch.setattr(inbox_routes, "settle_completed_step",
                            lambda **kw: {"outcome": "completed", "stop_code": "chain_completed"})
        monkeypatch.setattr(svc, "_clear_handoff_row", lambda *a, **kw: True)
        monkeypatch.setattr(review.db_group_ai_leases, "release",
                            lambda group_id, run_id, reason=None: released.append(reason))
        run = _hop_run()
        slot = {"doc_id": TR_DOC, "doc_type": "TR", "item_seq": 5}
        assert review._settle_gate_pass(GROUP, slot, _bundle(target_seq=5), run) == "completed"
        assert run["_chain_completed"] is True
        assert released == ["chain_completed"]

    def test_7c_completion_is_recorded_and_never_re_parked(self, monkeypatch, handoff_env):
        def gate(group_id, pending, run):
            run["_chain_completed"] = True       # what _settle_gate_pass marks
            return False

        monkeypatch.setattr(review, "run_review_gate", gate)
        run = _hop_run()
        svc._maybe_auto_resume_hop(run)
        assert run["stop_code"] == "chain_completed"
        assert run["stop_reason"] == "The chain reached its target step; the chain is complete."
        assert handoff_env["parked"] == []
        # The worker's terminal step must not turn it into hop_handoff_failed afterwards.
        assert terminal.claim_abandon(run) is False
        terminal.abandon_handoff(run, stop_code=svc.HOP_HANDOFF_FAILED_STOP_CODE)
        assert handoff_env["parked"] == []
        assert run["stop_code"] == "chain_completed"
        assert not run.get("_terminal_abandoned")

    def test_a_parked_gate_still_leaves_its_own_record(self, monkeypatch, handoff_env):
        """Only a COMPLETED gate is relabelled; a parked one keeps what the park wrote."""
        def gate(group_id, pending, run):
            run["stop_code"] = svc.REVIEW_VERDICT_HOLD_STOP_CODE
            return False

        monkeypatch.setattr(review, "run_review_gate", gate)
        run = _hop_run()
        svc._maybe_auto_resume_hop(run)
        assert run["stop_code"] == svc.REVIEW_VERDICT_HOLD_STOP_CODE
        assert terminal.claim_abandon(run) is True
