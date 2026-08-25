"""flowgate.default.0459 T0005 — 리비전 인식 리뷰 게이트 · 완료 기반 system stop exact-delete.

The two defects 0459 NR0003 traced, and nothing else. Each NR regression item owns one
class, so a failure names its own cause:

  * NR1 ``TestNr1RevisionAwareGateReachesCompleted`` — rev0 `issues` → rework → rev1
    `revised` → rev1 `pass` settles ``revised + approve -> approved -> completed`` and
    leaves no paused row. Driven through the real ``run_review_gate`` →
    ``_settle_gate_pass`` → ``inbox_routes.settle_completed_step`` chain with the review
    transition judged by the production ``transition_rules`` table, so the assertion is
    "the single writer was asked to reject / approve", not "the gate dict looked right".
  * NR2 ``TestNr2WfDoneClearsNullHopSystemStop`` — a ``mode='single'`` review hop's stop
    (``hop_item_seq`` NULL) against an R/B root at ``wf_done``.
  * NR3 ``TestNr3StoredScopeCompletion`` — ``next_seq is None`` and
    ``next_seq > continuation_target_seq``, plus the negative: no sequence / unreadable
    sequence is NOT completion evidence.
  * NR4 ``TestNr4PreservationAndExactDeleteRace`` — outstanding work, user pauses, and a
    row that changed under the read all survive; the delete is exact, and the same test
    file pins the production predicate the fake copies.
  * NR5 ``TestNr5ItemSeqJudgementUnchanged`` — ``head_slot_mismatch`` / ``advance_blocked``
    rows that DO carry a ``hop_item_seq`` keep the original head judgement.

No database: the workflow sequence, the documents, the reviews and the paused store are
dict-backed doubles with the production contracts, and every verdict below is read off an
observable — a store row, a recorded call, a returned payload.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import users as db_users  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.workflow import pipeline_service  # noqa: E402
from modules.flow_gate.workflow import transition_rules  # noqa: E402
from modules.flow_gate.workflow.routers import workflow as wf_router  # noqa: E402

GROUP = "flowgate.default.0459"
SPINE = f"{GROUP}.0001-B"
TARGET_DOC = "flowgate.default.0459.0002-TR"
USER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"
API_BASE = "http://127.0.0.1:8089/flowgate/api/v1"
REVIEW_RUN = "aiv_20260824_000052"      # the single review hop that parked the chain
OTHER_RUN = "aiv_20260824_000099"


# ══════════════════════════════════════════════════════════════════════════════════════
# Doubles
# ══════════════════════════════════════════════════════════════════════════════════════

class PausedStore:
    """``ai_invoke_paused_chains`` as a dict, with the production delete predicates.

    ``delete_system_stop`` copies the real WHERE clause —
    ``group_id AND COALESCE(stop_kind,'user')='system' AND stop_run_id`` — so a
    group-only implementation of the cleanup cannot pass NR4's race tests.
    ``TestNr4PreservationAndExactDeleteRace.test_the_production_delete_is_the_same_predicate``
    pins that copy to the SQL the production function really issues.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.snapshot: list[dict] | None = None      # what list_by_user hands out, if forced
        self.deleted_system: list[tuple] = []
        self.deleted_groups: list[str] = []

    def put(self, **row):
        base = {
            "id": len(self.rows) + 1, "group_id": GROUP, "doc_ref": SPINE, "mode": "single",
            "paused_by": USER, "paused_at": "2026-08-25T08:36:24+09:00",
            "continuation_target_seq": None, "docs_target": 1, "docs_reached": 1,
            "chain_id": "aiv_20260824_000048", "chain_docs_target": 1,
            "chain_docs_reached": 1, "stop_kind": "system", "stop_code": "approve_failed",
            "stop_run_id": REVIEW_RUN, "stop_last_message_excerpt": None,
        }
        base.update(row)
        self.rows[base["group_id"]] = base
        return base

    def list_by_user(self, user_id):
        if self.snapshot is not None:
            return [dict(r) for r in self.snapshot]
        return [dict(r) for r in self.rows.values() if r.get("paused_by") == user_id]

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row else None

    def exists(self, group_id):
        return group_id in self.rows

    def delete_and_return(self, group_id):
        row = self.rows.pop(group_id, None)
        return dict(row) if row else None

    def delete_by_group(self, group_id):
        self.deleted_groups.append(group_id)
        self.rows.pop(group_id, None)

    def delete_system_stop(self, group_id, stop_run_id):
        self.deleted_system.append((group_id, stop_run_id))
        if not stop_run_id:
            return
        row = self.rows.get(group_id)
        if (row is not None
                and (row.get("stop_kind") or "user") == "system"
                and row.get("stop_run_id") == stop_run_id):
            self.rows.pop(group_id, None)

    def upsert(self, **kw):
        self.put(**{k: v for k, v in kw.items() if not k.startswith("continuation_")
                    or k == "continuation_target_seq"})


class Sequence:
    """The workflow sequence behind ``doc_ref``. ``present=False`` is "no sequence at all";
    ``explode=True`` is "the lookup failed" — the two the cleanup must NOT read as done."""

    def __init__(self, items=None, present=True, explode=False):
        self.items = items if items is not None else []
        self.present = present
        self.explode = explode

    def get_sequence_for_member_doc(self, doc_ref):
        if self.explode:
            raise RuntimeError("workflow sequence read failed")
        return {"id": 1} if self.present else None

    def get_sequence_items(self, seq_id):
        if self.explode:
            raise RuntimeError("workflow sequence read failed")
        return [dict(i) for i in self.items]


def _slot(item_seq, *, doc_id=None, status=None, type_code="TR"):
    return {"item_seq": item_seq, "type": type_code, "result_doc_id": doc_id,
            "result_doc_review_status": status}


@pytest.fixture
def cleanup_env(monkeypatch):
    """``active_all`` reduced to the one thing under test: the staleness cleanup."""
    store = PausedStore()
    seq = Sequence()
    runs: dict[str, dict] = {}
    wf_done: set[str] = set()

    for name in ("upsert", "get_by_group", "exists", "delete_and_return",
                 "delete_by_group", "delete_system_stop", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(store, name))
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc",
                        seq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", seq.get_sequence_items)
    monkeypatch.setattr(svc.db_docs, "group_root_wf_done", lambda gid: gid in wf_done)
    monkeypatch.setattr(db_runs, "get", lambda run_id: runs.get(run_id))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_open_q_doc_ids", lambda group_id: [])
    monkeypatch.setattr(svc, "_paused_row_resume_state", lambda project_id, row: {
        "resume_available": True, "resume_block_code": None,
        "resume_block_reason": None, "resume_provider_name": None,
    })
    return {"store": store, "seq": seq, "runs": runs, "wf_done": wf_done}


def _single_review_run(**overrides):
    """The run record 0459 NR0003 read off the live DB: a review hop is ``mode='single'``,
    and only continuous runs record a ``hop_item_seq`` — so this one has none."""
    run = {"run_id": REVIEW_RUN, "group_id": GROUP, "mode": "single", "status": "finished",
           "outcome": "complete", "end_reason": "exited", "stop_code": "hop_handoff",
           "hop_item_seq": None}
    run.update(overrides)
    return run


# ══════════════════════════════════════════════════════════════════════════════════════
# NR1 — the review gate reaches `completed` on a reworked revision
# ══════════════════════════════════════════════════════════════════════════════════════

class World:
    """Sequence + documents + reviews, the only facts the gate derives from."""

    def __init__(self):
        self.items = [
            _slot(1, doc_id="flowgate.default.0459.0001-T", status="approved", type_code="T"),
            _slot(2, type_code="TR"),
        ]
        self.docs: dict[str, dict] = {}
        self.reviews: dict[str, list[dict]] = {}

    def fill(self, item_seq, doc_id, *, status="pending_review", revision_no=0,
             doc_type="TR"):
        for row in self.items:
            if row["item_seq"] == item_seq:
                row["result_doc_id"] = doc_id
                row["result_doc_review_status"] = status
        self.docs[doc_id] = {"doc_id": doc_id, "id": 42, "type_code": doc_type,
                             "group_id": GROUP, "branch": "main",
                             "doc_review_status": status, "revision_no": revision_no}
        return self

    def review(self, doc_id, verdict, *, revision_no=0, findings=None, comment=None):
        self.reviews.setdefault(doc_id, []).insert(0, {
            "id": len(self.reviews.get(doc_id, [])) + 1, "doc_id": doc_id,
            "verdict": verdict, "revision_no": revision_no,
            "findings": findings or [], "comment": comment,
        })
        return self

    def apply_review_action(self, doc_id, action):
        """The production transition table, applied for real. ``rejected + approve`` is
        absent from it, which is exactly why the old gate's re-rejection was fatal."""
        doc = self.docs[doc_id]
        nxt = transition_rules.get_doc_review_rule(doc["doc_review_status"], action)
        if nxt is None:
            raise ValueError(
                f"Invalid review transition: doc_review_status="
                f"'{doc['doc_review_status']}' + action='{action}'")
        doc["doc_review_status"] = nxt
        if action == "submit" and nxt == "revised":
            doc["revision_no"] = int(doc["revision_no"]) + 1
        for row in self.items:
            if row.get("result_doc_id") == doc_id:
                row["result_doc_review_status"] = nxt
        return nxt


@pytest.fixture
def gate_world(monkeypatch):
    w = World()
    store = PausedStore()
    calls: list[dict] = []

    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda d: {"id": 1})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda sid: [dict(i) for i in w.items])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: w.docs.get(doc_id))
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: w.docs.get(doc_id))
    monkeypatch.setattr(svc.db_reviews, "list_by_doc",
                        lambda doc_id: [dict(r) for r in w.reviews.get(doc_id, [])])
    monkeypatch.setattr(svc.db_reviews, "get_latest_by_doc",
                        lambda doc_id: dict(w.reviews[doc_id][0]) if w.reviews.get(doc_id) else None)
    monkeypatch.setattr(db_users, "get_by_id",
                        lambda uid: {"user_id": uid, "is_admin": 1})
    monkeypatch.setattr(wf_router, "_get_user_permissions",
                        lambda user: {"document.approve", "document.reject"})

    def _transition(*, doc_id, action, actor_user_id, user_permissions, **kw):
        calls.append({"doc_id": doc_id, "action": action})
        return {"doc_review_status": w.apply_review_action(doc_id, action)}

    monkeypatch.setattr(pipeline_service, "transition_document_review", _transition)
    monkeypatch.setattr(inbox_routes.tr_commit_service, "on_document_approved",
                        lambda doc_id: None)
    for name in ("upsert", "get_by_group", "exists", "delete_and_return",
                 "delete_by_group", "delete_system_stop", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(store, name))

    spawned = {"review": [], "rework": [], "work": [], "queued": [], "parked": []}
    monkeypatch.setattr(svc, "_spawn_review_hop",
                        lambda g, b, gate: spawned["review"].append(gate))
    monkeypatch.setattr(svc, "_spawn_rework_hop",
                        lambda g, b, gate: spawned["rework"].append(gate))
    monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, b: spawned["work"].append(dict(b)))
    monkeypatch.setattr(svc, "request_auto_resume",
                        lambda g, payload: spawned["queued"].append(dict(payload)))
    monkeypatch.setattr(svc, "clear_auto_resume", lambda g: None)
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda run, bundle, stop_code: spawned["parked"].append(stop_code))
    monkeypatch.setattr(svc, "_clear_handoff_row", lambda g, r: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda g, r: True)
    return {"world": w, "store": store, "calls": calls, "spawned": spawned}


def _bundle(**overrides):
    base = {"doc_ref": SPINE, "target_seq": 2, "issued_to": USER, "api_base_url": API_BASE,
            "locale": "ko", "instruction_mode": "auto_approved",
            "review_count_overrides": {"2": -1}, "reviewer_overrides": None}
    base.update(overrides)
    return base


def _run(**overrides):
    run = {"run_id": REVIEW_RUN, "group_id": GROUP, "doc_ref": SPINE, "mode": "single",
           "issued_to": USER, "continuation_target_seq": 2, "api_base_url": API_BASE}
    run.update(overrides)
    return run


class TestNr1RevisionAwareGateReachesCompleted:
    def test_a_reworked_revision_is_reviewed_approved_and_completes_the_chain(
            self, gate_world):
        """NR1 end to end: rev0 issues → rework → rev1 revised → rev1 pass.

        The three things NR0003 says went wrong are asserted as three separate observables:
        the single writer is never asked to reject the fresh revision, the approval it IS
        asked for is the legal ``revised + approve``, and the target slot's completion
        removes the paused row instead of parking an ``approve_failed`` card.
        """
        w, calls, spawned = gate_world["world"], gate_world["calls"], gate_world["spawned"]
        store = gate_world["store"]
        # The chain is standing on the target slot with a system stop row already written
        # by the hop that just finished — the row that became the permanent ghost card.
        store.put(continuation_target_seq=2, stop_code="hop_handoff")
        w.fill(2, TARGET_DOC, status="pending_review", revision_no=0)

        # ── round 1: an `issues` verdict on rev0 earns its rejection and its rework ──
        w.review(TARGET_DOC, "issues", revision_no=0,
                 findings=[{"locus": "§2", "note": "missing evidence"}])
        assert svc.run_review_gate(GROUP, _bundle(), _run()) is True
        assert [c["action"] for c in calls] == ["reject"], (
            "the same revision's own complaint is still rejected exactly once")
        assert w.docs[TARGET_DOC]["doc_review_status"] == "rejected"
        assert len(spawned["rework"]) == 1

        # ── the rework hop lands: ('rejected', 'submit') -> 'revised', revision_no 1 ──
        w.apply_review_action(TARGET_DOC, "submit")
        assert (w.docs[TARGET_DOC]["doc_review_status"],
                w.docs[TARGET_DOC]["revision_no"]) == ("revised", 1)

        # ── round 2 starts. THE REGRESSION: rev0's issues must not touch rev1 ──
        gate = svc.resolve_review_gate(_bundle())
        assert gate["stage"] == "review" and gate["round_no"] == 2
        assert "reject_first" not in gate, (
            "an old verdict must not re-reject the revision that answered it")
        assert svc.run_review_gate(GROUP, _bundle(), _run()) is True
        assert [c["action"] for c in calls] == ["reject"], (
            "no second rejection — this is the write that used to strand rev1 in 'rejected'")
        assert w.docs[TARGET_DOC]["doc_review_status"] == "revised", (
            "the fresh revision keeps the status that makes 'approve' a legal transition")
        assert len(spawned["review"]) == 1

        # ── round 2 passes on rev1 ──
        w.review(TARGET_DOC, "pass", revision_no=1)
        assert svc.run_review_gate(GROUP, _bundle(), _run()) is False, (
            "a completed chain does not start another hop")

        assert [c["action"] for c in calls] == ["reject", "approve"]
        assert w.docs[TARGET_DOC]["doc_review_status"] == "approved", (
            "settled through the ordinary revised + approve -> approved transition")
        assert spawned["parked"] == [], "no approve_failed park"
        assert store.rows == {}, "the target slot completing removed the paused row"
        assert store.deleted_groups == [GROUP]

    def test_the_old_verdict_path_would_have_needed_an_unlisted_transition(self):
        """NR1's counterfactual, and T0005 §1-5: the fix is NOT 'allow rejected + approve'.

        If the gate re-rejects a reworked revision, the pass that follows has to make this
        transition — and the production table has no such row, which is precisely the
        ``approve_failed`` NR0003 read off the live chain."""
        assert transition_rules.get_doc_review_rule("rejected", "approve") is None
        assert ("rejected", "approve") not in transition_rules.DOC_REVIEW_TRANSITIONS
        assert transition_rules.get_doc_review_rule("revised", "approve") == "approved"

    def test_the_same_revisions_issues_still_rejects_once_and_stays_idempotent(
            self, gate_world):
        """The positive control for the narrowed condition: when the complaint IS about the
        revision standing there, nothing changed — one rejection, then never again."""
        w, calls = gate_world["world"], gate_world["calls"]
        w.fill(2, TARGET_DOC, status="pending_review", revision_no=3)
        w.review(TARGET_DOC, "issues", revision_no=3)

        gate = svc.resolve_review_gate(_bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is True
        svc.run_review_gate(GROUP, _bundle(), _run())
        assert [c["action"] for c in calls] == ["reject"]

        gate = svc.resolve_review_gate(_bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is False
        svc.run_review_gate(GROUP, _bundle(), _run())
        assert [c["action"] for c in calls] == ["reject"], "idempotent, as before"

    def test_a_review_for_a_newer_revision_than_the_document_never_rejects(self, gate_world):
        """Defensive: a verdict recorded against a revision the slot does not carry is not
        evidence about this document either way, so it must not force a rejection."""
        w = gate_world["world"]
        w.fill(2, TARGET_DOC, status="pending_review", revision_no=1)
        w.review(TARGET_DOC, "issues", revision_no=4)
        gate = svc.resolve_review_gate(_bundle())
        assert gate["reject_first"] is False
        assert gate_world["calls"] == []


# ══════════════════════════════════════════════════════════════════════════════════════
# NR2 — wf_done clears a NULL-hop system stop
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNr2WfDoneClearsNullHopSystemStop:
    def test_a_finished_group_drops_the_single_hop_stop_by_exact_delete(self, cleanup_env):
        """The reported card, reconstructed from the live 0457 row: ``mode='single'``,
        ``hop_item_seq`` NULL, R/B root at ``wf_done``."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=10, stop_code="approve_failed")
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["wf_done"].add(GROUP)
        # Not "everything is complete" — the sequence still has an open slot. Only the
        # group's own terminal state answers here.
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2)]

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [(GROUP, REVIEW_RUN)], (
            "deleted by the exact (group_id, system, stop_run_id) snapshot")
        assert store.deleted_groups == [], "never a group-only delete"
        assert store.rows == {}

    def test_the_null_hop_alone_is_what_used_to_keep_it_forever(self, cleanup_env):
        """The pre-fix behaviour, pinned as a negative control: with no wf_done and no
        completion evidence, a NULL ``hop_item_seq`` still preserves the row — the old
        code's ONLY answer, and still the right one when nothing says the work is done."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=10, stop_code="approve_failed")
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2)]

        result = svc.active_all(USER)

        assert [r["group_id"] for r in result["paused"]] == [GROUP]
        assert result["paused"][0]["stop_run_id"] == REVIEW_RUN
        assert store.deleted_system == [] and store.rows

    def test_a_user_pause_in_a_finished_group_is_never_auto_deleted(self, cleanup_env):
        """T0005 §2-1: only ``stop_kind='system'`` rows carrying a ``stop_run_id`` are
        judged at all. A person's pause survives even a wf_done group."""
        store = cleanup_env["store"]
        store.put(stop_kind="user", stop_code=None, stop_run_id=None)
        cleanup_env["wf_done"].add(GROUP)

        result = svc.active_all(USER)

        assert [r["stop_kind"] for r in result["paused"]] == ["user"]
        assert store.deleted_system == [] and store.deleted_groups == []


# ══════════════════════════════════════════════════════════════════════════════════════
# NR3 — stored-scope completion, and what is NOT completion evidence
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNr3StoredScopeCompletion:
    def test_a_sequence_with_no_incomplete_slot_left_clears_the_row(self, cleanup_env):
        """(a) ``next_seq is None`` on a REAL sequence, with a non-terminal root."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2, stop_code="review_stalled")
        runs[REVIEW_RUN] = _single_review_run(stop_code="review_stalled")
        cleanup_env["seq"].items = [
            _slot(1, doc_id="d-1", status="approved"),
            _slot(2, doc_id="d-2", status="approved"),
        ]

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [(GROUP, REVIEW_RUN)]
        assert GROUP not in cleanup_env["wf_done"], "cleared without any terminal root"

    def test_a_next_slot_past_the_stored_target_clears_the_row(self, cleanup_env):
        """(b) ``next_seq > continuation_target_seq`` — the same reading ``resume_chain``
        already calls ``nothing_to_resume``, with work still open beyond the stored scope."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2, stop_code="approve_denied")
        runs[REVIEW_RUN] = _single_review_run(stop_code="approve_denied")
        cleanup_env["seq"].items = [
            _slot(1, doc_id="d-1", status="approved"),
            _slot(2, doc_id="d-2", status="approved"),
            _slot(3),                       # open, but past the target this chain stored
        ]

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [(GROUP, REVIEW_RUN)]

    def test_a_next_slot_at_the_stored_target_is_still_outstanding_work(self, cleanup_env):
        """The boundary the two cases above are drawn against: ``next_seq == target`` is
        work the chain still owes, so the card stays."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2, stop_code="approve_denied")
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2)]

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_an_unapproved_result_document_is_not_a_completed_slot(self, cleanup_env):
        """Completion is the existing slot definition — result document exists AND is
        approved. A submitted-but-unapproved slot is not done."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2, stop_code="approve_denied")
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].items = [
            _slot(1, doc_id="d-1", status="approved"),
            _slot(2, doc_id="d-2", status="pending_review"),
        ]

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_a_missing_sequence_is_not_completion_evidence(self, cleanup_env):
        """T0005 §2-3, negative: "no sequence" must not be read as "all done". Before this
        split, ``_next_incomplete_item_seq`` returned ``None`` for both."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].present = False

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == [] and store.rows

    def test_an_empty_sequence_is_not_completion_evidence(self, cleanup_env):
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].items = []

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_a_failed_sequence_lookup_keeps_the_row_and_the_response(self, cleanup_env):
        """T0005 §2-6: a lookup that raises must not delete anything and must not fail
        ``/ai-invoke/active-all`` — the widget still renders the card it could not judge."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].explode = True

        result = svc.active_all(USER)

        assert result["ok"] is True
        assert [r["group_id"] for r in result["paused"]] == [GROUP]
        assert store.deleted_system == [] and store.rows

    def test_a_missing_sequence_does_not_delete_an_item_seq_row_either(self, cleanup_env):
        """The teeth of §2-3. With a head to compare against, the ORIGINAL judgement read
        a missing sequence as ``current_seq is None`` and therefore deleted the row. The
        absence of a sequence is not the completion of one."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code="head_slot_mismatch", continuation_target_seq=3)
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=2)
        cleanup_env["seq"].present = False

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_a_failed_sequence_lookup_does_not_delete_an_item_seq_row_either(
            self, cleanup_env):
        """Same teeth for the raising case: the old code let the exception land in one
        catch-all that returned "not stale", but only because the whole probe aborted —
        here the lookup fails while every other fact is readable, and the row still stays."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code="advance_blocked", continuation_target_seq=3)
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=2)
        cleanup_env["seq"].explode = True

        result = svc.active_all(USER)

        assert result["ok"] is True
        assert [r["group_id"] for r in result["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_a_failed_wf_done_probe_falls_through_instead_of_deleting(self, cleanup_env,
                                                                     monkeypatch):
        """The terminal probe is the most conclusive branch, so its failure must be the
        least destructive: fall through to the evidence below it, never to "stale"."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=2)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2)]

        def _boom(group_id):
            raise RuntimeError("documents table unreadable")

        monkeypatch.setattr(svc.db_docs, "group_root_wf_done", _boom)

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_the_split_reports_both_facts_separately(self, cleanup_env):
        """The helper the three branches above stand on, read directly: "a sequence was
        found" and "the first incomplete slot" are two answers, not one."""
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2)]
        assert svc._sequence_completion_state(SPINE) == (True, 2)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved")]
        assert svc._sequence_completion_state(SPINE) == (True, None)
        assert svc._next_incomplete_item_seq(SPINE) is None
        cleanup_env["seq"].present = False
        assert svc._sequence_completion_state(SPINE) == (False, None)
        assert svc._next_incomplete_item_seq(SPINE) is None, (
            "the old single-value contract is unchanged for resume_chain")


# ══════════════════════════════════════════════════════════════════════════════════════
# NR4 — preservation and the exact-delete race
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNr4PreservationAndExactDeleteRace:
    def test_a_genuinely_outstanding_system_stop_survives(self, cleanup_env):
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=3, stop_code="head_slot_mismatch")
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=2)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2),
                                    _slot(3)]

        result = svc.active_all(USER)

        assert [r["group_id"] for r in result["paused"]] == [GROUP]
        assert result["paused"][0]["stop_code"] == "head_slot_mismatch"
        assert store.deleted_system == []

    def test_a_newer_system_stop_written_under_the_read_survives(self, cleanup_env):
        """The race T0005 §2-5 names: the row is judged from a snapshot, and by the time
        the delete runs the group carries a DIFFERENT system stop. The exact predicate
        misses; a ``delete_by_group`` would have taken the new row with it."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        snapshot = dict(store.put(continuation_target_seq=10))
        newer = store.put(stop_run_id=OTHER_RUN, stop_code="advance_blocked",
                          continuation_target_seq=10)
        store.snapshot = [snapshot]          # active_all still sees the old row
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["wf_done"].add(GROUP)

        result = svc.active_all(USER)

        assert result["paused"] == [], "the snapshot it judged is hidden"
        assert store.deleted_system == [(GROUP, REVIEW_RUN)]
        assert store.rows[GROUP]["stop_run_id"] == OTHER_RUN, (
            "the newer stop survived the delete")
        assert store.rows[GROUP] == newer

    def test_a_user_pause_written_under_the_read_survives(self, cleanup_env):
        """Same race, worse outcome if it went wrong: a person pressed [일시정지] between
        the read and the delete, and their row must not be swept away by a cleanup that
        was aimed at a system stop."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        snapshot = dict(store.put(continuation_target_seq=10))
        store.put(stop_kind="user", stop_code=None, stop_run_id=None,
                  continuation_target_seq=10)
        store.snapshot = [snapshot]
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["wf_done"].add(GROUP)

        svc.active_all(USER)

        assert store.deleted_system == [(GROUP, REVIEW_RUN)]
        assert store.rows[GROUP]["stop_kind"] == "user", "the human's pause is still there"

    def test_a_group_only_delete_would_have_taken_both(self, cleanup_env):
        """The control that makes the two tests above bite: with the same fake store, a
        group-scoped delete removes the row the exact delete leaves alone. An
        implementation that called ``delete_by_group`` cannot pass this file."""
        store = cleanup_env["store"]
        store.put(stop_run_id=OTHER_RUN, stop_code="advance_blocked")
        store.delete_system_stop(GROUP, REVIEW_RUN)
        assert store.rows[GROUP]["stop_run_id"] == OTHER_RUN
        store.delete_by_group(GROUP)
        assert GROUP not in store.rows

    def test_the_production_delete_is_the_same_predicate(self, monkeypatch):
        """Pins the fake to production: the real ``delete_system_stop`` must issue a
        DELETE keyed on all three of group_id, system stop_kind and stop_run_id — and
        must issue nothing at all without a stop_run_id."""
        issued: list[tuple[str, list]] = []

        class _Store:
            def _execute(self, sql, params):
                issued.append((" ".join(sql.split()), list(params)))

        monkeypatch.setattr(db_paused, "get_store", lambda: _Store())

        db_paused.delete_system_stop(GROUP, REVIEW_RUN)
        assert len(issued) == 1
        sql, params = issued[0]
        assert sql.startswith("DELETE FROM ai_invoke_paused_chains WHERE ")
        assert "group_id = ?" in sql
        assert "COALESCE(stop_kind, 'user') = 'system'" in sql
        assert "stop_run_id = ?" in sql
        assert params == [GROUP, REVIEW_RUN]

        db_paused.delete_system_stop(GROUP, None)
        assert len(issued) == 1, "no stop_run_id, no delete"

    def test_a_delete_failure_does_not_fail_the_whole_response(self, cleanup_env,
                                                               monkeypatch):
        """T0005 §2-6: cleanup is best-effort. The stale row is still withheld from the
        card list and the next active-all retries the delete."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(continuation_target_seq=10)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["wf_done"].add(GROUP)

        def _boom(group_id, stop_run_id):
            raise RuntimeError("delete failed")

        monkeypatch.setattr(db_paused, "delete_system_stop", _boom)

        result = svc.active_all(USER)

        assert result["ok"] is True and result["paused"] == []
        assert store.rows, "the row is still there for the next attempt"

    def test_a_hop_handoff_inside_its_grace_is_still_hidden_not_deleted(self, cleanup_env):
        """The handoff grace is untouched by this change: an in-flight handoff is hidden
        by ``_handoff_row_in_flight`` BEFORE the staleness test, so it is never deleted."""
        from datetime import datetime, timezone

        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code=svc.HOP_HANDOFF_STOP_CODE,
                  paused_at=datetime.now(timezone.utc).isoformat(),
                  continuation_target_seq=10)
        runs[REVIEW_RUN] = _single_review_run()
        cleanup_env["wf_done"].add(GROUP)

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [], "hidden by the grace, not swept"
        assert store.rows, "the row survives for the successor hop to clear"


# ══════════════════════════════════════════════════════════════════════════════════════
# NR5 — the item_seq judgement for ordinary continuous stops is unchanged
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNr5ItemSeqJudgementUnchanged:
    @pytest.mark.parametrize("stop_code", ["head_slot_mismatch", "advance_blocked"])
    def test_a_live_row_on_the_same_head_is_kept(self, cleanup_env, stop_code):
        """Positive control for the original contract: the head has not moved, so the stop
        still describes the slot it stopped on and the card stays."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code=stop_code, continuation_target_seq=3)
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=2)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2),
                                    _slot(3)]

        result = svc.active_all(USER)

        assert [r["stop_code"] for r in result["paused"]] == [stop_code]
        assert store.deleted_system == []

    @pytest.mark.parametrize("stop_code", ["head_slot_mismatch", "advance_blocked"])
    def test_a_reopen_that_moved_the_head_is_exact_deleted(self, cleanup_env, stop_code):
        """The original stale case, preserved: the stop points at slot 3 and a reopen put
        the head back on slot 2, so the row no longer describes anything."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code=stop_code, continuation_target_seq=3)
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=3)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2),
                                    _slot(3)]

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [(GROUP, REVIEW_RUN)]
        assert store.deleted_groups == []

    def test_an_item_seq_row_whose_scope_finished_is_cleared_by_completion(self,
                                                                          cleanup_env):
        """Completion evidence is checked before the head test, so a continuous stop in a
        finished scope is cleared by the same rule as a review hop's — no reopen needed."""
        store, runs = cleanup_env["store"], cleanup_env["runs"]
        store.put(stop_code="advance_blocked", continuation_target_seq=2)
        runs[REVIEW_RUN] = _single_review_run(mode="continuous", hop_item_seq=2)
        cleanup_env["seq"].items = [
            _slot(1, doc_id="d-1", status="approved"),
            _slot(2, doc_id="d-2", status="approved"),
        ]

        result = svc.active_all(USER)

        assert result["paused"] == []
        assert store.deleted_system == [(GROUP, REVIEW_RUN)]

    def test_a_vanished_run_record_keeps_the_row(self, cleanup_env):
        """"Missing legacy evidence is not stale" is unchanged: a stop whose run record is
        gone has no head to compare and is preserved."""
        store = cleanup_env["store"]
        store.put(stop_code="head_slot_mismatch", continuation_target_seq=3)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2),
                                    _slot(3)]

        assert [r["group_id"] for r in svc.active_all(USER)["paused"]] == [GROUP]
        assert store.deleted_system == []

    def test_a_failed_run_lookup_keeps_the_row(self, cleanup_env, monkeypatch):
        store = cleanup_env["store"]
        store.put(stop_code="advance_blocked", continuation_target_seq=3)
        cleanup_env["seq"].items = [_slot(1, doc_id="d-1", status="approved"), _slot(2),
                                    _slot(3)]

        def _boom(run_id):
            raise RuntimeError("runs table unreadable")

        monkeypatch.setattr(db_runs, "get", _boom)

        result = svc.active_all(USER)

        assert result["ok"] is True
        assert [r["group_id"] for r in result["paused"]] == [GROUP]
        assert store.deleted_system == []
