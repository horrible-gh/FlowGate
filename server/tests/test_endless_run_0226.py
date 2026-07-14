"""flowgate.default.0226 B0001 "끝나지 않는 실행" — NR0003 §5 fixes.

§5-1 coordinate unification — continuation_target_seq is a workflow item_seq;
      ai_invoke docs_target counts the sequence's worker items (instruction heads
      N/T excluded: auto-created server-side as drafts) instead of subtracting the
      unrelated group document seq. (covered here + test_ai_invoke_0187 admission)
§5-2 honest counters — the live docs_reached_so_far uses the SAME oracle filter as
      the final judge (non-draft docs past baseline), and the final docs_reached is
      no longer min()-clamped, so an overrun stays visible.
§5-3 chain stop defense — an inbox submission that fills no workflow head slot
      (completed_seq=None) pauses the continuation instead of advancing forever.
§5-4 target_id validation symmetry — the frontmatter identity guard accepts the
      short form the mention itself instructs ("target_id: B0001").
§5-5 first hop parity — a continuous (non-review) invoke start routes through
      advance_workflow so instruction heads are auto-completed exactly like every
      later self-chain hop; review mode stays on the direct-issue path.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402


# ── §5-1: docs_target in the item_seq coordinate system ──────────────────────

class _Wfseq:
    def __init__(self, items, decided=True):
        self.sequence = {"id": 1} if decided else None
        self.items = items

    def install(self, monkeypatch):
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda d: self.sequence)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", lambda d: self.sequence)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda s: list(self.items))


def _item(item_seq, type_, result=None):
    return {"item_seq": item_seq, "type": type_, "result_doc_id": result}


class TestContinuationDocsTarget:
    def test_counts_only_worker_items_up_to_target(self, monkeypatch):
        _Wfseq([
            _item(1, "N", "d-N"), _item(2, "NR", "d-NR"),
            _item(3, "T"), _item(4, "TR"), _item(5, "TS"), _item(6, "TSR"),
        ]).install(monkeypatch)
        # target 4: pending ≤ 4 = T(3)+TR(4); the T auto-completes as a draft ⇒ 1.
        assert svc._continuation_docs_target("doc", 4) == 1
        # target 6: TR + TS + TSR ⇒ 3.
        assert svc._continuation_docs_target("doc", 6) == 3

    def test_sparse_item_seq_after_edit_workflow_pending(self, monkeypatch):
        # 0226 B0001 ②: renumbered pending tail (18–21) — the old group-seq
        # subtraction produced 9 for a 12-doc group; item counting gives 3.
        _Wfseq([
            _item(18, "T"), _item(19, "TR"), _item(20, "TS"), _item(21, "TSR"),
        ]).install(monkeypatch)
        assert svc._continuation_docs_target("doc", 21) == 3

    def test_realized_items_do_not_count_when_pending_only(self, monkeypatch):
        _Wfseq([
            _item(1, "T", "d-T"), _item(2, "TR", "d-TR"), _item(3, "TS"), _item(4, "TSR"),
        ]).install(monkeypatch)
        assert svc._continuation_docs_target("doc", 4) == 2                       # TS + TSR
        assert svc._continuation_docs_target("doc", 4, pending_only=False) == 3    # + TR

    def test_to_end_has_no_upper_bound(self, monkeypatch):
        _Wfseq([_item(1, "N", "d"), _item(2, "NR", "d"), _item(3, "M")]).install(monkeypatch)
        assert svc._continuation_docs_target("doc", None, pending_only=False) == 2  # NR + M

    def test_undecided_sequence_returns_none(self, monkeypatch):
        _Wfseq([], decided=False).install(monkeypatch)
        assert svc._continuation_docs_target("doc", 5) is None


# ── §5-2: honest live counter + unclamped final count ────────────────────────

def _run_dict(tmp_path, *, docs_target, baseline_seq=4, action_scope="new",
              mode="continuous", target_to_end=False):
    run = {
        "run_id": "aiv_0226", "status": "running", "mode": mode,
        "project_id": "flowgate", "module": "default",
        "group_id": "flowgate.default.0226",
        "doc_ref": "flowgate.default.0226.0001-B",
        "docs_target": docs_target, "baseline_seq": baseline_seq,
        "timeout_sec": 60, "provider": None, "provider_id": "aip_x",
        "attempt_no": 1, "fallback_history": [],
        "started_at": "t", "started_mono": time.monotonic(),
        "cancel_event": threading.Event(), "proc": None,
        "timed_out": False, "end_reason": "exited", "exit_code": 0,
        "last_message": None, "last_message_received": False,
        "outcome": None, "docs_reached": 0, "reached_doc_ids": [],
        "source_dirty": None, "source_dirty_files": [],
        "scratch_dir": str(tmp_path / "scratch" / "aiv_0226"),
        "scratch_retained": None, "duration_ms": None, "finished_at": None,
        "dirty_baseline": None, "source_root": None,
        "api_base_url": "", "chain_source": "system", "raw_token": "x",
        "action_scope": action_scope, "target_to_end": target_to_end,
    }
    Path(run["scratch_dir"]).mkdir(parents=True, exist_ok=True)
    return run


def _fake_group_docs(monkeypatch, docs):
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda g: list(docs))


class TestHonestCounters:
    def test_status_counter_uses_oracle_filter_not_max_seq(self, monkeypatch, tmp_path):
        # Group max-seq jumped to 9 (drafts + foreign docs) but only 2 non-draft docs
        # past baseline exist — the live counter must say 2, not 5.
        run = _run_dict(tmp_path, docs_target=3)
        monkeypatch.setattr(svc, "_runs", {"aiv_0226": run})
        _fake_group_docs(monkeypatch, [
            {"doc_id": "a", "seq": 4, "status": "open"},        # ≤ baseline: not this run
            {"doc_id": "b", "seq": 5, "status": "open"},
            {"doc_id": "c", "seq": 6, "status": "draft"},       # auto-created N/T
            {"doc_id": "d", "seq": 7, "status": "open"},
            {"doc_id": "e", "seq": 9, "status": "draft"},
        ])
        monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda g: 9)
        status = svc.get_status("aiv_0226")
        assert status["docs_reached_so_far"] == 2

    def test_settle_keeps_overrun_visible(self, monkeypatch, tmp_path):
        # 4 docs landed against a target of 3: previously clamped to 3/3 at the end,
        # hiding the overrun. Now the finish payload says 4/3 (outcome stays complete).
        monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
        monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)
        run = _run_dict(tmp_path, docs_target=3)
        _fake_group_docs(monkeypatch, [
            {"doc_id": f"d{s}", "seq": s, "status": "open"} for s in (5, 6, 7, 8)
        ])
        svc._settle_and_judge(run)
        assert run["docs_reached"] == 4
        assert run["docs_target"] == 3
        assert run["outcome"] == "complete"

    def test_to_end_settle_resolves_target_from_worker_items(self, monkeypatch, tmp_path):
        # workflow_decide to-end: docs_target resolves to the decided sequence's
        # worker-item count (N excluded), not max(item_seq) - baseline.
        monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
        monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)
        _Wfseq([
            _item(1, "N", "d-N"), _item(2, "NR", "d-NR"),
            _item(3, "T", "d-T"), _item(4, "TR", "d-TR"),
        ]).install(monkeypatch)
        run = _run_dict(tmp_path, docs_target=0, action_scope="workflow_decide",
                        target_to_end=True)
        _fake_group_docs(monkeypatch, [
            {"doc_id": "d5", "seq": 5, "status": "open"},
            {"doc_id": "d6", "seq": 6, "status": "draft"},
            {"doc_id": "d7", "seq": 7, "status": "open"},
        ])
        svc._settle_and_judge(run)
        assert run["docs_target"] == 2      # NR + TR (old math: max item_seq 4 - baseline 4 = 0)
        assert run["docs_reached"] == 2     # drafts stay invisible to the oracle
        assert run["outcome"] == "complete"


# ── §5-3: chain stop defense (slot-less submission pauses) ───────────────────

class _FakeRequest:
    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://h/"


def test_self_chain_pauses_when_no_head_slot_matched(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import workflow_sequences as wfseq
    from modules.flow_gate.workflow import pipeline_service

    monkeypatch.setattr(wfseq, "get_item_by_result_doc_id", lambda _d: None)
    approve = MagicMock()
    monkeypatch.setattr(pipeline_service, "transition_document_review", approve)
    advance = MagicMock()
    from modules.flow_gate.services import workflow_decision_service as wds
    monkeypatch.setattr(wds, "advance_workflow", advance)

    env = inbox_routes._continuation_self_chain(
        _FakeRequest(),
        {"doc_ref": "flowgate.default.0226.0001-B", "issued_to": "pm",
         "continuation_target_seq": 6, "continuation_review_mode": 0},
        "flowgate", "flowgate.default.0226.0009-M", "M",
    )
    assert env["continuation_paused"] is True
    assert "did not fill the current workflow head slot" in env["continuation_reason"]
    assert "next_token" not in env
    approve.assert_not_called()   # the stray doc stays unapproved for human triage
    advance.assert_not_called()   # and no further token is minted (the endless-run cut)


# ── §5-4: target_id identity-guard symmetry ──────────────────────────────────

class TestTargetIdAlternates:
    EXPECTED = "flowgate.default.0226.0001-B"

    def _mismatch(self, declared_target):
        from modules.flow_gate.api import inbox_routes
        text = (
            "---\n"
            "project: flowgate\n"
            "module: default\n"
            "group: 0226\n"
            "type: TR\n"
            f"target_id: {declared_target}\n"
            "---\n\n# body\n"
        )
        return inbox_routes._frontmatter_identity_mismatch(
            text,
            expected_project="flowgate",
            expected_module="default",
            expected_group_id="flowgate.default.0226",
            expected_doc_type="TR",
            expected_target_id=self.EXPECTED,
        )

    def test_short_form_from_the_mention_is_accepted(self):
        # The mention's §2 header instructs exactly this spelling (B0001).
        assert self._mismatch("B0001") is None

    def test_code_form_is_accepted(self):
        assert self._mismatch("0001-B") is None

    def test_canonical_form_still_accepted(self):
        assert self._mismatch(self.EXPECTED) is None

    def test_genuinely_different_target_still_rejected(self):
        assert "target_id" in (self._mismatch("B0002") or "")
        assert "target_id" in (self._mismatch("flowgate.default.0225.0001-B") or "")


# ── §5-5: continuous invoke first hop routes through advance_workflow ────────

def _invoke_client(monkeypatch, captured):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api.v1 import ai_invoke_routes as air

    monkeypatch.setattr(air, "verify_bearer", lambda req: {
        "_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": 1,
    })
    monkeypatch.setattr(air, "has_permission", lambda *a: True)
    monkeypatch.setattr(air.db_projects, "get_by_id", lambda pid: {"project_id": pid})

    def _fake_advance(**kwargs):
        captured["advance"] = kwargs
        return {"token": "ADVRAW", "token_id": "tok-adv", "scratch_dir": "/scratch",
                "mention": "ADV-MENTION", "expires_at": "2026-07-15"}

    monkeypatch.setattr(air.workflow_decision_service, "advance_workflow", _fake_advance)

    def _fake_start_run(**kwargs):
        captured["start_run"] = kwargs
        issue_builder = kwargs.get("issue_builder")
        if issue_builder is not None:
            captured["issue"] = issue_builder()
        return {"ok": True, "run_id": "aiv_test", "status": "running"}

    monkeypatch.setattr(air.ai_invoke_service, "start_run", _fake_start_run)

    app = FastAPI()
    app.include_router(air.router)
    return TestClient(app)


def _start_body(**overrides):
    body = {
        "project": "flowgate", "module": "default", "group": "0226",
        "doc_ref": "flowgate.default.0226.0001-B", "action_scope": "new",
        "mode": "continuous", "continuation_target_seq": 6,
        "continuation_review_mode": False,
    }
    body.update(overrides)
    return body


class TestFirstHopParity:
    def test_continuous_new_routes_through_advance_workflow(self, monkeypatch):
        captured: dict = {}
        client = _invoke_client(monkeypatch, captured)
        resp = client.post("/api/v1/ai-invoke/start", json=_start_body(),
                           headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        adv = captured["advance"]
        assert adv["continuous"] is True
        assert adv["continuation_target_seq"] == 6
        assert adv["doc_id"] == "flowgate.default.0226.0001-B"
        # The advance-minted token/mention feed the run (start_run's issue_builder).
        assert captured["issue"]["raw_token"] == "ADVRAW"
        assert captured["issue"]["mention"] == "ADV-MENTION"

    def test_review_mode_stays_on_direct_issue(self, monkeypatch):
        # Pre-flight Q phase must not create documents: no advance (whose instruction
        # auto-complete writes N/T docs) — the direct-issue path handles it.
        captured: dict = {}
        client = _invoke_client(monkeypatch, captured)
        resp = client.post("/api/v1/ai-invoke/start",
                           json=_start_body(continuation_review_mode=True),
                           headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        assert "advance" not in captured
        assert captured["start_run"]["issue_builder"] is None

    def test_single_mode_keeps_direct_issue(self, monkeypatch):
        captured: dict = {}
        client = _invoke_client(monkeypatch, captured)
        resp = client.post("/api/v1/ai-invoke/start",
                           json=_start_body(mode="single", continuation_target_seq=None),
                           headers={"Authorization": "Bearer x"})
        assert resp.status_code == 200
        assert "advance" not in captured
        assert captured["start_run"]["issue_builder"] is None

    def test_advance_conflict_maps_to_409(self, monkeypatch):
        captured: dict = {}
        client = _invoke_client(monkeypatch, captured)
        from modules.flow_gate.api.v1 import ai_invoke_routes as air

        def _blocked(**kwargs):
            raise ValueError("head_in_progress:TR:작업레포트")

        monkeypatch.setattr(air.workflow_decision_service, "advance_workflow", _blocked)

        def _fake_start_run(**kwargs):
            issue_builder = kwargs.get("issue_builder")
            if issue_builder is not None:
                issue_builder()  # raises ValueError like the real start_run would
            return {"ok": True}

        monkeypatch.setattr(air.ai_invoke_service, "start_run", _fake_start_run)
        resp = client.post("/api/v1/ai-invoke/start", json=_start_body(),
                           headers={"Authorization": "Bearer x"})
        assert resp.status_code == 409
