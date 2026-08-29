"""flowgate.default.0414 T0010: 연속 작업 [검수] 게이트 — 요청 계약 · 상태 도출 · 지속성.

Covers the server half of P0007 / L0008 / DB0009:

  * request contract (P0007): the two maps' normalization, the ONE 422 envelope every
    shape/value/sequence violation produces, `reviewer_unavailable`, and the single /
    workflow_decide paths that drop the maps instead of refusing them.
  * gate derivation (L0008 §2.3): every branch of resolve_review_gate, derived from
    document_reviews + revision_no + doc_review_status with nothing persisted.
  * automatic rejection (§2.6): permission refusal, transition failure, the reason text.
  * carriers (P0007 전달 지점 1~7 + L0008 §2.9 8~10 / DB0009 I3): the two maps survive
    pause, system stop, hop settlement, resume and a failed resume's row restore — with an
    AST guard that no paused-row upsert caller can quietly omit them again.
  * the omitted-maps control group: a request that sends neither map behaves EXACTLY as
    it did before this feature, which is the claim the whole change rests on.
  * migration 086 in all three dialects, applied for real against sqlite.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import HTTPException  # noqa: E402

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.api.v1 import ai_invoke_routes as routes  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import users as db_users  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402
from modules.flow_gate.workflow import pipeline_service  # noqa: E402
from modules.flow_gate.workflow.transition_rules import get_doc_review_rule  # noqa: E402

GROUP = "flowgate.default.0414"
SPINE = "flowgate.default.0414.0001-R"
API_BASE = "http://127.0.0.1:8089/flowgate/api/v1"
USER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"

# P0007's own worked example: 1 P · 2 L · 3 DB · 4 T · 5 TR · 6 T · 7 TR · 8 TS · 9 TSR,
# instruction_mode auto_approved, so 4 and 6 (T) are server-handled and 9 (TSR) is
# server-assembled — the worker slots are 1, 2, 3, 5, 7, 8.
SEQUENCE = [
    {"item_seq": 1, "type": "P", "status": "todo", "provider_id": None},
    {"item_seq": 2, "type": "L", "status": "todo", "provider_id": None},
    {"item_seq": 3, "type": "DB", "status": "todo", "provider_id": None},
    {"item_seq": 4, "type": "T", "status": "todo", "provider_id": None},
    {"item_seq": 5, "type": "TR", "status": "todo", "provider_id": "aip_step5"},
    {"item_seq": 6, "type": "T", "status": "todo", "provider_id": None},
    {"item_seq": 7, "type": "TR", "status": "todo", "provider_id": None},
    {"item_seq": 8, "type": "TS", "status": "todo", "provider_id": None},
    {"item_seq": 9, "type": "TSR", "status": "todo", "provider_id": None},
]

PROVIDERS = [
    {"id": "aip_default", "name": "Claude Opus 5", "exec_type": "cli", "kind": "claude"},
    {"id": "aip_rev", "name": "Codex GPT-5.6 Sol", "exec_type": "cli", "kind": "codex"},
    {"id": "aip_step5", "name": "Claude Sonnet 5", "exec_type": "cli", "kind": "claude"},
]


def _seq_items(overrides=None):
    items = [dict(row) for row in SEQUENCE]
    for item_seq, patch in (overrides or {}).items():
        for row in items:
            if row["item_seq"] == item_seq:
                row.update(patch)
    return items


# ══════════════════════════════════════════════════════════════════════════════════════
# P0007 — normalization
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNormalization:
    def test_worked_example_normalizes_unchanged(self):
        counts = wds.normalize_continuation_review_count_overrides(
            {"1": 1, "3": 2, "5": -1, "7": 3})
        assert counts == {"1": 1, "3": 2, "5": -1, "7": 3}
        reviewers = wds.normalize_continuation_reviewer_overrides(
            {"1": "aip_rev", "3": "aip_step5", "5": "aip_rev", "7": "aip_rev"}, counts)
        assert reviewers == {"1": "aip_rev", "3": "aip_step5", "5": "aip_rev", "7": "aip_rev"}

    def test_integer_and_string_keys_unify_to_strings(self):
        counts = wds.normalize_continuation_review_count_overrides({3: 2, "5": 1})
        assert counts == {"3": 2, "5": 1}
        assert wds.normalize_continuation_reviewer_overrides({3: "aip_rev"}, counts) == {
            "3": "aip_rev"}

    def test_zero_counts_and_their_orphan_reviewers_fold_to_none(self):
        """P0007 [엣지] 값이 전부 0: the dialog's untouched defaults must produce exactly
        what "sent no maps at all" produces — one representation of "no selection"."""
        counts = wds.normalize_continuation_review_count_overrides(
            {"1": 0, "3": 0, "5": 0, "7": 0})
        assert counts is None
        reviewers = wds.normalize_continuation_reviewer_overrides(
            {"1": "aip_rev", "3": "aip_rev", "5": "aip_rev", "7": "aip_rev"}, counts)
        assert reviewers is None

    def test_a_zero_step_drops_only_its_own_reviewer(self):
        counts = wds.normalize_continuation_review_count_overrides({"3": 0, "5": 2})
        assert counts == {"5": 2}
        assert wds.normalize_continuation_reviewer_overrides(
            {"3": "aip_rev", "5": "aip_step5"}, counts) == {"5": "aip_step5"}

    def test_empty_and_missing_maps_are_none(self):
        assert wds.normalize_continuation_review_count_overrides(None) is None
        assert wds.normalize_continuation_review_count_overrides({}) is None
        assert wds.normalize_continuation_reviewer_overrides({}, None) is None

    @pytest.mark.parametrize("value", [4, -2, 5, 99])
    def test_out_of_range_count_is_refused(self, value):
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_review_count_overrides({"5": value})
        assert str(exc.value).startswith(f"invalid_review_count_value:{value}")
        assert "must be one of -1, 0, 1, 2, 3" in str(exc.value)

    def test_string_count_is_refused(self):
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_review_count_overrides({"5": "2"})
        assert str(exc.value).startswith("invalid_review_count_value:'2'")

    def test_bool_count_is_refused_even_though_true_equals_one(self):
        """Unguarded, `true` would sail through as "review once" — a value nobody chose."""
        assert True == 1  # noqa: E712 — the hazard, stated
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_review_count_overrides({"7": True})
        assert "invalid_review_count_value:True" in str(exc.value)

    @pytest.mark.parametrize("key", ["TR#1", "0", "-1", "abc", ""])
    def test_non_item_seq_keys_are_refused_on_both_maps(self, key):
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_review_count_overrides({key: 2})
        assert str(exc.value).startswith("invalid_review_item_seq_key:")
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_reviewer_overrides({key: "aip_rev"}, {"5": 2})
        assert str(exc.value).startswith("invalid_review_item_seq_key:")

    @pytest.mark.parametrize("value", ["", "   ", 7, None, True])
    def test_bad_reviewer_ids_are_refused(self, value):
        with pytest.raises(ValueError) as exc:
            wds.normalize_continuation_reviewer_overrides({"5": value}, {"5": 2})
        assert str(exc.value).startswith("invalid_reviewer_provider_id:")

    def test_a_bad_reviewer_id_is_refused_even_on_an_orphan_step(self):
        """Validation precedes rule 3's orphan drop: the request is malformed either way,
        and accepting it would hide the client bug until that step came up."""
        with pytest.raises(ValueError):
            wds.normalize_continuation_reviewer_overrides({"5": ""}, None)

    def test_non_object_maps_are_refused(self):
        with pytest.raises(ValueError):
            wds.normalize_continuation_review_count_overrides([1, 2])
        with pytest.raises(ValueError):
            wds.normalize_continuation_reviewer_overrides("aip_rev", {"5": 2})


# ══════════════════════════════════════════════════════════════════════════════════════
# P0007 — sequence-aware validation
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_sequence(monkeypatch):
    state = {"items": _seq_items()}
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", lambda seq_id: list(state["items"]))
    return state


class TestSequenceValidation:
    def test_worker_slots_pass(self, fake_sequence):
        wds.validate_continuation_review_item_seqs(
            {"1": 1, "3": 2, "5": -1, "7": 3}, SPINE, 9,
            instruction_mode="auto_approved", auto_approve_item_seqs=[])

    def test_auto_handled_instruction_head_is_ineligible(self, fake_sequence):
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"4": 1}, SPINE, 9,
                instruction_mode="auto_approved", auto_approve_item_seqs=[])
        assert str(exc.value).startswith("ineligible_review_item_seq:4")
        assert "no worker output to review" in str(exc.value)

    def test_the_same_step_is_eligible_under_ai_direct(self, fake_sequence):
        """P0007: eligibility reads the request's OWN mode, so the same map is valid under
        ai_direct and invalid under auto_approved. That difference is the contract."""
        wds.validate_continuation_review_item_seqs(
            {"4": 1}, SPINE, 9,
            instruction_mode="ai_direct", auto_approve_item_seqs=[])
        with pytest.raises(ValueError):
            wds.validate_continuation_review_item_seqs(
                {"4": 1}, SPINE, 9,
                instruction_mode="ai_direct", auto_approve_item_seqs=[4])

    def test_server_assembled_tsr_is_ineligible(self, fake_sequence):
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"9": 2}, SPINE, 9, instruction_mode="auto_approved")
        assert str(exc.value).startswith("ineligible_review_item_seq:9")

    def test_unknown_slot_is_ineligible(self, fake_sequence):
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"12": 1}, SPINE, 12, instruction_mode="auto_approved")
        assert str(exc.value).startswith("ineligible_review_item_seq:12")

    def test_first_violation_wins_in_ascending_order(self, fake_sequence):
        """P0007's own example {4,9,12} reports ONE error, and it is the lowest slot."""
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"4": 1, "9": 2, "12": 1}, SPINE, 9, instruction_mode="auto_approved")
        assert str(exc.value).startswith("ineligible_review_item_seq:4")

    def test_beyond_the_target_is_out_of_range(self, fake_sequence):
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"7": 2}, SPINE, 5, instruction_mode="auto_approved")
        assert str(exc.value) == (
            "out_of_range_review_item_seq:7 — beyond continuation_target_seq 5")

    def test_finished_step_is_refused_on_a_fresh_request_only(self, fake_sequence):
        fake_sequence["items"] = _seq_items({1: {"status": "done"}})
        with pytest.raises(ValueError) as exc:
            wds.validate_continuation_review_item_seqs(
                {"1": 2}, SPINE, 9, instruction_mode="auto_approved")
        assert str(exc.value) == "already_done_review_item_seq:1 — pick a remaining step"
        # The ONGOING chain re-reads the same selection every hop; a step it finished
        # itself must not retroactively invalidate the rest of the chain.
        wds.validate_continuation_review_item_seqs(
            {"1": 2}, SPINE, 9, instruction_mode="auto_approved",
            reject_already_done=False)

    def test_no_selection_skips_the_lookup_entirely(self, monkeypatch):
        called = []
        monkeypatch.setattr(wds.db_wfseq, "get_sequence_for_member_doc",
                            lambda doc_id: called.append(doc_id))
        wds.validate_continuation_review_item_seqs(None, SPINE, 9)
        wds.validate_continuation_review_item_seqs({}, SPINE, 9)
        assert called == []


class TestReviewerAvailability:
    def test_unknown_reviewer_is_reported(self, monkeypatch):
        monkeypatch.setattr(wds, "provider_view_of", lambda pid: {
            "readable": True, "providers": {p["id"]: p for p in PROVIDERS}})
        assert wds.unavailable_reviewer_provider_ids("flowgate", {"5": "aip_deleted"}) == [
            "aip_deleted"]
        assert wds.unavailable_reviewer_provider_ids("flowgate", {"5": "aip_rev"}) == []

    def test_unreadable_provider_view_fails_open(self, monkeypatch):
        """A transient settings failure must not turn a valid start into a 422."""
        monkeypatch.setattr(wds, "provider_view_of",
                            lambda pid: {"readable": False, "providers": {}})
        assert wds.unavailable_reviewer_provider_ids("flowgate", {"5": "aip_rev"}) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# P0007 — the route's ONE 422 envelope
# ══════════════════════════════════════════════════════════════════════════════════════

class _Req:
    """The two attributes the route actually reads off a Request."""

    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://127.0.0.1:8089/"


@pytest.fixture
def route_env(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(routes, "_require_user",
                        lambda request: {"issued_to": USER, "is_admin": True})
    monkeypatch.setattr(routes.db_projects, "get_by_id", lambda pid: {"project_name": "p"})
    monkeypatch.setattr(routes, "_continuation_target_error", lambda doc_ref, target: None)
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", lambda seq_id: _seq_items())
    monkeypatch.setattr(wds, "provider_view_of", lambda pid: {
        "readable": True, "providers": {p["id"]: p for p in PROVIDERS}})

    def _start_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "run_id": "run_x", "status": "running"}

    monkeypatch.setattr(routes.ai_invoke_service, "start_run", _start_run)
    return captured


def _post(**overrides):
    body = {
        "project": "flowgate", "module": "default", "group": "0414",
        "doc_ref": SPINE, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 9, "continuation_review_mode": False,
        "continuation_instruction_mode": "auto_approved",
    }
    body.update(overrides)
    response = routes.start_ai_invoke(routes.AiInvokeStartRequest(**body), _Req())
    return response.status_code, json.loads(bytes(response.body).decode("utf-8"))


class TestRouteContract:
    def test_valid_maps_reach_start_run_normalized(self, route_env):
        status, _ = _post(
            continuation_review_count_overrides={"1": 1, 3: 2, "5": 0},
            continuation_reviewer_overrides={"1": "aip_rev", "5": "aip_step5"})
        assert status == 200
        assert route_env["continuation_review_count_overrides"] == {"1": 1, "3": 2}
        # "5" had count 0, so its reviewer is an orphan and never reaches the engine.
        assert route_env["continuation_reviewer_overrides"] == {"1": "aip_rev"}

    def test_bad_value_is_one_validation_failed_envelope(self, route_env):
        status, payload = _post(continuation_review_count_overrides={"5": 4})
        assert status == 422
        assert payload["code"] == "validation_failed"
        assert payload["errors"] == [{
            "loc": "continuation_review_count_overrides",
            "msg": "invalid_review_count_value:4 — must be one of -1, 0, 1, 2, 3",
        }]

    def test_bad_reviewer_uses_its_own_loc(self, route_env):
        status, payload = _post(
            continuation_review_count_overrides={"5": 2},
            continuation_reviewer_overrides={"5": ""})
        assert status == 422
        assert payload["code"] == "validation_failed"
        assert payload["errors"][0]["loc"] == "continuation_reviewer_overrides"

    def test_ineligible_slot_is_the_same_envelope(self, route_env):
        status, payload = _post(continuation_review_count_overrides={"4": 1})
        assert status == 422
        assert payload["code"] == "validation_failed"
        assert payload["errors"][0]["msg"].startswith("ineligible_review_item_seq:4")

    def test_disabled_reviewer_gets_its_own_code(self, route_env):
        status, payload = _post(
            continuation_review_count_overrides={"5": 2},
            continuation_reviewer_overrides={"5": "aip_deleted"})
        assert status == 422
        assert payload == {
            "code": "reviewer_unavailable",
            "message": "The selected reviewer is not enabled for this project.",
            "reviewer_provider_ids": ["aip_deleted"],
        }

    def test_a_refused_request_never_reaches_start_run(self, route_env):
        _post(continuation_review_count_overrides={"5": 4})
        assert route_env == {}, "a 422 must not mint a token or create a scratch dir"

    def test_single_mode_drops_the_maps_instead_of_refusing_them(self, route_env):
        status, _ = _post(mode="single", action_scope="new",
                          continuation_target_seq=None,
                          continuation_review_count_overrides={"5": 2},
                          continuation_reviewer_overrides={"5": "aip_deleted"})
        assert status == 200
        assert route_env["continuation_review_count_overrides"] is None
        assert route_env["continuation_reviewer_overrides"] is None

    def test_workflow_decide_drops_the_maps_instead_of_refusing_them(self, route_env):
        status, _ = _post(action_scope="workflow_decide", continuation_target_seq=-1,
                          continuation_review_count_overrides={"5": 4})
        assert status == 200, "there are no item_seqs before the decision — nothing to refuse"
        assert route_env["continuation_review_count_overrides"] is None

    def test_omitting_both_maps_passes_none(self, route_env):
        status, _ = _post()
        assert status == 200
        assert route_env["continuation_review_count_overrides"] is None
        assert route_env["continuation_reviewer_overrides"] is None

    def test_the_model_does_not_narrow_the_dict_types(self):
        """P0007: narrowing would split the error envelope in two — FastAPI's own for a bad
        value, validation_failed for everything else — and force clients to parse both."""
        fields = routes.AiInvokeStartRequest.model_fields
        for name in ("continuation_review_count_overrides", "continuation_reviewer_overrides"):
            assert str(fields[name].annotation) in (
                "typing.Optional[dict]", "Optional[dict]", "dict | None")


# ══════════════════════════════════════════════════════════════════════════════════════
# L0008 §2.3 — gate derivation
# ══════════════════════════════════════════════════════════════════════════════════════

# Captured BEFORE any fixture can wrap it. World.reject models a rejection that already
# happened on an EARLIER gate pass, so it must not show up in this pass's instrumentation.
_REAL_AUTO_REJECT = svc._auto_reject


class World:
    """The four facts §2.3 derives everything from, and nothing else.

    0458 NR0003: "nothing else" grew one member. `rejection_history` is the accumulated
    record of which review rows were already rejected, and unlike doc_review_status it is
    not erased by ('rejected', 'submit') -> 'revised'. So this World keeps documents as
    STATE, not as constants: the rejections it records are written by the real
    pipeline_service writer and read back by the real gate.
    """

    def __init__(self):
        self.items = _seq_items()
        self.docs: dict[str, dict] = {}
        self.reviews: dict[str, list[dict]] = {}

    def fill(self, item_seq, doc_id, *, status="pending_review", revision_no=0,
             doc_type=None):
        for row in self.items:
            if row["item_seq"] == item_seq:
                row["result_doc_id"] = doc_id
                row["result_doc_review_status"] = status
                doc_type = doc_type or row["type"]
        self.docs[doc_id] = {"doc_id": doc_id, "type_code": doc_type, "branch": "main",
                             "doc_review_status": status, "revision_no": revision_no,
                             "rejection_history": None}
        return self

    # ── the documents table, as far as db_docs.update is concerned ─────────────
    def update_doc(self, doc_id, updates):
        """db_docs.update: merge and hand the merged row back, like the real one."""
        doc = self.docs.get(doc_id)
        if doc is None:
            return None
        doc.update(updates)
        for row in self.items:
            if row.get("result_doc_id") == doc_id:
                row["result_doc_review_status"] = doc["doc_review_status"]
        return dict(doc)

    def history(self, doc_id) -> list:
        """rejection_history as the gate reads it — through the production parser."""
        return svc._parse_rejection_history(self.docs[doc_id].get("rejection_history"))

    def set_history(self, doc_id, raw):
        """Plant a stored history directly — for the legacy/malformed shapes only."""
        self.docs[doc_id]["rejection_history"] = (
            raw if isinstance(raw, (str, type(None))) else json.dumps(raw, ensure_ascii=False))
        return self

    # ── the two writes that are NOT this gate's own pass ───────────────────────
    def reject(self, doc_id, *, api_base_url=API_BASE):
        """The automatic rejection an EARLIER gate pass already performed.

        Goes through the real svc._auto_reject -> transition_document_review, so the item
        this World accumulates is the item production stores — review_id and all. Nothing
        here re-implements the shape the assertions then read back.
        """
        slot = svc._pending_review_slot(SPINE)
        assert slot is not None and slot["doc_id"] == doc_id
        result = _REAL_AUTO_REJECT(slot, svc._latest_review_of(slot),
                                   bundle(api_base_url=api_base_url))
        assert result.get("ok") is True, result
        return self

    def mark_revised(self, doc_id):
        """A human pressing [수정요청 취소]: ('rejected','mark_revised') -> 'pending_review'.

        The revision does NOT move, so the rework never landed — only the status guard is
        gone. NR0003 I5: the review that was already applied must still not come back.
        """
        pipeline_service.transition_document_review(
            doc_id=doc_id, action="mark_revised", actor_user_id=USER,
            user_permissions={"document.update", "own.draft"})
        return self

    def review(self, doc_id, verdict, *, revision_no=0, findings=None, comment=None):
        """Newest first, exactly like document_reviews.list_by_doc."""
        self.reviews.setdefault(doc_id, []).insert(0, {
            "id": len(self.reviews.get(doc_id, [])) + 1,
            "doc_id": doc_id, "verdict": verdict, "revision_no": revision_no,
            "findings": json.dumps(findings or []), "comment": comment,
        })
        return self

    def rework(self, doc_id, revision_no, *, status="revised"):
        """A rework hop landing: the worker re-submits the rejected document, so the
        revision goes up and ('rejected', 'submit') -> 'revised' (transition_rules)."""
        self.docs[doc_id]["revision_no"] = revision_no
        self.docs[doc_id]["doc_review_status"] = status
        for row in self.items:
            if row.get("result_doc_id") == doc_id:
                row["result_doc_review_status"] = status
        return self


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda seq_id: list(w.items))
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: w.docs.get(doc_id))
    # svc.db_docs IS modules.flow_gate.db.documents, the same module pipeline_service holds,
    # so the real transition_document_review reads and writes THIS World. That is what lets
    # the tests below assert on a rejection_history production actually produced.
    monkeypatch.setattr(svc.db_docs, "update", lambda doc_id, updates: w.update_doc(doc_id, updates))
    monkeypatch.setattr(pipeline_service, "log_state_changed", lambda **kw: None)
    monkeypatch.setattr(db_users, "get_by_id",
                        lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(svc.db_reviews, "list_by_doc",
                        lambda doc_id: [dict(r) for r in w.reviews.get(doc_id, [])])
    monkeypatch.setattr(svc.db_reviews, "get_latest_by_doc",
                        lambda doc_id: dict(w.reviews[doc_id][0]) if w.reviews.get(doc_id) else None)
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective",
                        lambda pid: {"ok": True, "providers": list(PROVIDERS)})
    return w


def bundle(**overrides):
    base = {
        "doc_ref": SPINE, "target_seq": 9, "issued_to": USER, "api_base_url": API_BASE,
        "locale": "ko", "instruction_mode": "auto_approved",
        "review_count_overrides": {"5": 2}, "reviewer_overrides": {"5": "aip_rev"},
    }
    base.update(overrides)
    return base


class TestGateDerivation:
    def test_no_filled_slot_is_the_old_flow(self, world):
        assert svc.resolve_review_gate(bundle()) == {"stage": "work"}

    def test_an_approved_newest_slot_is_the_old_flow(self, world):
        world.fill(5, "doc-5", status="approved")
        assert svc.resolve_review_gate(bundle()) == {"stage": "work"}

    def test_count_zero_approves_and_continues(self, world):
        world.fill(5, "doc-5")
        gate = svc.resolve_review_gate(bundle(review_count_overrides=None))
        assert gate["stage"] == "work" and gate["approve_first"] is True
        assert gate["count"] == 0

    def test_first_pass_launches_review_round_one(self, world):
        world.fill(5, "doc-5")
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "review"
        assert (gate["round_no"], gate["limit"], gate["count"]) == (1, 2, 2)
        assert gate["slot"]["doc_id"] == "doc-5"

    def test_pass_approves_and_advances(self, world):
        world.fill(5, "doc-5").review("doc-5", "pass")
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "work" and gate["approve_first"] is True
        assert "reject_first" not in gate

    def test_hold_stops_the_chain(self, world):
        world.fill(5, "doc-5").review("doc-5", "hold")
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "stop"
        assert gate["stop_code"] == svc.REVIEW_VERDICT_HOLD_STOP_CODE
        assert svc.is_resumable(gate["stop_code"]) is False

    def test_count_one_issues_rejects_reworks_then_advances(self, world):
        """0414 M0020 / CH0019: the count is a budget of review+rework PAIRS, so the LAST
        round's issues is not just recorded — it is rejected, handed to the step's own
        worker, and the fix that comes back is what the chain carries on with.

        This is the exact case the memo refused: one review, one complaint, no fix."""
        world.fill(5, "doc-5").review("doc-5", "issues")
        gate = svc.resolve_review_gate(bundle(review_count_overrides={"5": 1}))
        assert gate["stage"] == "rework" and gate["round_no"] == 1
        assert gate["reject_first"] is True, "the rejection still happens"

        world.rework("doc-5", 1)
        gate = svc.resolve_review_gate(bundle(review_count_overrides={"5": 1}))
        assert gate["stage"] == "work" and gate["approve_first"] is True
        assert "reject_first" not in gate, "the reworked revision is approved, not rejected"
        assert gate["rounds_used"] == 1

    def test_count_two_issues_rejects_then_reworks(self, world):
        world.fill(5, "doc-5").review("doc-5", "issues")
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework"
        assert gate["reject_first"] is True and gate["round_no"] == 1

    def test_an_already_rejected_document_is_not_rejected_twice(self, world):
        world.fill(5, "doc-5", status="rejected").review("doc-5", "issues")
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework"
        assert gate.get("reject_first") is False

    def test_a_landed_rework_earns_the_next_review_round(self, world):
        """0458 NR0003 §7(a): a landed rework leaves `revised`, never `rejected` —
        ('rejected','submit') -> 'revised' is exactly what the re-submission does. Pinning
        `rejected` here was the one value that kept the duplicate-rejection guard closed,
        so this test walked through the defective branch and saw nothing."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.reject("doc-5")
        world.rework("doc-5", 1)
        assert world.docs["doc-5"]["doc_review_status"] == "revised"
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "review" and gate["round_no"] == 2
        assert "reject_first" not in gate, (
            "I2: reaching this branch IS the proof the complaint was rejected and fixed")

    def test_count_three_runs_three_review_rework_pairs_then_advances(self, world):
        """검수1 → 수정1 → 검수2 → 수정2 → 검수3 → 수정3 → 다음 단계 (M0020 / CH0019)."""
        counts = {"5": 3}
        world.fill(5, "doc-5")
        for round_no in (1, 2, 3):
            world.review("doc-5", "issues", revision_no=round_no - 1,
                         findings=[{"locus": f"§{round_no}", "note": f"issue {round_no}"}])
            # The third round's issues used to stop the chain right here, with its findings
            # recorded and never fixed. It now earns its rework like every other round.
            gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
            assert gate["stage"] == "rework" and gate["round_no"] == round_no
            assert gate["reject_first"] is True, "every review row earns its own rejection"
            world.reject("doc-5")
            world.rework("doc-5", round_no)          # the REAL landing: 'revised', rev + 1
            if round_no < 3:
                nxt = svc.resolve_review_gate(bundle(review_count_overrides=counts))
                assert nxt["stage"] == "review" and nxt["round_no"] == round_no + 1
                assert "reject_first" not in nxt, "the fix that landed is not rejected again"
        gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
        assert gate["stage"] == "work" and gate["approve_first"] is True
        assert gate["rounds_used"] == 3
        assert [item["review_id"] for item in world.history("doc-5")] == [1, 2, 3], (
            "three review rows, three rejections, one each (NR0003 I1)")

    def test_a_finite_budget_advances_only_after_the_last_fix_landed(self, world):
        """The pair is review+rework, so the step does NOT advance while the last round's
        rework is still owed — the gate asks for that rework first (positive control for
        the advance above)."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.reject("doc-5")
        world.rework("doc-5", 1)
        world.review("doc-5", "issues", revision_no=1)
        gate = svc.resolve_review_gate(bundle())          # count 2, both rounds spent
        assert gate["stage"] == "rework" and gate["round_no"] == 2
        assert gate["reject_first"] is True, "round 2 is a NEW review row, so it is rejected"
        world.reject("doc-5")
        world.rework("doc-5", 2)
        assert svc.resolve_review_gate(bundle())["stage"] == "work"
        assert len(world.history("doc-5")) == 2

    def test_unbounded_never_stops_on_a_round_count(self, world):
        """0414 0022-TR 반려: -1 이면 통과 할때까지 무한으로 돌아야 한다.

        Thirty review+rework rounds in — three times the ceiling this used to have — the
        gate is still running the loop and has never once handed the chain to a human."""
        counts = {"5": -1}
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        for round_no in range(1, 31):
            world.review("doc-5", "issues", revision_no=round_no - 1,
                         findings=[{"locus": f"§{round_no}", "note": f"issue {round_no}"}])
            gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
            assert gate["stage"] == "rework" and gate["round_no"] == round_no, (
                f"round {round_no}'s findings must earn their fix")
            world.rework("doc-5", round_no)
            gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
            assert gate["stage"] == "review", f"round {round_no} must earn another review"
            assert gate["round_no"] == round_no + 1
            assert gate["limit"] == svc.REVIEW_ROUNDS_NO_LIMIT

    def test_unbounded_ends_when_the_document_finally_passes(self, world):
        """The exit -1 does have. A pass approves the document and the chain moves on,
        whatever round it lands on — which is the whole meaning of "until it passes"."""
        counts = {"5": -1}
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        for round_no in range(1, 13):
            world.review("doc-5", "issues", revision_no=round_no - 1,
                         findings=[{"locus": f"§{round_no}", "note": f"issue {round_no}"}])
            world.rework("doc-5", round_no)
        assert svc.resolve_review_gate(
            bundle(review_count_overrides=counts))["stage"] == "review"
        world.review("doc-5", "pass", revision_no=12)
        gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
        assert gate["stage"] == "work" and gate["approve_first"] is True
        assert gate["rounds_used"] == 13

    def test_unbounded_still_stops_when_the_loop_stops_moving(self, world):
        """The ceiling is gone, but the loop breakers are not: a rework that raised no new
        revision still parks the chain. "Forever" means until it passes, not busy-looping
        on a step that cannot change."""
        counts = {"5": -1}
        world.fill(5, "doc-5", status="rejected", revision_no=3)
        world.review("doc-5", "issues", revision_no=3)
        gate = svc.resolve_review_gate(bundle(review_count_overrides=counts,
                                              last_stage="rework", revision_before=3))
        assert gate["stop_code"] == svc.REVIEW_STALLED_STOP_CODE

    def test_the_target_slot_is_reviewed_like_any_other(self, world):
        """L0008 §2.7 마지막 칸도 검수한다 — the target check comes after approval, and
        approval comes after the pass."""
        world.fill(8, "doc-8")
        gate = svc.resolve_review_gate(
            bundle(target_seq=8, review_count_overrides={"8": 1}))
        assert gate["stage"] == "review" and gate["slot"]["item_seq"] == 8

    def test_a_step_the_user_did_not_pick_is_not_gated(self, world):
        world.fill(7, "doc-7")
        gate = svc.resolve_review_gate(bundle())     # only step 5 was picked
        assert gate["stage"] == "work" and gate["approve_first"] is True


class TestLoopBreakers:
    def test_a_review_hop_that_left_no_verdict_stops_and_is_resumable(self, world):
        world.fill(5, "doc-5")
        gate = svc.resolve_review_gate(bundle(last_stage="review", rounds_before=0))
        assert gate["stop_code"] == svc.REVIEW_NO_VERDICT_STOP_CODE
        assert svc.is_resumable(svc.REVIEW_NO_VERDICT_STOP_CODE) is True

    def test_without_that_check_the_gate_would_relaunch_the_review(self, world):
        """The positive control: same state, cold start — a review hop IS the right answer
        there, which is exactly why the check has to be scoped to a live loop."""
        world.fill(5, "doc-5")
        assert svc.resolve_review_gate(bundle())["stage"] == "review"

    def test_a_rework_that_raised_no_revision_stops(self, world):
        world.fill(5, "doc-5", status="rejected", revision_no=3)
        world.review("doc-5", "issues", revision_no=3)
        gate = svc.resolve_review_gate(bundle(last_stage="rework", revision_before=3))
        assert gate["stop_code"] == svc.REVIEW_STALLED_STOP_CODE

    def test_the_same_findings_twice_stops(self, world):
        same = [{"locus": "§2.1", "note": "the same complaint"}]
        world.fill(5, "doc-5", status="rejected", revision_no=2)
        world.review("doc-5", "issues", revision_no=0, findings=same)
        world.review("doc-5", "issues", revision_no=1, findings=same)
        gate = svc.resolve_review_gate(
            bundle(review_count_overrides={"5": -1}, last_stage="rework", revision_before=1))
        assert gate["stop_code"] == svc.REVIEW_STALLED_STOP_CODE

    def test_different_findings_do_not_stop(self, world):
        world.fill(5, "doc-5", status="rejected", revision_no=2)
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§2.1", "note": "first"}])
        world.review("doc-5", "issues", revision_no=1,
                     findings=[{"locus": "§2.1", "note": "second, different"}])
        gate = svc.resolve_review_gate(
            bundle(review_count_overrides={"5": -1}, last_stage="rework", revision_before=1))
        assert gate["stage"] == "review"

    def test_the_digest_ignores_reflowed_whitespace(self):
        a = {"findings": json.dumps([{"locus": "§2.1", "note": "one  two\nthree"}])}
        b = {"findings": json.dumps([{"locus": " §2.1 ", "note": "one two three"}])}
        c = {"findings": json.dumps([{"locus": "§2.1", "note": "one two four"}])}
        assert svc.review_finding_digest(a) == svc.review_finding_digest(b)
        assert svc.review_finding_digest(a) != svc.review_finding_digest(c)

    def test_a_pending_question_holds_the_loop_without_a_review_stop(self, world):
        world.fill(5, "doc-5")
        gate = svc.resolve_review_gate(
            bundle(last_stage="review", rounds_before=0, last_stop_code="question_pending"))
        assert gate["stop_code"] == "question_pending"
        assert gate["stop_code"] not in svc.ENGINE_NOTIFY_STOP_CODES, (
            "waiting on a human answer is not a failure notification")

    def test_a_cold_start_skips_the_progress_check_entirely(self, world):
        """§2.3: [이어서 진행] after a restart has no previous hop to hold to account, and
        the DB derivation alone is already right for it."""
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        world.review("doc-5", "issues", revision_no=0)
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework"


class TestValueResolution:
    def test_count_lookup_accepts_both_key_spellings(self):
        assert svc.resolve_review_count({"5": 2}, 5) == 2
        assert svc.resolve_review_count({5: 2}, 5) == 2
        assert svc.resolve_review_count(None, 5) == 0
        assert svc.resolve_review_count({"5": 2}, 7) == 0
        assert svc.resolve_review_count({"5": 2}, None) == 0

    def test_a_hand_edited_out_of_range_count_reads_as_no_review(self):
        """The write path is 422-guarded, so this can only come from an edited row; it must
        degrade rather than crash an unmanned chain."""
        assert svc.resolve_review_count({"5": 4}, 5) == 0
        assert svc.resolve_review_count({"5": True}, 5) == 0

    def test_round_limit_marks_minus_one_unbounded(self):
        assert svc.resolve_round_limit(-1) == svc.REVIEW_ROUNDS_NO_LIMIT
        assert [svc.resolve_round_limit(n) for n in (1, 2, 3)] == [1, 2, 3]
        assert not hasattr(svc, "REVIEW_ROUNDS_UNBOUNDED_MAX"), (
            "0414 0022-TR \ubc18\ub824: -1 has no ceiling constant left to reach")

    def test_a_round_always_remains_for_an_unbounded_budget(self):
        assert svc.review_rounds_remain(10 ** 6, svc.REVIEW_ROUNDS_NO_LIMIT) is True
        assert svc.review_rounds_remain(0, svc.REVIEW_ROUNDS_NO_LIMIT) is True
        assert svc.review_rounds_remain(1, 2) is True
        assert svc.review_rounds_remain(2, 2) is False      # a finite budget does run out

    def test_neither_round_stop_code_is_emitted_but_both_explain_old_rows(self):
        """0414 M0020 + the 0022-TR rejection: the gate no longer stops on a round COUNT at
        all — a finite budget reworks its last round and advances, and -1 has no ceiling.
        Chains parked BEFORE those changes still carry both codes, so their sentences and
        their notifications have to survive the removal. The cap code additionally became
        resumable in the 0022-TR review rework (below): resolve_review_gate no longer has a
        round-count branch, so resuming one just re-derives the gate like a fresh -1 chain."""
        import inspect

        source = inspect.getsource(svc.resolve_review_gate)
        assert "REVIEW_EXHAUSTED_STOP_CODE" not in source
        assert "REVIEW_CAP_REACHED_STOP_CODE" not in source, (
            "no round count parks an unbounded chain any more")
        assert not hasattr(svc, "exhausted_stop_code"), (
            "the finite/ceiling pairing helper has no caller left")
        for code in (svc.REVIEW_EXHAUSTED_STOP_CODE, svc.REVIEW_CAP_REACHED_STOP_CODE):
            assert (svc._stop_reason_text(code, {}) or "").strip()
            assert code in svc.ENGINE_NOTIFY_STOP_CODES
        assert svc.is_resumable(svc.REVIEW_EXHAUSTED_STOP_CODE) is False
        assert svc.is_resumable(svc.REVIEW_CAP_REACHED_STOP_CODE) is True

    def test_a_resumed_cap_row_replays_as_an_ordinary_unbounded_round(self, world):
        """The cap code's resumability isn't just a flag flip: resuming one has to land on
        the same decision a fresh -1 chain would reach for the same DB state — nothing about
        a resume path is special-cased on the old stop code."""
        counts = {"5": -1}
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        world.review("doc-5", "issues", revision_no=0)
        gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
        assert gate["stage"] == "rework" and gate["round_no"] == 1, (
            "a legacy cap row's un-reworked last complaint still gets its rework hop")

    def test_reviewer_falls_back_to_the_project_default(self, world):
        assert svc.resolve_reviewer({"5": "aip_rev"}, 5, "flowgate") == "aip_rev"
        assert svc.resolve_reviewer(None, 5, "flowgate") == "aip_default"
        assert svc.resolve_reviewer({"5": "aip_gone"}, 5, "flowgate") == "aip_default"

    def test_the_step_executor_is_resolved_separately_from_the_reviewer(self, world):
        """L0008 §2.2: a rework is done by the step's own worker, not by its reviewer."""
        b = bundle(provider_overrides={"5": "aip_step5"}, reviewer_overrides={"5": "aip_rev"})
        assert svc.resolve_step_executor(b, 5, "flowgate", SPINE) == "aip_step5"
        assert svc.resolve_reviewer(b["reviewer_overrides"], 5, "flowgate") == "aip_rev"

    def test_the_executor_follows_start_runs_own_priority_order(self, world):
        assert svc.resolve_step_executor(
            bundle(provider_overrides={"5": "aip_step5"}, provider_pinned=True,
                   base_provider_id="aip_rev"), 5, "flowgate", SPINE) == "aip_step5"
        assert svc.resolve_step_executor(
            bundle(provider_overrides=None, provider_pinned=True,
                   base_provider_id="aip_rev"), 5, "flowgate", SPINE) == "aip_rev"
        # no override, no pin -> the row's stored assignment (step 5 holds aip_step5)
        assert svc.resolve_step_executor(
            bundle(provider_overrides=None, provider_pinned=False), 5, "flowgate",
            SPINE) == "aip_step5"
        # ...and finally the project default for a row with nothing stored
        assert svc.resolve_step_executor(
            bundle(provider_overrides=None, provider_pinned=False), 7, "flowgate",
            SPINE) == "aip_default"


# ══════════════════════════════════════════════════════════════════════════════════════
# L0008 §2.6 — the automatic rejection
# ══════════════════════════════════════════════════════════════════════════════════════

class TestAutoReject:
    def _slot(self):
        return {"item_seq": 5, "doc_id": "doc-5", "doc_type": "TR",
                "revision_no": 0, "review_status": "pending_review"}

    def test_the_reason_carries_comment_findings_and_the_full_review_url(self):
        review = {"comment": "구조가 계약과 어긋납니다.", "findings": json.dumps([
            {"locus": "§2.1", "note": "missing branch"},
            {"locus": "", "note": "no locus given"},
        ])}
        text = svc.build_auto_reject_reason(review, self._slot(), API_BASE)
        assert text.startswith(svc.REVIEW_REJECT_HEADING)
        assert "구조가 계약과 어긋납니다." in text
        assert "- §2.1: missing branch" in text
        assert f"- {svc.REVIEW_LOCUS_UNSPECIFIED}: no locus given" in text
        assert f"GET {API_BASE}/document/doc-5/reviews" in text

    def test_an_empty_issues_verdict_still_produces_a_non_empty_reason(self):
        """transition_document_review refuses a blank rejection reason, and an `issues`
        verdict is allowed to carry neither comment nor findings."""
        text = svc.build_auto_reject_reason({"comment": None, "findings": "[]"},
                                            self._slot(), API_BASE)
        assert text.strip()
        assert svc.REVIEW_REJECT_HEADING in text

    def test_findings_are_capped_and_the_overflow_is_announced(self):
        review = {"findings": json.dumps(
            [{"locus": f"§{i}", "note": "x"} for i in range(svc.REVIEW_REASON_MAX_FINDINGS + 5)])}
        text = svc.build_auto_reject_reason(review, self._slot(), API_BASE)
        assert text.count("\n- ") == svc.REVIEW_REASON_MAX_FINDINGS
        assert "5 further finding(s) omitted here." in text

    def test_over_length_is_trimmed_from_the_tail_so_the_heading_survives(self):
        review = {"comment": "가" * (svc.REVIEW_REASON_MAX_CHARS + 500), "findings": "[]"}
        text = svc.build_auto_reject_reason(review, self._slot(), API_BASE)
        assert len(text) == svc.REVIEW_REASON_MAX_CHARS
        assert text.startswith(svc.REVIEW_REJECT_HEADING)

    def test_the_cap_matches_the_response_it_shares_a_screen_with(self):
        from modules.flow_gate.workflow import pipeline_service

        assert svc.REVIEW_REASON_MAX_CHARS == pipeline_service.AI_RESPONSE_MAX_LEN

    def test_missing_reject_permission_stops_the_chain(self, monkeypatch):
        from modules.flow_gate.workflow.routers import workflow as wf_routes

        monkeypatch.setattr(wf_routes, "_get_user_permissions", lambda user: {"document.approve"})
        monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 0})
        result = svc._auto_reject(self._slot(), {"findings": "[]"}, bundle())
        assert result["ok"] is False
        assert result["stop_code"] == svc.REVIEW_REJECT_DENIED_STOP_CODE
        assert svc.is_resumable(svc.REVIEW_REJECT_DENIED_STOP_CODE) is False

    def test_a_failing_transition_stops_the_chain(self, monkeypatch):
        from modules.flow_gate.workflow import pipeline_service
        from modules.flow_gate.workflow.routers import workflow as wf_routes

        monkeypatch.setattr(wf_routes, "_get_user_permissions", lambda user: {"document.reject"})
        monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})

        def _boom(**kwargs):
            raise ValueError("Invalid review transition")

        monkeypatch.setattr(pipeline_service, "transition_document_review", _boom)
        result = svc._auto_reject(self._slot(), {"findings": "[]"}, bundle())
        assert result["ok"] is False
        assert result["stop_code"] == svc.REVIEW_REJECT_FAILED_STOP_CODE
        assert "Invalid review transition" in result["detail"]

    def test_a_successful_rejection_uses_the_single_writer(self, monkeypatch):
        from modules.flow_gate.workflow import pipeline_service
        from modules.flow_gate.workflow.routers import workflow as wf_routes

        seen: dict = {}
        monkeypatch.setattr(wf_routes, "_get_user_permissions", lambda user: {"document.reject"})
        monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
        monkeypatch.setattr(pipeline_service, "transition_document_review",
                            lambda **kw: seen.update(kw) or {"ok": True})
        assert svc._auto_reject(self._slot(), {"findings": "[]"}, bundle())["ok"] is True
        assert seen["action"] == "reject" and seen["doc_id"] == "doc-5"
        assert seen["actor_user_id"] == USER and seen["comment"]


# ══════════════════════════════════════════════════════════════════════════════════════
# L0008 §1.2 / §2.5 — stop codes, notification split, the engine's review clause
# ══════════════════════════════════════════════════════════════════════════════════════

class TestStopCodesAndMention:
    def test_all_seven_codes_notify_through_the_engine_only(self):
        assert svc.REVIEW_STOP_CODES <= svc.ENGINE_NOTIFY_STOP_CODES
        assert not (svc.ENGINE_NOTIFY_STOP_CODES & svc.INBOX_NOTIFY_STOP_CODES), (
            "a code in both sets would notify twice")

    def test_only_no_verdict_and_the_legacy_cap_are_resumable(self):
        resumable = {c for c in svc.REVIEW_STOP_CODES if svc.is_resumable(c)}
        assert resumable == {svc.REVIEW_NO_VERDICT_STOP_CODE, svc.REVIEW_CAP_REACHED_STOP_CODE}

    def test_every_code_has_an_english_sentence(self):
        hangul = [c for c in svc.REVIEW_STOP_CODES
                  if any("가" <= ch <= "힣" for ch in (svc._stop_reason_text(c, {}) or ""))]
        assert hangul == [], "server modules stay Korean-free (0430 TR0010 rev2)"
        for code in svc.REVIEW_STOP_CODES:
            assert (svc._stop_reason_text(code, {}) or "").strip()

    def test_the_ai_review_mode_stop_keeps_its_own_name(self):
        """L0008 §1.2 재사용 금지: review_hold is [AI 검토 모드]'s stop; a reviewer's hold
        VERDICT is a different event and gets its own code."""
        assert svc.REVIEW_VERDICT_HOLD_STOP_CODE != "review_hold"
        assert svc._stop_reason_text("review_hold", {}) == "Review mode: waiting for the human go."

    def test_the_engine_clause_states_the_consequence_and_the_budget(self):
        text = svc._append_engine_review_clause(
            "BASE", {"count": 2, "round_no": 1, "limit": 2})
        assert text.startswith("BASE")
        assert "round 1 of 2" in text
        assert "rejects the document automatically" in text
        unbounded = svc._append_engine_review_clause(
            "BASE", {"count": -1, "round_no": 3, "limit": svc.REVIEW_ROUNDS_NO_LIMIT})
        assert "until the document passes" in unbounded
        assert "no round ceiling" in unbounded
        assert "ceiling" not in unbounded.replace("no round ceiling", ""), (
            "the reviewer must not be told a round number ends this")

    def test_the_clause_no_longer_makes_the_fix_conditional(self):
        """0414 M0020: every round's findings are fixed, so the reviewer must not be told
        the hand-back only happens \"when a round remains\"."""
        for gate in ({"count": 2, "round_no": 1, "limit": 2},
                     {"count": 1, "round_no": 1, "limit": 1},
                     {"count": -1, "round_no": 3, "limit": svc.REVIEW_ROUNDS_NO_LIMIT}):
            text = svc._append_engine_review_clause("BASE", gate)
            assert "when a round remains" not in text
            assert "every round's findings get their fix" in text

    def test_the_last_finite_round_is_told_it_is_the_last(self):
        """Its findings produce a fix nobody reviews — the chain moves on with it — so that
        round has to know it is the last chance to name what still has to change."""
        last = svc._append_engine_review_clause(
            "BASE", {"count": 2, "round_no": 2, "limit": 2})
        assert "This is the LAST round" in last
        assert "without another review" in last
        not_last = svc._append_engine_review_clause(
            "BASE", {"count": 2, "round_no": 1, "limit": 2})
        assert "LAST round" not in not_last
        unbounded = svc._append_engine_review_clause(
            "BASE", {"count": -1, "round_no": 10, "limit": svc.REVIEW_ROUNDS_NO_LIMIT})
        assert "LAST round" not in unbounded, "-1 has no last round to announce"

    def test_build_review_mention_itself_is_untouched(self):
        """The human [멘트복사] path shares that builder, so the clause is appended by the
        engine and never baked into the shared text."""
        from modules.flow_gate.services import mention_service
        import inspect

        source = inspect.getsource(mention_service.build_review_mention)
        assert "Automated follow-up" not in source

    def test_the_shared_rework_issuer_exists_for_both_callers(self):
        """L0008 §2.6: kept in the route, the engine would grow a second copy of the
        rejection-history + edit-mention assembly, and two copies always drift."""
        import inspect

        assert callable(invoke_mention_service.build_rework_mention)
        assert callable(invoke_mention_service.issue_rework_request)
        route_source = inspect.getsource(routes.start_ai_invoke)
        assert "build_rework_mention" in route_source
        assert "build_rejection_section" not in route_source, (
            "the route must call the shared assembly, not re-implement it")
        gate_source = inspect.getsource(svc._spawn_rework_hop)
        assert "issue_rework_request" in gate_source


# ══════════════════════════════════════════════════════════════════════════════════════
# DB0009 I3 — the two maps survive every carrier
# ══════════════════════════════════════════════════════════════════════════════════════

REVIEW_KWARGS = ("continuation_review_count_overrides", "continuation_reviewer_overrides")


class FakePausedStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[dict] = []

    def upsert(self, **kw):
        self.calls.append(dict(kw))
        row = {
            "id": 1, "mode": "continuous",
            **{k: v for k, v in kw.items() if not k.startswith("continuation_")},
            "continuation_target_seq": kw.get("continuation_target_seq"),
            "continuation_base_provider_id": kw.get("continuation_base_provider_id"),
            "continuation_provider_pinned": bool(kw.get("continuation_provider_pinned")),
            "continuation_provider_overrides": db_paused.dump_json_map(
                kw.get("continuation_provider_overrides")),
            "continuation_default_note": kw.get("continuation_default_note"),
            "continuation_note_overrides": db_paused.dump_json_map(
                kw.get("continuation_note_overrides")),
            "continuation_instruction_mode": kw.get("continuation_instruction_mode"),
            "continuation_auto_approve_item_seqs": db_paused.dump_json_list(
                kw.get("continuation_auto_approve_item_seqs")),
            "continuation_step_timeout_sec": kw.get("continuation_step_timeout_sec"),
            "continuation_restart_max_attempts": kw.get("continuation_restart_max_attempts"),
            # Stored as normalized TEXT, exactly like the real columns, so the read path is
            # exercised through the production decoder.
            "continuation_review_count_overrides": db_paused.dump_json_map(
                kw.get("continuation_review_count_overrides")),
            "continuation_reviewer_overrides": db_paused.dump_json_map(
                kw.get("continuation_reviewer_overrides")),
        }
        self.rows[kw["group_id"]] = row

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row else None

    def exists(self, group_id):
        return group_id in self.rows

    def delete_and_return(self, group_id):
        row = self.rows.pop(group_id, None)
        return dict(row) if row else None

    def release_owned(self, group_id, *, paused_by, paused_at, stop_kind, stop_run_id):
        # 0459 TR0008 rev1: resume_chain() consumes via this CAS predicate, tied to
        # the exact row its ownership check read, instead of a bare group-only
        # delete_and_return(group_id).
        row = self.rows.get(group_id)
        if row is None:
            return None
        normalized = stop_kind or "user"
        if (row.get("paused_by") != paused_by
                or row.get("paused_at") != paused_at
                or (row.get("stop_kind") or "user") != normalized
                or row.get("stop_run_id") != stop_run_id):
            return None
        return self.rows.pop(group_id)

    def delete_by_group(self, group_id):
        self.rows.pop(group_id, None)

    def delete_system_stop(self, group_id, stop_run_id):
        row = self.rows.get(group_id)
        if row and row.get("stop_kind") == "system" and row.get("stop_run_id") == stop_run_id:
            self.rows.pop(group_id, None)

    def list_by_user(self, user_id):
        return [dict(r) for r in self.rows.values() if r.get("paused_by") == user_id]


@pytest.fixture
def paused(monkeypatch):
    store = FakePausedStore()
    for name in ("upsert", "get_by_group", "exists", "delete_and_return", "release_owned",
                 "delete_by_group", "delete_system_stop", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(store, name))
    return store


def _run(**overrides):
    run = {
        "run_id": "run_20260822_0001", "group_id": GROUP, "doc_ref": SPINE, "mode": "continuous",
        "issued_to": USER, "docs_target": 6, "docs_reached": 1, "chain_id": "run_chain",
        "chain_docs_target": 6, "chain_docs_reached": 1, "chain_docs_accounted": True,
        "continuation_target_seq": 9, "continuation_instruction_mode": "auto_approved",
        "continuation_auto_approve_item_seqs": [], "continuation_step_timeout_sec": 10800,
        "continuation_provider_overrides": {"5": "aip_step5"},
        "continuation_base_provider_id": "aip_default", "continuation_provider_pinned": True,
        "continuation_default_note": None, "continuation_note_overrides": None,
        "continuation_review_count_overrides": {"5": 2, "7": 3},
        "continuation_reviewer_overrides": {"5": "aip_rev"},
        "continuation_locale": "ko", "api_base_url": API_BASE,
        "last_message": None, "status": "finished", "end_reason": "exited",
        "stop_code": None, "resumable": True, "finished_at": "2026-08-22T10:00:00+09:00",
        "pause_requested": False,
    }
    run.update(overrides)
    return run


def _pending():
    return {"doc_ref": SPINE, "target_seq": 9, "review_mode": False,
            "instruction_mode": "auto_approved", "auto_approve_item_seqs": [],
            "locale": "ko", "issued_to": USER, "api_base_url": API_BASE}


class TestCarriers:
    def test_the_upsert_signature_names_both_columns(self):
        import inspect

        params = inspect.signature(db_paused.upsert).parameters
        for name in REVIEW_KWARGS:
            assert name in params, f"{name} must be an explicit upsert argument (DB0009 §4-1)"

    def test_every_paused_row_writer_passes_both(self):
        """DB0009 I3, enforced structurally. The upsert overwrites EVERY column, so a single
        caller that forgets these two wipes the selection and the chain resumes with the gate
        switched off — L0008 R1 broken from outside the schema."""
        offenders = []
        for path in (
            _SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke_service.py",
            _SERVER_DIR / "modules" / "flow_gate" / "api" / "inbox_routes.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "upsert":
                    continue
                # db_runs.upsert writes the run-record table, which has nothing to do with
                # the selection columns — scope this to the paused-chain module.
                if "paused" not in getattr(node.func.value, "id", ""):
                    continue
                named = {kw.arg for kw in node.keywords if kw.arg}
                missing = [k for k in REVIEW_KWARGS if k not in named]
                if missing:
                    offenders.append(f"{path.name}:{node.lineno} missing {missing}")
        assert offenders == [], "\n".join(offenders)

    def test_the_conflict_update_lets_the_incoming_value_win(self):
        """DB0009 §4-1: deliberately NOT COALESCE(excluded.x, table.x). A new run started
        with review switched OFF must clear an older row's selection, or the gate keeps
        firing from a stale row the user already changed their mind about."""
        import inspect

        source = inspect.getsource(db_paused.upsert)
        for name in REVIEW_KWARGS:
            assert f"{name} = excluded.{name}" in source
            assert f"COALESCE(excluded.{name}" not in source

    def test_user_pause_snapshots_both_maps(self, paused, monkeypatch):
        run = _run(status="running", mode="continuous")
        monkeypatch.setattr(svc, "_runs", {run["run_id"]: run})
        monkeypatch.setattr(svc, "_oracle_new_docs", lambda r: [])
        svc.pause_run(run["run_id"], USER)
        row = paused.rows[GROUP]
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}
        assert json.loads(row["continuation_reviewer_overrides"]) == {"5": "aip_rev"}

    def test_the_post_pause_refresh_does_not_erase_them(self, paused, monkeypatch):
        run = _run(status="running")
        monkeypatch.setattr(svc, "_runs", {run["run_id"]: run})
        monkeypatch.setattr(svc, "_oracle_new_docs", lambda r: [])
        svc.pause_run(run["run_id"], USER)
        # _apply_stop_row ALWAYS runs right after a pause and overwrites every column.
        svc._apply_stop_row(_run(end_reason="user_paused"), respawn_pending=False)
        row = paused.rows[GROUP]
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}

    def test_a_system_stop_row_carries_them_too(self, paused):
        """§4.3 / DB0009 W4: policy, not preference — a system stop that dropped these
        would resume the chain unreviewed."""
        svc._apply_stop_row(_run(end_reason="exited", stop_code="timeout", resumable=True),
                            respawn_pending=False)
        row = paused.rows[GROUP]
        assert row["stop_kind"] == "system"
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}
        assert json.loads(row["continuation_reviewer_overrides"]) == {"5": "aip_rev"}

    def test_the_hop_handoff_row_carries_them(self, paused):
        """DB0009 W2 — this one runs on EVERY hop, so a miss here loses the selection from
        the second hop onward."""
        svc._write_handoff_row(GROUP, _pending(), _run())
        row = paused.rows[GROUP]
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}

    def test_a_review_hops_own_run_cannot_wipe_the_queued_bundle(self, paused):
        """The single-mode trap: a review hop's run holds None for every continuous-only
        field. If the durable row took those, the chain would review step 5 and then never
        review again."""
        queued = {**_pending(), "review_count_overrides": {"5": 2, "7": 3},
                  "reviewer_overrides": {"5": "aip_rev"},
                  "provider_overrides": {"5": "aip_step5"}, "step_timeout_sec": 10800}
        review_run = _run(mode="single", continuation_review_count_overrides=None,
                          continuation_reviewer_overrides=None,
                          continuation_provider_overrides=None,
                          continuation_step_timeout_sec=None)
        svc._write_handoff_row(GROUP, queued, review_run)
        row = paused.rows[GROUP]
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}
        assert json.loads(row["continuation_provider_overrides"]) == {"5": "aip_step5"}
        assert row["continuation_step_timeout_sec"] == 10800

    def test_resume_hands_both_maps_back_to_start_run(self, paused, monkeypatch, world):
        svc._write_handoff_row(GROUP, _pending(), _run())
        captured: dict = {}
        monkeypatch.setattr(svc, "_runs", {})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})
        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert captured["continuation_review_count_overrides"] == {"5": 2, "7": 3}
        assert captured["continuation_reviewer_overrides"] == {"5": "aip_rev"}

    def test_a_failed_resume_restores_both_maps(self, paused, monkeypatch, world):
        svc._write_handoff_row(GROUP, _pending(), _run())
        monkeypatch.setattr(svc, "_runs", {})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        def _boom(**kw):
            raise HTTPException(status_code=409, detail={"code": "run_in_progress"})

        monkeypatch.setattr(svc, "start_run", _boom)
        with pytest.raises(HTTPException):
            svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        row = paused.rows[GROUP]
        assert json.loads(row["continuation_review_count_overrides"]) == {"5": 2, "7": 3}
        assert json.loads(row["continuation_reviewer_overrides"]) == {"5": "aip_rev"}

    def test_a_disabled_reviewer_is_dropped_on_resume_but_the_count_survives(
            self, paused, monkeypatch, world):
        svc._write_handoff_row(GROUP, _pending(), _run(
            continuation_reviewer_overrides={"5": "aip_gone", "7": "aip_rev"}))
        captured: dict = {}
        monkeypatch.setattr(svc, "_runs", {})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})
        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert captured["continuation_reviewer_overrides"] == {"7": "aip_rev"}
        assert captured["continuation_review_count_overrides"] == {"5": 2, "7": 3}, (
            "the review itself is never dropped — only the dead pick")
        assert svc.resolve_reviewer(
            captured["continuation_reviewer_overrides"], 5, "flowgate") == "aip_default"

    def test_corrupt_stored_json_degrades_to_none_instead_of_blocking_the_resume(
            self, paused, monkeypatch, world):
        svc._write_handoff_row(GROUP, _pending(), _run())
        paused.rows[GROUP]["continuation_review_count_overrides"] = "{not json"
        captured: dict = {}
        monkeypatch.setattr(svc, "_runs", {})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})
        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert captured["continuation_review_count_overrides"] is None

    def test_the_next_work_hop_carries_them_forward(self, monkeypatch, world):
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})
        svc._spawn_auto_resume(GROUP, {
            **_pending(),
            "review_count_overrides": {"5": 2}, "reviewer_overrides": {"5": "aip_rev"},
        })
        assert captured["continuation_review_count_overrides"] == {"5": 2}
        assert captured["continuation_reviewer_overrides"] == {"5": "aip_rev"}


# ══════════════════════════════════════════════════════════════════════════════════════
# flowgate.default.0466 T0007 A11 — [이어서 진행] on a review_no_verdict / cap_reached park
# must re-derive and re-launch the review/rework gate COLD, not the plain "new" advance
# path — advance_workflow refuses a still-pending_review head with head_in_progress, so
# resume_chain has to detect that shape itself before ever reaching _issue_resume.
# ══════════════════════════════════════════════════════════════════════════════════════

class TestResumeReviewGateDispatch0466:
    @pytest.fixture(autouse=True)
    def _quiet_resettle_surfaces(self, monkeypatch):
        """0466 x 0458 merge: `_park_handoff` now re-decides the run's stop through
        `_resettle_stop_after_park` (0458 T0007 2.1), which also refreshes the persisted
        run record and re-broadcasts the finished payload. The tests in this class hand
        `_park_handoff` a deliberately partial run dict because they assert ONE surface --
        the durable paused row / the notify scope -- so the other two are stubbed out here.
        Those two surfaces have their own end-to-end coverage in
        TestGateStopReachesEverySurfaceAfterFinalize below, which drives a full run.
        """
        monkeypatch.setattr(svc, "_persist_run_record", lambda run: None)
        monkeypatch.setattr(svc, "finished_payload", lambda run: {})
        monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)

    def _seed_park(self, monkeypatch, paused, *, last_stage, rounds_before=None,
                   revision_before=None, doc_ref="doc-5", stop_code=None):
        """Park a row exactly the way run_review_gate really does — through the REAL
        _park_handoff/_write_handoff_row — so the resume test exercises the same row shape
        production code writes, not a hand-built fixture row."""
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        stop_code = stop_code or svc.REVIEW_NO_VERDICT_STOP_CODE
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref=doc_ref, last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None)
        extra = {"last_stage": last_stage}
        if rounds_before is not None:
            extra["rounds_before"] = rounds_before
        if revision_before is not None:
            extra["revision_before"] = revision_before
        svc._park_handoff(run, bundle(**extra), stop_code)
        assert paused.rows[GROUP]["stop_code"] == stop_code
        return run

    def test_a_no_verdict_park_resumes_into_round_two_of_the_same_review(
            self, world, paused, monkeypatch):
        """A11-1/A11-3: round 1's issues+rework already landed (revision 1); round 2's
        review hop left no verdict. [이어서 진행] must relaunch round_no=2 on the SAME
        document/revision — never a 3rd round, never a plain 'new' advance."""
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        self._seed_park(monkeypatch, paused, last_stage=svc.REVIEW_HOP_KIND, rounds_before=1)

        spawned = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: spawned.update(bundle=dict(b), gate=dict(gate)) or {"ok": True})
        monkeypatch.setattr(
            svc, "_spawn_rework_hop",
            lambda g, b, gate: pytest.fail("a no-verdict review park must not dispatch rework"))
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        result = svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)

        assert result == {"ok": True}
        assert spawned["gate"]["round_no"] == 2
        assert spawned["gate"]["rounds_used"] == 1
        assert spawned["bundle"]["last_stage"] == svc.REVIEW_HOP_KIND
        assert spawned["bundle"]["rounds_before"] == 1
        assert spawned["bundle"]["doc_ref"] == SPINE
        # The old review_no_verdict stop row was CAS-consumed; `_queue_gate_bundle` (the
        # same "intent, then launch" ordering `run_review_gate` itself uses) wrote a fresh
        # queuing row in its place — not the stale stop, a hop-handoff-shaped one.
        assert paused.rows[GROUP]["stop_code"] != svc.REVIEW_NO_VERDICT_STOP_CODE

    def test_a_round_one_no_verdict_park_resumes_into_round_one_not_two(
            self, world, paused, monkeypatch):
        """Cold-derive from the DB alone (no last_stage/rounds_before restored): a FIRST
        review round that left no verdict must still relaunch round_no=1, count=2 unspent."""
        world.fill(5, "doc-5")               # no review rows at all yet
        self._seed_park(monkeypatch, paused, last_stage=svc.REVIEW_HOP_KIND, rounds_before=0)

        spawned = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: spawned.update(bundle=dict(b), gate=dict(gate)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert spawned["gate"]["round_no"] == 1
        assert spawned["gate"]["rounds_used"] == 0

    def test_a_pending_rework_resumes_via_the_rework_hop_not_a_fresh_advance(
            self, world, paused, monkeypatch):
        """The legacy REVIEW_CAP_REACHED_STOP_CODE path can cold-resolve to ANY stage — this
        proves the dispatch is not hard-coded to review-only: an outstanding 'issues' verdict
        with no rework landed yet must relaunch the REWORK hop instead."""
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        world.review("doc-5", "issues", revision_no=0)
        self._seed_park(monkeypatch, paused, last_stage=svc.REWORK_HOP_KIND, revision_before=0,
                        stop_code=svc.REVIEW_CAP_REACHED_STOP_CODE)

        spawned = {}
        monkeypatch.setattr(
            svc, "_spawn_rework_hop",
            lambda g, b, gate: spawned.update(bundle=dict(b), gate=dict(gate)) or {"ok": True})
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: pytest.fail("an unreworked issues verdict must dispatch rework"))
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        result = svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert result == {"ok": True}
        assert spawned["bundle"]["last_stage"] == svc.REWORK_HOP_KIND
        assert spawned["bundle"]["revision_before"] == 0

    def test_a_resumed_rework_hop_carries_the_restart_max_attempts_pick_to_start_run(
            self, world, paused, monkeypatch):
        """0476 NR0003 defect1 / T0005 end-to-end: a rework hop parked mid-chain must not
        lose the "재실행 횟수" pick across a cold resume_chain() dispatch. Mirrors
        test_spawn_auto_resume_does_not_crash_on_the_resumed_bundle's two-step shape
        (dispatch, then feed the dispatched bundle to the real hop spawner), applied to
        the REWORK hop instead of the WORK hop."""
        world.fill(5, "doc-5", status="rejected", revision_no=0)
        world.review("doc-5", "issues", revision_no=0)
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None, continuation_restart_max_attempts=3)
        svc._park_handoff(
            run,
            bundle(last_stage=svc.REWORK_HOP_KIND, revision_before=0),
            svc.REVIEW_CAP_REACHED_STOP_CODE,
        )
        assert paused.rows[GROUP]["stop_code"] == svc.REVIEW_CAP_REACHED_STOP_CODE

        real_spawn_rework_hop = svc._spawn_rework_hop
        dispatched = {}
        monkeypatch.setattr(
            svc, "_spawn_rework_hop",
            lambda g, b, gate: dispatched.update(bundle=dict(b), gate=dict(gate)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        result = svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert result == {"ok": True}
        queued_bundle = dispatched["bundle"]
        gate = dispatched["gate"]
        assert queued_bundle["restart_max_attempts"] == 3

        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s", "mention": "M"})
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})

        # the real spawner, saved BEFORE it was stubbed above to intercept resume_chain's
        # own internal dispatch — svc._spawn_rework_hop is still that stub at this point.
        real_spawn_rework_hop(GROUP, queued_bundle, gate)
        assert captured["continuation_restart_max_attempts"] == 3
        assert svc._resolve_restart_max_attempts(3) == 4

    def test_the_review_and_reviewer_maps_reach_the_dispatched_gate_bundle(
            self, world, paused, monkeypatch):
        """A11-4: the paused row's [검수] policy must survive into the re-dispatched gate
        bundle exactly like the plain 'new' resume path already proves in TestCarriers —
        including the disabled-reviewer-degrades-but-count-survives edge (P0007 [엣지])."""
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None,
                   continuation_reviewer_overrides={"5": "aip_gone"})
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        svc._park_handoff(
            run,
            bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1,
                  reviewer_overrides={"5": "aip_gone"}),
            svc.REVIEW_NO_VERDICT_STOP_CODE,
        )

        spawned = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: spawned.update(bundle=dict(b), gate=dict(gate)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert spawned["bundle"]["review_count_overrides"] == {"5": 2}
        # The disabled reviewer is dropped from the MAP; the review itself is never dropped.
        assert spawned["bundle"]["reviewer_overrides"] is None
        assert svc.resolve_reviewer(
            spawned["bundle"]["reviewer_overrides"], 5, "flowgate") == "aip_default"

    def test_a_plain_work_hop_resume_is_unaffected(self, world, paused, monkeypatch):
        """Control: a chain paused with nothing pending review (e.g. no_output_exhausted on
        a WORK hop) must keep taking the ordinary advance_workflow 'new' path unchanged."""
        world.fill(5, "doc-5", status="approved", revision_no=1)   # already through the gate
        svc._write_handoff_row(GROUP, _pending(), _run())
        captured: dict = {}
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 7)
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: pytest.fail("nothing is pending review — must not dispatch"))

        result = svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        assert result == {"ok": True}
        assert captured["action_scope"] == "new"
        assert captured["mode"] == "continuous"

    def test_the_dispatched_gate_bundle_carries_target_seq_and_note_policy(
            self, world, paused, monkeypatch):
        """T0007 §3.3.1: the cold-resume gate bundle is not a one-shot dispatch payload — it
        is QUEUED (`_queue_gate_bundle`) and becomes the bundle `run_review_gate` reads back
        once the resumed hop records its verdict (§2.9, `_finalize_run` → auto-resume queue).
        `target_seq`/`note_overrides`/`default_note`/`restart_max_attempts` all have to be in
        it NOW, at dispatch time, not just the fields `resolve_review_gate` itself reads —
        the earlier shape of this code built the bundle by hand and left all four out."""
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None,
                   continuation_target_seq=9,
                   continuation_note_overrides={"5": "double-check the table"},
                   continuation_default_note="be terse",
                   continuation_restart_max_attempts=3)
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        svc._park_handoff(run, bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1),
                          svc.REVIEW_NO_VERDICT_STOP_CODE)

        dispatched = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: dispatched.update(bundle=dict(b)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)

        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        queued_bundle = dispatched["bundle"]
        assert queued_bundle["target_seq"] == 9
        assert queued_bundle["note_overrides"] == {"5": "double-check the table"}
        assert queued_bundle["default_note"] == "be terse"
        assert queued_bundle["restart_max_attempts"] == 3

    def test_the_resumed_bundle_survives_verdict_settlement_without_crashing(
            self, world, paused, monkeypatch):
        """The defect this closes did not fail at dispatch — `_spawn_review_hop` launched
        fine either way. It failed LATER: once the resumed round-2 review lands a verdict,
        `_finalize_run` re-reads the exact bundle queued at dispatch time and feeds it back
        into `run_review_gate` (§2.9). A `pass` verdict then routes it through
        `_settle_gate_pass` (reads `bundle.get("target_seq")`, silently None) and
        `_spawn_auto_resume` (reads `pending["target_seq"]` — a HARD index, so a missing key
        is not silently None, it is a `KeyError` that kills the chain outright). This test
        drives that exact sequence end to end instead of stopping at dispatch."""
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None,
                   continuation_target_seq=9,
                   continuation_note_overrides={"5": "double-check the table"},
                   continuation_default_note="be terse",
                   continuation_restart_max_attempts=3)
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        svc._park_handoff(run, bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1),
                          svc.REVIEW_NO_VERDICT_STOP_CODE)

        dispatched = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: dispatched.update(bundle=dict(b)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        queued_bundle = dispatched["bundle"]

        # The resumed round-2 review lands a `pass` verdict at revision 1.
        world.review("doc-5", "pass", revision_no=1)

        settled = {}
        monkeypatch.setattr(
            svc, "_settle_gate_pass",
            lambda g, slot, b, r: settled.update(bundle=dict(b)) or "continue")
        spawn_pending = {}
        monkeypatch.setattr(
            svc, "_spawn_auto_resume",
            lambda g, pending: spawn_pending.update(pending))

        started = svc.run_review_gate(GROUP, queued_bundle, run)
        assert started is True
        # `_settle_gate_pass` must see the real target, not a silently-None one.
        assert settled["bundle"]["target_seq"] == 9
        # `_spawn_auto_resume` must receive the same target_seq without a KeyError, plus the
        # note/restart policy the resumed chain is still carrying.
        assert spawn_pending["target_seq"] == 9
        assert spawn_pending["note_overrides"] == {"5": "double-check the table"}
        assert spawn_pending["default_note"] == "be terse"
        assert spawn_pending["restart_max_attempts"] == 3

    def test_spawn_auto_resume_does_not_crash_on_the_resumed_bundle(
            self, world, paused, monkeypatch):
        """Direct reproduction of the reported defect: `_spawn_auto_resume` hard-indexes
        `pending["target_seq"]` (not `.get`) — a bundle missing that key does not degrade,
        it raises `KeyError` and the chain dies. Calling it directly on the exact shape
        `run_review_gate`'s WORK_HOP_KIND branch builds (`{**bundle, "last_stage": ...}`)
        proves the resumed bundle survives that hard index."""
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None,
                   continuation_target_seq=9,
                   continuation_note_overrides={"5": "double-check the table"},
                   continuation_default_note="be terse",
                   continuation_restart_max_attempts=3)
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        svc._park_handoff(run, bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1),
                          svc.REVIEW_NO_VERDICT_STOP_CODE)

        dispatched = {}
        monkeypatch.setattr(
            svc, "_spawn_review_hop",
            lambda g, b, gate: dispatched.update(bundle=dict(b)) or {"ok": True})
        monkeypatch.setattr(svc, "_next_incomplete_item_seq", lambda doc_ref: 5)
        svc.resume_chain(group_id=GROUP, user_id=USER, api_base_url=API_BASE)
        queued_bundle = dispatched["bundle"]

        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw) or {"ok": True})

        svc._spawn_auto_resume(GROUP, {**queued_bundle, "last_stage": svc.WORK_HOP_KIND})
        assert captured["continuation_target_seq"] == 9
        assert captured["continuation_note_overrides"] == {"5": "double-check the table"}
        assert captured["continuation_default_note"] == "be terse"
        assert captured["continuation_restart_max_attempts"] == 3


# ══════════════════════════════════════════════════════════════════════════════════════
# flowgate.default.0466 T0007 A10 §3.2 — the review_no_verdict stop card's failure detail:
# the excerpt must not collapse to nothing just because last_message happens to be empty,
# which is exactly the usage-limit-crash shape T0007 names.
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReviewNoVerdictExcerpt0466:
    @pytest.fixture(autouse=True)
    def _quiet_resettle_surfaces(self, monkeypatch):
        """0466 x 0458 merge: `_park_handoff` now re-decides the run's stop through
        `_resettle_stop_after_park` (0458 T0007 2.1), which also refreshes the persisted
        run record and re-broadcasts the finished payload. The tests in this class hand
        `_park_handoff` a deliberately partial run dict because they assert ONE surface --
        the durable paused row / the notify scope -- so the other two are stubbed out here.
        Those two surfaces have their own end-to-end coverage in
        TestGateStopReachesEverySurfaceAfterFinalize below, which drives a full run.
        """
        monkeypatch.setattr(svc, "_persist_run_record", lambda run: None)
        monkeypatch.setattr(svc, "finished_payload", lambda run: {})
        monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)

    def _park(self, monkeypatch, paused, run_overrides):
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        base = dict(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None, attempts_used=2, exit_code=1)
        base.update(run_overrides)
        run = _run(**base)
        svc._park_handoff(run, bundle(last_stage=svc.REVIEW_HOP_KIND, rounds_before=1),
                          svc.REVIEW_NO_VERDICT_STOP_CODE)
        return paused.rows[GROUP]["stop_last_message_excerpt"]

    def test_last_message_wins_when_present(self, paused, monkeypatch):
        """The core is last_message, but the provider/attempt/exit head is ALWAYS present
        too (A10-3) — this is composition, not a bare substitution of the sources."""
        excerpt = self._park(monkeypatch, paused, {"last_message": "usage limit reached"})
        assert "usage limit reached" in excerpt
        assert "attempt 2" in excerpt

    def test_falls_back_to_the_last_attempts_own_failure_detail(self, paused, monkeypatch):
        """last_message is empty (the incident shape) and there is no current-attempt
        stderr/stdout/timeout either — the archived PREVIOUS attempt's own
        `_no_output_detail` (provider/exit/elapsed) is the next best diagnostic."""
        excerpt = self._park(monkeypatch, paused, {
            "fallback_history": [{"provider_id": "aip_rev", "reason": "no_output",
                                  "detail": "worker exited 1 after 12s without registering "
                                            "a document", "exit_code": 1}],
        })
        assert "worker exited 1 after 12s" in excerpt

    def test_falls_back_to_stderr_when_no_message_or_history(self, paused, monkeypatch):
        excerpt = self._park(monkeypatch, paused, {
            "stderr_tail": "Error: usage limit exceeded for this billing period",
        })
        assert "usage limit exceeded" in excerpt

    def test_the_final_attempts_stderr_outranks_an_earlier_archived_attempts_detail(
            self, paused, monkeypatch):
        """A10-3's real shape: TWO attempts happened (`fallback_history` holds attempt 1's
        entry, archived when the retry kicked in), and the SECOND — final, unretried —
        attempt is the one that actually hit the provider's usage limit, which lands in
        THIS run's own `stderr_tail`/`last_message`, not in the history entry. Returning
        history[-1] first (the earlier shape of this function) would hide the real error
        behind attempt 1's generic "worker exited ... without registering a document"
        sentence — exactly the regression T0007 rejected."""
        excerpt = self._park(monkeypatch, paused, {
            "fallback_history": [{"provider_id": "aip_rev", "reason": "no_output",
                                  "detail": "worker exited 1 after 12s without registering "
                                            "a document", "exit_code": 1}],
            "stderr_tail": "Error: usage limit exceeded for this billing period",
        })
        assert "usage limit exceeded" in excerpt
        assert "attempt 2" in excerpt
        assert "worker exited 1 after 12s" not in excerpt

    def test_falls_back_to_stdout_when_no_message_history_or_stderr(self, paused, monkeypatch):
        excerpt = self._park(monkeypatch, paused, {
            "stdout_tail": "provider reported: quota exhausted, try again later",
        })
        assert "quota exhausted" in excerpt

    def test_falls_back_to_timeout_diagnosis(self, paused, monkeypatch):
        excerpt = self._park(monkeypatch, paused, {
            "timeout_diagnosis": "no output for 900s before the watchdog killed the process",
        })
        assert "watchdog killed the process" in excerpt

    def test_last_resort_is_never_empty_and_names_provider_attempt_and_exit(
            self, paused, monkeypatch):
        """Nothing anywhere — the excerpt must still name provider/attempt/exit, never
        collapse to None (which would leave the card with only the generic stop sentence)."""
        excerpt = self._park(monkeypatch, paused, {
            "provider": {"id": "aip_rev", "name": "Codex GPT-5.6 Sol"},
            "continuation_selected_provider_name": "Codex GPT-5.6 Sol",
            "exit_code": 1,
        })
        assert excerpt is not None and excerpt.strip() != ""
        assert "Codex GPT-5.6 Sol" in excerpt
        assert "2" in excerpt          # attempts_used

    def test_a_non_review_stop_keeps_the_plain_last_message_excerpt(self, paused, monkeypatch):
        """The fallback chain is scoped to REVIEW_NO_VERDICT_STOP_CODE only — every other
        stop code keeps reading straight from last_message, unchanged."""
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        run = _run(mode="continuous", last_message=None,
                   stderr_tail="should not leak into a no_output_exhausted excerpt")
        svc._park_handoff(run, _pending(), "no_output_exhausted")
        assert paused.rows[GROUP]["stop_last_message_excerpt"] is None

    def test_the_started_providers_name_outranks_a_stale_chain_head_after_startup_fallback(
            self, paused, monkeypatch):
        """A10-5's real shape: an override-less review hop's chain head (aip_1) fails to
        start, `_execute_provider_chain` falls back and actually starts aip_2, and BOTH
        attempts run on aip_2. `continuation_selected_provider_name` is set once from
        chain[0] before the walk and is never updated afterward — it still reads "aip_1" —
        while `run["provider"]` was moved to aip_2 by the fallback (L2620-2626). The card
        must blame the provider that actually ran and produced this exit_code/attempts_used,
        not the one that never started."""
        excerpt = self._park(monkeypatch, paused, {
            "continuation_selected_provider_name": "aip_1",
            "continuation_selected_provider_id": "aip_1",
            "provider": {"id": "aip_2", "name": "aip_2"},
            "stderr_tail": "Error: usage limit exceeded for this billing period",
        })
        assert '"aip_2"' in excerpt
        assert "aip_1" not in excerpt

    def test_stderr_and_stdout_tails_are_redacted_before_reaching_the_card(
            self, paused, monkeypatch):
        """T0007 §3.2.3: stderr_tail/stdout_tail are the provider process's raw, unfiltered
        output, so nothing upstream scrubs them before they reach here. A provider that
        echoes its own outgoing Authorization header (or a bare Bearer token) on failure
        must not have that value ride verbatim into stop_last_message_excerpt."""
        excerpt = self._park(monkeypatch, paused, {
            "stderr_tail": "call failed: Authorization: Bearer sk-live-abcdef123456 rejected",
        })
        assert "sk-live-abcdef123456" not in excerpt
        assert "[redacted]" in excerpt

        excerpt2 = self._park(monkeypatch, paused, {
            "stdout_tail": "outgoing request used Bearer eyJhbGciOiJIUzI1NiJ9.secret.tok",
        })
        assert "eyJhbGciOiJIUzI1NiJ9.secret.tok" not in excerpt2
        assert "[redacted]" in excerpt2

    def test_last_message_is_redacted_too(self, paused, monkeypatch):
        """rev3 review finding: `last_message` was left out of rev2's redaction even though
        it is exactly as raw as stderr_tail/stdout_tail for a `claude`-kind attempt —
        `_recover_cli_last_message` sets it to the CLI's full trimmed stdout, not a parsed
        answer field (L3587-3588), so a provider echoing its own outgoing Authorization
        call lands the same way here as in the tail fields."""
        excerpt = self._park(monkeypatch, paused, {
            "last_message": "call failed: Authorization: Bearer sk-live-abcdef123456 rejected",
        })
        assert "sk-live-abcdef123456" not in excerpt
        assert "[redacted]" in excerpt

    def test_a_bare_unlabeled_raw_token_echo_is_redacted_too(self, paused, monkeypatch):
        """rev4 review finding #2: `_redact_secrets`'s two regexes only strip a token
        wearing an `Authorization:`/`Bearer ` label. The run's own raw task token reaches
        the provider process unlabeled, as the `FLOWGATE_TOKEN` env var (L3477) — a
        process that echoes its own environment or a bare env dump on failure leaks the
        value with neither label, past both regexes. `_known_run_raw_tokens` (built from
        every token `_note_issued_raw_token` saw this run issue) catches it by exact
        literal value instead, independent of any label."""
        excerpt = self._park(monkeypatch, paused, {
            "last_message": "env dump: FLOWGATE_TOKEN=tok_raw_9f8e7d6c5b4a3f2e1d rejected",
            "_issued_raw_tokens": {"tok_raw_9f8e7d6c5b4a3f2e1d"},
        })
        assert "tok_raw_9f8e7d6c5b4a3f2e1d" not in excerpt
        assert "[redacted]" in excerpt

    def test_an_earlier_attempts_reissued_token_is_redacted_from_the_archived_detail(
            self, paused, monkeypatch):
        """A review-hop retry may reissue a FRESH token for attempt 2 (`_prepare_retry_
        token`), so attempt 1's own raw token is no longer `run["raw_token"]` by the time
        this function runs — it only survives via `_note_issued_raw_token`'s running set.
        A leak of the OLDER, already-rotated token in the archived fallback detail must
        still be caught."""
        excerpt = self._park(monkeypatch, paused, {
            "fallback_history": [{
                "provider_id": "aip_rev", "reason": "no_output",
                "detail": "worker exited 1 after 12s without registering a document; "
                          "last message: FLOWGATE_TOKEN=tok_raw_attempt1_old leaked",
                "exit_code": 1,
            }],
            "raw_token": "tok_raw_attempt2_new",
            "_issued_raw_tokens": {"tok_raw_attempt1_old", "tok_raw_attempt2_new"},
        })
        assert "tok_raw_attempt1_old" not in excerpt
        assert "[redacted]" in excerpt

    def test_an_archived_earlier_attempts_message_is_redacted_when_it_becomes_the_core(
            self, paused, monkeypatch):
        """rev3: the fallback_history[-1] detail this function falls back to is
        `_no_output_detail`'s sentence (L3005-3016), which embeds attempt 1's own
        `last_message` verbatim — so a secret in an EARLIER, archived attempt's message
        must be caught here too, not just a secret in the current attempt's own fields."""
        excerpt = self._park(monkeypatch, paused, {
            "fallback_history": [{
                "provider_id": "aip_rev", "reason": "no_output",
                "detail": "worker exited 1 after 12s without registering a document; "
                          "last message: token leaked as Bearer eyJhbGciOiJIUzI1NiJ9.abc",
                "exit_code": 1,
            }],
        })
        assert "eyJhbGciOiJIUzI1NiJ9.abc" not in excerpt
        assert "[redacted]" in excerpt

    def test_a_short_full_prompt_echoed_verbatim_is_redacted(self, paused, monkeypatch):
        """rev5 review finding: neither regex nor `known_tokens` touches the PROMPT itself
        — T0007 §3.2.3 separately bans exposing it ("전체 프롬프트를 노출하지 마라"). A CLI
        worker that rejects its own stdin ("invalid input, got: <prompt>" is the shape a
        parse error takes) echoes this run's exact `run["mention"]` text back, and a SHORT
        prompt fits whole inside `LAST_MESSAGE_EXCERPT_BYTES` — nothing about the existing
        byte caps trims it away on its own."""
        prompt = ("@flowgate please review flowgate.default.0466.0005-T revision 3 against "
                  "the acceptance criteria in section 4 and record your verdict.")
        excerpt = self._park(monkeypatch, paused, {
            "last_message": f"error: could not parse instruction: {prompt}",
            "_issued_prompts": {prompt},
        })
        assert prompt not in excerpt
        assert "[redacted prompt]" in excerpt

    def test_a_truncated_prompt_echo_is_redacted_by_its_longest_present_prefix(
            self, paused, monkeypatch):
        """The provider's own output can get cut off before it finishes echoing stdin back
        — the leaked text is then only a PREFIX of the prompt, never a full literal match.
        Stdin is written to the child exactly once, front to back, so the longest prefix of
        the prompt still present verbatim is exactly what leaked, and it alone must go."""
        prompt = "line one of the instruction\n" + ("filler content " * 50) + "line last"
        leaked_prefix = prompt[:120]
        excerpt = self._park(monkeypatch, paused, {
            "stderr_tail": f"fatal: unexpected input near: {leaked_prefix}<TRUNCATED>",
            "_issued_prompts": {prompt},
        })
        assert leaked_prefix not in excerpt
        assert "[redacted prompt]" in excerpt

    def test_a_short_shared_prefix_below_the_floor_is_left_alone(self, paused, monkeypatch):
        """A few characters of accidental overlap between the prompt and an unrelated
        failure message ("the ", "please ") is not a genuine echo — redacting on that would
        mangle ordinary provider prose for no privacy benefit. `_PROMPT_ECHO_MIN_LEN` is the
        same floor `_BEARER_TOKEN_RE` already uses for a bare token."""
        excerpt = self._park(monkeypatch, paused, {
            "stderr_tail": "the request could not be completed: quota exceeded",
            "_issued_prompts": {"the request was very specific about formatting rules"},
        })
        assert "[redacted prompt]" not in excerpt
        assert "quota exceeded" in excerpt

    def test_an_earlier_attempts_rotated_prompt_is_redacted_from_the_archived_detail(
            self, paused, monkeypatch):
        """Mirrors `test_an_earlier_attempts_reissued_token_is_redacted_from_the_archived_
        detail`: a review-hop retry rebuilds `run["mention"]` in place for attempt 2
        (L3048), so attempt 1's own prompt text no longer lives there by the time this
        function runs — it only survives via `_note_issued_prompt`'s running set. A leak of
        that OLDER, already-rotated prompt in the archived fallback detail must still be
        caught."""
        old_prompt = "review flowgate.default.0466.0005-T revision 2, round 1"
        excerpt = self._park(monkeypatch, paused, {
            "fallback_history": [{
                "provider_id": "aip_rev", "reason": "no_output",
                "detail": f"worker exited 1 after 12s; rejected input: {old_prompt}",
                "exit_code": 1,
            }],
            "mention": "review flowgate.default.0466.0005-T revision 2, round 2",
            "_issued_prompts": {old_prompt,
                                "review flowgate.default.0466.0005-T revision 2, round 2"},
        })
        assert old_prompt not in excerpt
        assert "[redacted prompt]" in excerpt

    def test_a_full_prompt_echo_with_an_embedded_credential_is_fully_redacted(
            self, paused, monkeypatch):
        """rev6 review finding: an issued FlowGate prompt routinely embeds this run's own
        `Authorization: Bearer <token>` as task context. rev5 redacted headers/tokens FIRST
        and only then binary-searched the already-mutated string for a prefix of the
        ORIGINAL prompt — once the embedded credential was rewritten in place, no prefix of
        the original text extending past it could still match, so only the portion before
        the credential was recognized as the echoed prompt and erased; the confidential
        suffix that followed `Bearer <token>` in the prompt survived verbatim. The prompt
        search must run against the unmutated text so the whole echoed block — credential
        and all — is found and erased as one unit."""
        confidential_suffix = ("also attach the private acceptance-criteria notes for "
                               "0466 section 4 that must not leave this chain")
        prompt = (f"@flowgate please review flowgate.default.0466.0005-T using "
                  f"Authorization: Bearer tok_prompt_embedded_9f8e7d6c5b4a — "
                  f"{confidential_suffix}")
        excerpt = self._park(monkeypatch, paused, {
            "last_message": f"error: could not parse instruction: {prompt}",
            "_issued_prompts": {prompt},
        })
        assert confidential_suffix not in excerpt
        assert "tok_prompt_embedded_9f8e7d6c5b4a" not in excerpt
        assert "[redacted prompt]" in excerpt

    def test_an_overlapping_retained_prompt_pair_is_redacted_regardless_of_set_order(
            self, paused, monkeypatch):
        """rev7 review finding: `_issued_prompts` is a set, and a reissued retry commonly
        reuses the whole prior prompt text and appends more — so two RETAINED prompts can
        legitimately be one a literal prefix of the other. Iterating that set in whatever
        order Python happens to hash it into is not a security boundary: if the shorter
        prompt is processed first, its own binary search finds it fully present (it's a
        prefix of what actually echoed) and erases only that short span — leaving the
        confidential suffix that follows it in the longer prompt exposed, and the longer
        prompt's own turn then finds nothing left to match. Longest-first processing must
        erase the whole echoed block before the shorter prefix ever gets a turn, regardless
        of which order the set happens to yield the two prompts in."""
        confidential_suffix = ("also attach the private acceptance-criteria notes for 0466 "
                               "section 4 that must not leave this chain")
        short_prompt = ("@flowgate please review flowgate.default.0466.0005-T revision 3 "
                        "fully")
        long_prompt = f"{short_prompt} -- {confidential_suffix}"
        excerpt = self._park(monkeypatch, paused, {
            "last_message": f"error: could not parse instruction: {long_prompt}",
            "_issued_prompts": {short_prompt, long_prompt},
        })
        assert confidential_suffix not in excerpt
        assert short_prompt not in excerpt
        assert "[redacted prompt]" in excerpt


class TestReviewNoVerdictStopReasonAttempts0476:
    """flowgate.default.0476 T0007 §3: `stop_reason` on REVIEW_NO_VERDICT_STOP_CODE must let
    a human reading the record after the fact tell "both attempts ran and still left no
    verdict" apart from "the second attempt never opened because of budget/provider/token
    exhaustion" — same `blocked_text` composition `no_output_exhausted` already uses."""

    def test_both_attempts_used_with_no_block_reason_has_no_tail(self):
        run = _run(attempts_used=2, attempts_max=2, retry_block_reason=None)
        text = svc._stop_reason_text(svc.REVIEW_NO_VERDICT_STOP_CODE, run)
        assert "2 of 2 attempts used" in text
        assert "No further attempt was opened" not in text

    def test_a_budget_exhausted_block_reason_names_itself(self):
        run = _run(attempts_used=1, attempts_max=2, retry_block_reason="budget_exhausted")
        text = svc._stop_reason_text(svc.REVIEW_NO_VERDICT_STOP_CODE, run)
        assert "1 of 2 attempts used" in text
        assert "No further attempt was opened: budget_exhausted." in text

    def test_a_providers_exhausted_block_reason_names_itself(self):
        run = _run(attempts_used=1, attempts_max=2,
                   retry_block_reason="providers_exhausted_for_retry")
        text = svc._stop_reason_text(svc.REVIEW_NO_VERDICT_STOP_CODE, run)
        assert "1 of 2 attempts used" in text
        assert "No further attempt was opened: providers_exhausted_for_retry." in text

    def test_a_run_with_no_attempts_max_falls_back_to_the_fixed_constant(self):
        text = svc._stop_reason_text(svc.REVIEW_NO_VERDICT_STOP_CODE, {})
        assert svc.NO_OUTPUT_MAX_ATTEMPTS == 2
        assert "0 of 2 attempts used" in text


# ══════════════════════════════════════════════════════════════════════════════════════
# flowgate.default.0466 TR0008 rev8 review finding — `_park_handoff`'s notify call is
# scoped to REVIEW_NO_VERDICT_STOP_CODE. T0007 §3.2.5 only asks for a failure signal on
# that one stop code; the other six REVIEW_STOP_CODES are all members of
# ENGINE_NOTIFY_STOP_CODES too (L194-199), so calling `_notify_chain_failure_if_needed`
# unconditionally would also fire on review_verdict_hold/review_stalled/
# review_reject_denied/review_reject_failed — none of which T0007 asked for, and the
# first of which is "waiting on a human answer, not a failure" by the same reasoning
# `question_pending` is kept out of the notify set entirely.
# ══════════════════════════════════════════════════════════════════════════════════════

class TestParkHandoffNotifyScope0466:
    @pytest.fixture(autouse=True)
    def _quiet_resettle_surfaces(self, monkeypatch):
        """0466 x 0458 merge: `_park_handoff` now re-decides the run's stop through
        `_resettle_stop_after_park` (0458 T0007 2.1), which also refreshes the persisted
        run record and re-broadcasts the finished payload. The tests in this class hand
        `_park_handoff` a deliberately partial run dict because they assert ONE surface --
        the durable paused row / the notify scope -- so the other two are stubbed out here.
        Those two surfaces have their own end-to-end coverage in
        TestGateStopReachesEverySurfaceAfterFinalize below, which drives a full run.
        """
        monkeypatch.setattr(svc, "_persist_run_record", lambda run: None)
        monkeypatch.setattr(svc, "finished_payload", lambda run: {})
        monkeypatch.setattr(svc, "_broadcast", lambda *a, **kw: None)

    def _park_and_capture(self, monkeypatch, paused, stop_code):
        monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **kw: None)
        from modules.flow_gate.workflow import event_logger

        signals: list[dict] = []
        monkeypatch.setattr(event_logger, "log_continuous_work_failed",
                            lambda **kw: signals.append(kw) or {})
        run = _run(mode="single", action_scope="review", hop_kind=svc.REVIEW_HOP_KIND,
                   scope_oracle_run=True, project_id="flowgate",
                   doc_ref="doc-5", last_message=None, docs_target=0, docs_reached=0,
                   outcome="none", stop_code=None, attempts_used=2, exit_code=1)
        svc._park_handoff(run, _pending(), stop_code)
        return signals

    def test_review_no_verdict_still_notifies(self, paused, monkeypatch):
        signals = self._park_and_capture(monkeypatch, paused, svc.REVIEW_NO_VERDICT_STOP_CODE)
        assert len(signals) == 1

    def test_review_verdict_hold_does_not_notify(self, paused, monkeypatch):
        """The reviewer's own finding: `review_verdict_hold` is a human-decision stop, the
        same shape as `question_pending` (L4991-4993 "waiting on a human answer, not
        because it failed") — an unconditional notify call would wrongly fire on every
        ordinary reviewer hold."""
        signals = self._park_and_capture(monkeypatch, paused, svc.REVIEW_VERDICT_HOLD_STOP_CODE)
        assert signals == []

    def test_review_stalled_does_not_notify(self, paused, monkeypatch):
        signals = self._park_and_capture(monkeypatch, paused, svc.REVIEW_STALLED_STOP_CODE)
        assert signals == []

    def test_review_reject_denied_does_not_notify(self, paused, monkeypatch):
        signals = self._park_and_capture(monkeypatch, paused, svc.REVIEW_REJECT_DENIED_STOP_CODE)
        assert signals == []

    def test_review_reject_failed_does_not_notify(self, paused, monkeypatch):
        signals = self._park_and_capture(monkeypatch, paused, svc.REVIEW_REJECT_FAILED_STOP_CODE)
        assert signals == []


# ══════════════════════════════════════════════════════════════════════════════════════
# L0008 §2.4 — the gate's execution: queue first, then launch; park instead of advancing
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def gate_exec(monkeypatch, world):
    """Every side effect run_review_gate can have, captured instead of performed."""
    seen = {"parked": [], "queued": [], "review": [], "rework": [], "work": [],
            "rejected": [], "rejected_review_ids": [], "settled": []}
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda run, bundle, stop_code: seen["parked"].append(stop_code))
    monkeypatch.setattr(svc, "request_auto_resume",
                        lambda group_id, payload: seen["queued"].append(dict(payload)))
    monkeypatch.setattr(svc, "clear_auto_resume", lambda group_id: seen["queued"].clear())
    monkeypatch.setattr(svc, "_spawn_review_hop",
                        lambda g, b, gate: seen["review"].append((dict(b), gate)))
    monkeypatch.setattr(svc, "_spawn_rework_hop",
                        lambda g, b, gate: seen["rework"].append((dict(b), gate)))
    monkeypatch.setattr(svc, "_spawn_auto_resume",
                        lambda g, b: seen["work"].append(dict(b)))
    def _counted_auto_reject(slot, review, b):
        """Counted AND performed. 0458 NR0003 §7(b): the old double only counted, so a
        second rejection left no trace for the next gate call to see, and no test in this
        file could ever observe one review row being rejected twice."""
        seen["rejected"].append(slot["doc_id"])
        seen["rejected_review_ids"].append((review or {}).get("id"))
        return _REAL_AUTO_REJECT(slot, review, b)

    monkeypatch.setattr(svc, "_auto_reject", _counted_auto_reject)
    monkeypatch.setattr(svc, "_settle_gate_pass",
                        lambda g, slot, b, run: seen["settled"].append(slot["doc_id"])
                        or "continue")
    return seen


class TestGateExecution:
    def test_a_review_is_queued_before_it_is_launched(self, world, gate_exec):
        """§2.4 큐잉 순서: _finalize_run reads that queue to decide between begin_handoff and
        releasing the group lease. Launch first and the successor dies on 409."""
        world.fill(5, "doc-5")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert len(gate_exec["queued"]) == 1 and len(gate_exec["review"]) == 1
        queued = gate_exec["queued"][0]
        assert queued["last_stage"] == "review" and queued["rounds_before"] == 0
        assert queued["review_count_overrides"] == {"5": 2}

    def test_rounds_before_is_the_count_the_review_hop_has_to_beat(self, world, gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.reject("doc-5")                 # round 1's rejection, on the previous pass
        world.rework("doc-5", 1)              # ('rejected','submit') -> 'revised'
        svc.run_review_gate(GROUP, bundle(), _run())
        assert gate_exec["queued"][0]["rounds_before"] == 1
        assert gate_exec["rejected"] == [], "the landed fix is not rejected a second time"
        assert len(world.history("doc-5")) == 1

    def test_a_rework_records_the_revision_it_has_to_beat(self, world, gate_exec):
        world.fill(5, "doc-5", revision_no=2)
        world.review("doc-5", "issues", revision_no=2)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        queued = gate_exec["queued"][0]
        assert queued["last_stage"] == "rework" and queued["revision_before"] == 2
        assert len(gate_exec["rework"]) == 1
        assert gate_exec["rejected"] == ["doc-5"], "issues is rejected before the rework"

    def test_the_loop_markers_never_reach_the_durable_row(self, world, gate_exec):
        """§2.9: last_stage / rounds_before / revision_before live in the memory queue ONLY.
        A cold start after a restart must land on the DB-derivation path, where their
        absence is exactly the right answer."""
        world.fill(5, "doc-5")
        svc.run_review_gate(GROUP, bundle(), _run())
        import inspect

        source = inspect.getsource(svc._write_handoff_row)
        for marker in ("last_stage", "rounds_before", "revision_before"):
            assert marker not in source

    def test_a_pass_settles_through_the_shared_helper_then_runs_the_next_work_hop(
            self, world, gate_exec):
        world.fill(5, "doc-5").review("doc-5", "pass")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["settled"] == ["doc-5"]
        assert len(gate_exec["work"]) == 1
        assert gate_exec["work"][0]["last_stage"] == "work"
        assert gate_exec["parked"] == []

    def test_a_settle_that_does_not_continue_stops_the_chain_here(
            self, world, gate_exec, monkeypatch):
        world.fill(5, "doc-5").review("doc-5", "pass")
        monkeypatch.setattr(svc, "_settle_gate_pass", lambda g, s, b, r: "completed")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is False
        assert gate_exec["work"] == [], "the target was reached — no further hop"

    def test_an_ungated_step_takes_the_ordinary_work_path(self, world, gate_exec):
        world.fill(5, "doc-5")
        assert svc.run_review_gate(GROUP, bundle(review_count_overrides=None), _run()) is True
        assert len(gate_exec["work"]) == 1
        assert gate_exec["review"] == [] and gate_exec["rework"] == []

    def test_the_work_branch_does_not_requeue_itself(self, world, gate_exec):
        """Deliberate divergence from §2.4's uniform pseudocode. _finalize_run already ran
        begin_handoff for this boundary and the work hop's own inbox self-chain queues the
        hop after it; queueing here too would leave a live entry behind a hop that produced
        nothing, and the engine would respawn it forever instead of stopping on
        no_output_exhausted."""
        world.fill(5, "doc-5")
        svc.run_review_gate(GROUP, bundle(review_count_overrides=None), _run())
        assert gate_exec["queued"] == []

    def test_a_stop_verdict_parks_and_reports_that_nothing_started(self, world, gate_exec):
        world.fill(5, "doc-5").review("doc-5", "hold")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is False
        assert gate_exec["parked"] == [svc.REVIEW_VERDICT_HOLD_STOP_CODE]
        assert gate_exec["queued"] == []

    def test_the_last_round_rejects_and_still_launches_its_rework(self, world, gate_exec):
        """10-1: the rejection is independent of what comes next. 0414 M0020: what comes
        next is a rework even in the last round — the findings are fixed, not just filed."""
        world.fill(5, "doc-5").review("doc-5", "issues")
        assert svc.run_review_gate(
            GROUP, bundle(review_count_overrides={"5": 1}), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        assert len(gate_exec["rework"]) == 1
        assert gate_exec["parked"] == []

    def test_a_spent_budget_settles_the_reworked_revision_and_runs_the_next_hop(
            self, world, gate_exec):
        """The end of the last review+rework pair goes through the SAME settle helper a
        `pass` uses (§2.7), so approval, the target check and the boundary pause cannot
        drift between the two ways a step can finish."""
        world.fill(5, "doc-5", status="rejected")
        world.review("doc-5", "issues", revision_no=0)
        world.rework("doc-5", 1)
        assert svc.run_review_gate(
            GROUP, bundle(review_count_overrides={"5": 1}), _run()) is True
        assert gate_exec["settled"] == ["doc-5"]
        assert gate_exec["rejected"] == [], "the fix is approved, not rejected again"
        assert len(gate_exec["work"]) == 1 and gate_exec["work"][0]["last_stage"] == "work"
        assert gate_exec["parked"] == []

    def test_a_failed_rejection_parks_with_its_own_code_and_launches_nothing(
            self, world, gate_exec, monkeypatch):
        world.fill(5, "doc-5").review("doc-5", "issues")
        monkeypatch.setattr(svc, "_auto_reject", lambda slot, review, b: {
            "ok": False, "stop_code": svc.REVIEW_REJECT_DENIED_STOP_CODE,
            "detail": "issuer lacks document.reject"})
        run = _run()
        assert svc.run_review_gate(GROUP, bundle(), run) is False
        assert gate_exec["parked"] == [svc.REVIEW_REJECT_DENIED_STOP_CODE]
        assert gate_exec["rework"] == []
        assert run["review_reject_detail"] == "issuer lacks document.reject"
        assert "document.reject" in (
            svc._stop_reason_text(svc.REVIEW_REJECT_DENIED_STOP_CODE, run) or "")

    def test_a_launch_failure_takes_the_queued_intent_back_out(
            self, world, gate_exec, monkeypatch):
        """§2.4: otherwise the caller parks the chain while a live queue entry says a hop is
        coming, and _finalize_run would hold the group lease open for a hop that never ran."""
        world.fill(5, "doc-5")

        def _boom(group_id, b, gate):
            raise HTTPException(status_code=409, detail={"code": "no_enabled_provider"})

        monkeypatch.setattr(svc, "_spawn_review_hop", _boom)
        with pytest.raises(HTTPException):
            svc.run_review_gate(GROUP, bundle(), _run())
        assert gate_exec["queued"] == [], "the intent must not outlive the failed launch"

    def test_the_review_hop_is_bound_to_the_document_not_the_spine(self, world, gate_exec):
        world.fill(5, "doc-5")
        svc.run_review_gate(GROUP, bundle(), _run())
        _queued, gate = gate_exec["review"][0]
        assert gate["slot"]["doc_id"] == "doc-5"
        assert gate["slot"]["doc_id"] != SPINE


# ═════════════════════════════════════════════════════════════════════════════════════
# 0458 B0001 / NR0003 I1~I6 — one review row makes at most ONE rejection (R1~R10)
# ═════════════════════════════════════════════════════════════════════════════════════

class TestOneRejectionPerReviewRow:
    """B0001 "똑같은 메세지가 두번 반려됨" — the 0457 chain, made into a standing contract.

    Review row 244 was rejected twice: the rework landed, ('rejected','submit') -> 'revised'
    erased the only guard the gate had, and _latest_review_of handed the SAME row back to
    build_auto_reject_reason. The second rejection drove the document to `rejected`, so the
    next round's `pass` could not approve it and the chain parked on approve_failed.

    Everything here counts rejections rather than describing them: gate_exec's double both
    tallies and performs, so what the second gate call sees is what the first one wrote.
    """

    def _round_one(self, world, gate_exec, count=2):
        """R1's setup: issues -> auto-reject -> the fix lands as `revised` at revision 1."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "round one"}])
        assert svc.run_review_gate(
            GROUP, bundle(review_count_overrides={"5": count}), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        assert world.docs["doc-5"]["doc_review_status"] == "rejected"
        world.rework("doc-5", 1)
        return world.history("doc-5")

    # ── R1 ────────────────────────────────────────────────────────────────────
    def test_r1_the_landed_fix_is_not_rejected_again_and_earns_round_two(
            self, world, gate_exec):
        history = self._round_one(world, gate_exec)
        assert len(history) == 1 and history[0]["review_id"] == 1

        gate = svc.resolve_review_gate(bundle())
        assert (gate["stage"], gate["round_no"]) == ("review", 2)
        assert "reject_first" not in gate, "§8 방향 A: this branch decides the next round only"

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"], "still exactly one auto-rejection"
        assert len(world.history("doc-5")) == 1
        assert world.docs["doc-5"]["doc_review_status"] == "revised", (
            "I4: the phantom must not drive the reworked document back to `rejected`")

    # ── R2 ────────────────────────────────────────────────────────────────────
    def test_r2_round_twos_own_issues_are_rejected_on_their_own_review_id(
            self, world, gate_exec):
        """4-3: with the guard spent on a phantom, round two's REAL findings were never
        written to rejection_history at all, and the rework mention re-read round one."""
        self._round_one(world, gate_exec)
        svc.run_review_gate(GROUP, bundle(), _run())          # launches review round 2
        world.review("doc-5", "issues", revision_no=1,
                     findings=[{"locus": "§2", "note": "round two"}])

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5", "doc-5"]
        assert gate_exec["rejected_review_ids"] == [1, 2]
        history = world.history("doc-5")
        assert [item["review_id"] for item in history] == [1, 2], "I1: one item per row"
        assert "round two" in history[1]["reason"], "the NEW findings are what was recorded"

    # ── R3 ────────────────────────────────────────────────────────────────────
    def test_r3_a_pass_after_the_landed_fix_can_still_be_approved(self, world, gate_exec):
        """4-1, the actual stop: ('rejected','approve') is deliberately absent from the
        transition table, so a document the gate pushed back to `rejected` cannot approve
        its own passing review. Nothing pushes it there any more."""
        self._round_one(world, gate_exec)
        world.review("doc-5", "pass", revision_no=1)

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"], "no phantom ahead of the approval"
        status = world.docs["doc-5"]["doc_review_status"]
        assert status == "revised"
        assert get_doc_review_rule(status, "approve") == "approved"
        assert get_doc_review_rule("rejected", "approve") is None, (
            "the rule stays absent — the cause was fixed, not the table opened")
        assert gate_exec["settled"] == ["doc-5"] and gate_exec["parked"] == []

    # ── R4 ────────────────────────────────────────────────────────────────────
    def test_r4_a_cold_resume_after_a_landed_fix_does_not_re_reject(self, world, gate_exec):
        """_check_expected_progress is skipped without last_stage, so a cold [이어서 진행]
        used to walk straight into the same second rejection."""
        self._round_one(world, gate_exec)
        cold = bundle()
        assert "last_stage" not in cold

        gate = svc.resolve_review_gate(cold)
        assert (gate["stage"], gate["round_no"]) == ("review", 2)
        assert gate.get("reject_first") in (None, False)
        assert svc.run_review_gate(GROUP, cold, _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        assert len(world.history("doc-5")) == 1

    # ── R5 ────────────────────────────────────────────────────────────────────
    def test_r5_re_entering_the_same_boundary_twice_changes_nothing(self, world, gate_exec):
        """I6: same document, same review bundle, same answer. The auto-resume queue makes
        this rare, not impossible — and rare is not a property of the gate."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        reentered = bundle()
        assert svc.run_review_gate(GROUP, reentered, _run()) is True
        assert svc.run_review_gate(GROUP, reentered, _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        assert len(world.history("doc-5")) == 1
        assert len(gate_exec["rework"]) == 2, "the hop is re-launched; the rejection is not"

    # ── R6 ────────────────────────────────────────────────────────────────────
    def test_r6_a_complaint_whose_fix_has_not_landed_is_still_rejected(
            self, world, gate_exec):
        """The positive control. The fix removes a duplicate, never the first rejection."""
        world.fill(5, "doc-5", revision_no=2)
        world.review("doc-5", "issues", revision_no=2)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"] and len(gate_exec["rework"]) == 1
        history = world.history("doc-5")
        assert len(history) == 1 and history[0]["review_id"] == 1
        assert history[0]["reason"].startswith(svc.REVIEW_REJECT_HEADING)

    # ── R7 ────────────────────────────────────────────────────────────────────
    def test_r7_a_human_rejection_is_not_doubled_by_the_gate(self, world, gate_exec):
        """I5, first half — the contract test_an_already_rejected_document_is_not_rejected
        _twice states, now also asserted on the executing path."""
        world.fill(5, "doc-5", status="rejected")
        world.review("doc-5", "issues", revision_no=0)
        assert svc.resolve_review_gate(bundle()).get("reject_first") is False
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == [] and world.history("doc-5") == []

    # ── R8 ────────────────────────────────────────────────────────────────────
    def test_r8_a_human_mark_revised_does_not_re_open_an_applied_review(
            self, world, gate_exec):
        """I5, second half. The status guard is genuinely gone here — the document is back
        in `pending_review` and the revision never moved — so only the recorded review_id
        can answer, which is why the status-only guard could not."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]

        world.mark_revised("doc-5")
        assert world.docs["doc-5"]["doc_review_status"] == "pending_review"
        assert world.docs["doc-5"]["revision_no"] == 0, "no rework landed"

        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework"
        assert gate["reject_first"] is False
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        assert len(world.history("doc-5")) == 1

    # ── R9 ────────────────────────────────────────────────────────────────────
    def test_r9_three_rounds_leave_three_reviews_and_three_rejections(
            self, world, gate_exec):
        """검수 N회 = 검수+수정 N쌍, every landing through the real transition."""
        counts = {"5": 3}
        world.fill(5, "doc-5")
        for round_no in (1, 2, 3):
            world.review("doc-5", "issues", revision_no=round_no - 1,
                         findings=[{"locus": f"§{round_no}", "note": f"issue {round_no}"}])
            assert svc.run_review_gate(
                GROUP, bundle(review_count_overrides=counts), _run()) is True
            assert len(gate_exec["rejected"]) == round_no
            world.rework("doc-5", round_no)
            if round_no < 3:
                assert svc.run_review_gate(
                    GROUP, bundle(review_count_overrides=counts), _run()) is True
                assert len(gate_exec["rejected"]) == round_no, (
                    f"the fix for round {round_no} must not be rejected again")

        assert svc.run_review_gate(
            GROUP, bundle(review_count_overrides=counts), _run()) is True
        assert gate_exec["settled"] == ["doc-5"], "the last fix is approved, not rejected"
        assert gate_exec["rejected_review_ids"] == [1, 2, 3]
        assert [item["review_id"] for item in world.history("doc-5")] == [1, 2, 3]
        assert len(world.reviews["doc-5"]) == 3

    # ── R10 ───────────────────────────────────────────────────────────────────
    def test_r10_unbounded_rejects_once_per_round_and_keeps_reviewing(
            self, world, gate_exec):
        """-1 has no budget to run out of, so the guard must be per review row, not per run."""
        counts = {"5": -1}
        world.fill(5, "doc-5")
        for round_no in (1, 2, 3):
            world.review("doc-5", "issues", revision_no=round_no - 1,
                         findings=[{"locus": f"§{round_no}", "note": f"issue {round_no}"}])
            assert svc.run_review_gate(
                GROUP, bundle(review_count_overrides=counts), _run()) is True
            assert len(gate_exec["rejected"]) == round_no
            world.rework("doc-5", round_no)
            gate = svc.resolve_review_gate(bundle(review_count_overrides=counts))
            assert gate["stage"] == "review" and gate["round_no"] == round_no + 1
            assert gate["limit"] == svc.REVIEW_ROUNDS_NO_LIMIT
            assert "reject_first" not in gate

        assert gate_exec["rejected_review_ids"] == [1, 2, 3]
        assert [item["review_id"] for item in world.history("doc-5")] == [1, 2, 3]

    # ── R11 ───────────────────────────────────────────────────────────────────
    def test_r11_cold_resume_stops_when_review_history_is_unreadable(
            self, world, gate_exec, monkeypatch):
        world.fill(5, "doc-5", status="revised", revision_no=1)
        world.review("doc-5", "issues", revision_no=0)
        world.docs["doc-5"].update({"id": 55, "project_id": "flowgate", "group_id": GROUP})
        world.docs["doc-5"]["rejection_history"] = json.dumps([{
            "rejection_id": "rej_existing", "review_id": 1,
            "reason": svc.build_auto_reject_reason(world.reviews["doc-5"][0], {"doc_id": "doc-5"}, API_BASE),
        }])
        calls = {"n": 0}
        def unreadable(doc_id):
            calls["n"] += 1
            raise RuntimeError("pool warming")
        events = []
        monkeypatch.setattr(svc.db_reviews, "list_by_doc", unreadable)
        monkeypatch.setattr(svc, "_log_review_annotation_failure",
                            lambda kind, slot, bundle, error: events.append((kind, slot["doc_id"], str(error))))
        cold = bundle(group_id=GROUP)
        gate = svc.resolve_review_gate(cold)
        assert gate["stage"] == "stop"
        assert gate["stop_code"] == "review_history_unreadable"
        assert gate["detail"] == "pool warming"
        assert calls["n"] == 1
        assert events == [("read", "doc-5", "pool warming")]
        assert gate_exec["rejected"] == []
        assert len(world.history("doc-5")) == 1


class TestRejectionHistoryCompatibility:
    """The recorded identity (B), the legacy fallback (B'), and what neither may touch."""

    def test_the_writer_stores_exactly_the_review_id_it_is_given(self, world):
        world.fill(5, "doc-5")
        pipeline_service.transition_document_review(
            doc_id="doc-5", action="reject", actor_user_id=USER,
            user_permissions={"document.reject"},
            comment="## Automated review rejection", review_id=244)
        item = world.history("doc-5")[-1]
        assert item["review_id"] == 244
        assert item["rejection_id"] and item["ai_response"] is None
        assert world.docs["doc-5"]["doc_review_status"] == "rejected"

    def test_a_human_rejection_grows_no_new_key(self, world):
        """The [반려] button never passes review_id, so its item is the item it always was."""
        world.fill(5, "doc-5")
        pipeline_service.transition_document_review(
            doc_id="doc-5", action="reject", actor_user_id=USER,
            user_permissions={"document.reject"}, comment="본문을 다시 써 주세요")
        item = world.history("doc-5")[-1]
        assert set(item) == {"rejection_id", "reason", "rejected_at", "rejected_by",
                             "ai_response", "responded_at", "response_recorded_by",
                             "response_revision_no"}

    def _legacy_reason(self, world):
        slot = svc._pending_review_slot(SPINE)
        return svc.build_auto_reject_reason(svc._latest_review_of(slot), slot, API_BASE)

    def test_a_legacy_auto_rejection_without_the_key_is_matched_by_its_text(self, world):
        """B': build_auto_reject_reason is pure, so the row that produced a stored reason is
        recoverable from the reason itself. No backfill — derived at read time only."""
        world.fill(5, "doc-5", status="rejected")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "written before review_id existed"}])
        world.set_history("doc-5", [{"rejection_id": "old-1",
                                     "reason": self._legacy_reason(world)}])
        world.mark_revised("doc-5")               # the guard the old code relied on is gone
        assert world.docs["doc-5"]["doc_review_status"] == "pending_review"
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is False

    def test_a_human_written_reason_never_looks_like_an_applied_review(self, world):
        """4-4: 34 operational documents repeat a short human reason round after round.
        Matching on text alone would call every one of them a duplicate."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "real"}])
        world.set_history("doc-5", [{"rejection_id": "old-1", "reason": "테스트 실패"}])
        assert svc.resolve_review_gate(bundle())["reject_first"] is True

    def test_b_prime_never_reaches_an_item_that_has_a_review_id(self, world, gate_exec):
        """Two DIFFERENT review rows whose reasons are byte-identical are two complaints,
        and each is rejected once. Suppressing the second by text is exactly how round two's
        real findings would be swallowed."""
        findings = [{"locus": "§1", "note": "the same words in both rounds"}]
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0, findings=findings)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        world.rework("doc-5", 1)
        svc.run_review_gate(GROUP, bundle(), _run())
        world.review("doc-5", "issues", revision_no=1, findings=findings)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True

        history = world.history("doc-5")
        assert len(history) == 2
        assert history[0]["reason"] == history[1]["reason"], "identical text, on purpose"
        assert [item["review_id"] for item in history] == [1, 2]

    def test_a_string_review_id_matches_the_integer_row_it_came_from(self, world):
        """The id round-trips through JSON, so 244 and "244" have to be one key."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.set_history("doc-5", [{"rejection_id": "r", "reason": "x", "review_id": "1"}])
        assert svc.resolve_review_gate(bundle())["reject_first"] is False
        world.set_history("doc-5", [{"rejection_id": "r", "reason": "x", "review_id": "9"}])
        assert svc.resolve_review_gate(bundle())["reject_first"] is True

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_an_unidentified_review_id_key_never_swallows_a_new_review(self, world, empty):
        """B' is for items that LACK the key, not for items whose key came back empty.

        An item written with review_id: null identifies no review row, so it must match
        nothing at all. Reading the VALUE instead of the key's presence dropped such an item
        into B', where a different review row rendering the same text would be suppressed and
        its complaint lost (T0005 2.3.1/2.3.3)."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "a brand new complaint"}])
        world.set_history("doc-5", [{"rejection_id": "old-1",
                                     "reason": self._legacy_reason(world),
                                     "review_id": empty}])
        assert svc.resolve_review_gate(bundle())["reject_first"] is True, (
            "the stored text is identical, but that item names no row")

    def test_two_unidentified_keys_are_not_read_as_the_same_review(self, world):
        """An empty key on both sides is still two unknowns, never one match."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.reviews["doc-5"][0].pop("id")
        world.set_history("doc-5", [{"rejection_id": "r", "reason": "x", "review_id": None}])
        assert svc.resolve_review_gate(bundle())["reject_first"] is True

    @pytest.mark.parametrize("raw", ["{not json", '{"a": 1}', "", "null", "[1, 2, 3]",
                                     "[]", None])
    def test_a_malformed_history_degrades_to_the_status_check(self, world, raw):
        """An unreadable column weakens the guard back to what it was; it never breaks the
        gate, because a parked chain would be a worse failure than a duplicate item."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.docs["doc-5"]["rejection_history"] = raw
        assert svc._pending_review_slot(SPINE)["rejection_history"] == []
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is True

    def test_a_review_row_without_an_id_is_written_without_the_key(self, world, gate_exec):
        """§2.3.3 defensive input: _auto_reject's contract does not change shape when the
        review it is handed is unusable."""
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        world.reviews["doc-5"][0].pop("id")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert gate_exec["rejected"] == ["doc-5"]
        item = world.history("doc-5")[-1]
        assert "review_id" not in item
        assert svc._review_already_rejected(None, {"doc_id": "doc-5",
                                                   "rejection_history": []}, API_BASE) is False


class TestHopLaunchContract:
    """§2.5/§2.6 — the two hops' start_run arguments, which are what make them judgeable."""

    @pytest.fixture
    def launched(self, monkeypatch, world):
        calls: list[dict] = []
        monkeypatch.setattr(svc, "start_run", lambda **kw: calls.append(kw) or {"ok": True})
        return calls

    def _gate(self, world):
        world.fill(5, "doc-5")
        return svc.resolve_review_gate(bundle())

    def test_the_review_hop_gets_the_review_probe_and_no_continuation_token(
            self, world, launched, monkeypatch):
        monkeypatch.setattr(wds, "request_review", lambda **kw: {
            "token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s", "mention": "BASE"})
        svc._spawn_review_hop(GROUP, bundle(chain_id="run_chain", chain_docs_target=6,
                                            chain_docs_reached=2, step_timeout_sec=10800),
                              self._gate(world))
        kw = launched[0]
        assert (kw["mode"], kw["action_scope"]) == ("single", "review"), (
            "this pair is what selects _probe_doc_reviews as the judge")
        assert svc._uses_scope_oracle("review", "single", None) is True
        assert kw["continuation_target_seq"] is None, (
            "no continuation token: a verdict must not be able to approve its own target")
        assert kw["doc_ref"] == "doc-5"
        assert kw["provider_id"] == "aip_rev"
        assert kw["hop_kind"] == svc.REVIEW_HOP_KIND
        # the chain identity and its counters survive a mode="single" hop
        assert kw["chain_id"] == "run_chain" and kw["chain_docs_reached"] == 2
        assert kw["continuation_step_timeout_sec"] == 10800
        assert kw["continuation_review_count_overrides"] == {"5": 2}

    def test_the_review_hop_budget_follows_the_chain_not_the_single_run_formula(self):
        assert svc._resolve_timeout_sec("single", 0, False, 10800,
                                        svc.REVIEW_HOP_KIND) == 10800
        assert svc._resolve_timeout_sec("single", 0, False, None,
                                        svc.REVIEW_HOP_KIND) == svc.HOP_TIMEOUT_SEC
        # This line used to read "an ordinary single run is untouched: 1 document ->
        # RUN_TIMEOUT_BASE_SEC", and it was true on 0414's branch base. 0446 T0010 3-1
        # (merged 2026-08-22, one day BEFORE d4d9656 was authored on a base that predated
        # it) then moved the explicit pick ABOVE the mode branch for every mode, because a
        # rejection rework otherwise bottomed out at exactly 3600 and no screen could ask
        # for more. So a single run carrying a pick now follows the pick too -- the merge,
        # not a regression, is what made the old assertion false.
        assert svc._resolve_timeout_sec("single", 1, False, 10800) == 10800
        # What is still this hop's own contract is the NO-pick default: the single-run
        # formula for an ordinary run, HOP_TIMEOUT_SEC for the review hop (asserted above).
        assert svc._resolve_timeout_sec("single", 1, False, None) == svc.RUN_TIMEOUT_BASE_SEC

    def test_the_rework_hop_runs_as_the_step_executor_with_the_revision_probe(
            self, world, launched, monkeypatch):
        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s", "mention": "M"})
        world.fill(5, "doc-5", status="rejected", revision_no=1)
        world.review("doc-5", "issues", revision_no=1)
        gate = svc.resolve_review_gate(bundle())
        svc._spawn_rework_hop(GROUP, bundle(provider_overrides={"5": "aip_step5"}), gate)
        kw = launched[0]
        assert (kw["mode"], kw["action_scope"]) == ("single", "edit"), (
            "the edit token scope is also what selects _probe_doc_revision as the judge")
        assert svc._uses_scope_oracle("edit", "single", None) is True
        assert kw["provider_id"] == "aip_step5", "the author fixes it, not the reviewer"
        assert kw["hop_kind"] == svc.REWORK_HOP_KIND
        assert kw["doc_ref"] == "doc-5"

    def test_the_rework_hop_carries_the_restart_max_attempts_pick_to_start_run(
            self, world, launched, monkeypatch):
        """0476 NR0003 defect1 / T0005: `_spawn_rework_hop` must forward the bundle's
        `restart_max_attempts` to `start_run` exactly like `_spawn_auto_resume` already
        does, and `_resolve_restart_max_attempts` must turn a pick of 3 restarts into a
        total attempts ceiling of 4 (1 initial + 3 restarts)."""
        monkeypatch.setattr(invoke_mention_service, "issue_rework_request", lambda **kw: {
            "raw_token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s", "mention": "M"})
        world.fill(5, "doc-5", status="rejected", revision_no=1)
        world.review("doc-5", "issues", revision_no=1)
        gate = svc.resolve_review_gate(bundle())
        svc._spawn_rework_hop(GROUP, bundle(restart_max_attempts=3), gate)
        kw = launched[0]
        assert kw["continuation_restart_max_attempts"] == 3
        assert svc._resolve_restart_max_attempts(3) == 4

    def test_the_review_hop_appends_its_clause_to_the_shared_mention(
            self, world, launched, monkeypatch):
        monkeypatch.setattr(wds, "request_review", lambda **kw: {
            "token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s",
            "mention": "SHARED REVIEW MENTION"})
        svc._spawn_review_hop(GROUP, bundle(), self._gate(world))
        issued = launched[0]["issue_builder"](ai_run_id="run_1")
        assert issued["mention"].startswith("SHARED REVIEW MENTION")
        assert "Automated follow-up" in issued["mention"]

    def test_a_self_review_is_allowed_but_logged(self, world, launched, monkeypatch, caplog):
        monkeypatch.setattr(wds, "request_review", lambda **kw: {
            "token": "raw", "token_id": "tok_1", "scratch_dir": "/tmp/s", "mention": "BASE"})
        import logging

        with caplog.at_level(logging.WARNING, logger=svc.logger.name):
            svc._spawn_review_hop(
                GROUP,
                bundle(reviewer_overrides={"5": "aip_step5"},
                       provider_overrides={"5": "aip_step5"}),
                self._gate(world))
        assert launched, "it still runs — a person may deliberately pick it"
        assert any("self-reviewed" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════════════════════
# The control group: a request that sends neither map behaves exactly as before
# ══════════════════════════════════════════════════════════════════════════════════════

class TestOmittedMapsAreUnchanged:
    def test_the_paused_row_columns_stay_null(self, paused):
        svc._write_handoff_row(GROUP, _pending(), _run(
            continuation_review_count_overrides=None, continuation_reviewer_overrides=None))
        row = paused.rows[GROUP]
        assert row["continuation_review_count_overrides"] is None
        assert row["continuation_reviewer_overrides"] is None
        # ...and "sent nothing" is indistinguishable from "sent all zeros", by design.
        svc._write_handoff_row(GROUP, _pending(), _run(
            continuation_review_count_overrides={}, continuation_reviewer_overrides={}))
        assert paused.rows[GROUP]["continuation_review_count_overrides"] is None

    def test_the_gate_resolves_to_the_old_flow_for_every_slot(self, world):
        for item_seq, doc_id in ((1, "doc-1"), (5, "doc-5"), (8, "doc-8")):
            world.fill(item_seq, doc_id)
            gate = svc.resolve_review_gate(bundle(review_count_overrides=None,
                                                  reviewer_overrides=None))
            assert gate["stage"] == "work"
            assert gate.get("approve_first") is True
            assert "stop_code" not in gate

    def test_the_inbox_boundary_does_not_gate_without_a_selection(self, monkeypatch):
        monkeypatch.setattr(svc, "_runs", {})
        assert svc.active_review_selection(GROUP) == (None, None)
        assert svc.resolve_review_count(None, 5) == 0

    def test_docs_target_is_not_changed_by_a_review_selection(self, route_env):
        """P0007: `docs_target: 6` is the SAME value with and without the maps — review
        rounds are not documents."""
        _post()
        without = route_env.get("continuation_target_seq")
        route_env.clear()
        _post(continuation_review_count_overrides={"5": 2},
              continuation_reviewer_overrides={"5": "aip_rev"})
        assert route_env.get("continuation_target_seq") == without
        # every other start_run argument the chain's shape depends on is untouched too
        assert route_env["mode"] == "continuous"
        assert route_env["continuation_instruction_mode"] == "auto_approved"
        assert route_env["continuation_review_mode"] is False

    def test_an_ordinary_single_run_still_owns_its_own_chain_identity(self):
        """The chain-preservation change must not move a run that names no chain."""
        import inspect

        source = inspect.getsource(svc.start_run)
        assert "chain_id = chain_id or run_id" in source
        assert "chain_id = run_id\n" not in source


# ══════════════════════════════════════════════════════════════════════════════════════
# DB0009 §3 — migration 086 in all three dialects
# ══════════════════════════════════════════════════════════════════════════════════════

_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
_NAME = "086_ai_invoke_paused_review.sql"


class TestMigration086:
    @pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
    def test_the_file_exists_and_adds_exactly_the_two_columns(self, dialect):
        path = _MIGRATIONS / dialect / _NAME
        assert path.is_file(), f"{dialect} has no {_NAME} — one dialect alone is a runtime error"
        text = path.read_text(encoding="utf-8")
        for column in REVIEW_KWARGS:
            assert (f"ALTER TABLE ai_invoke_paused_chains ADD COLUMN {column} TEXT;") in text
        assert text.count("ALTER TABLE") == 2, "additive only — no other DDL"
        for forbidden in ("CREATE TABLE", "CREATE INDEX", "FOREIGN KEY", "CHECK (", "UPDATE "):
            assert forbidden not in text, f"{forbidden} is out of scope for this migration"

    def test_only_sqlite_wraps_the_ddl_in_a_transaction(self):
        """DB0009 §3-1 follows 076a, the same table's own precedent."""
        sqlite_text = (_MIGRATIONS / "sqlite" / _NAME).read_text(encoding="utf-8")
        assert "BEGIN;" in sqlite_text and "COMMIT;" in sqlite_text
        for dialect in ("postgres", "mysql"):
            text = (_MIGRATIONS / dialect / _NAME).read_text(encoding="utf-8")
            assert "BEGIN;" not in text and "COMMIT;" not in text

    def test_086_is_free_in_every_dialect(self):
        for dialect in ("sqlite", "postgres", "mysql"):
            same_number = sorted(p.name for p in (_MIGRATIONS / dialect).glob("086*.sql"))
            assert same_number == [_NAME], f"{dialect} number collision: {same_number}"

    def test_applying_it_leaves_existing_rows_null_and_round_trips_a_new_one(self, tmp_path):
        """DB0009 §3-3: no backfill. A row written before 086 reads NULL, which the gate
        reads as REVIEW_COUNT_DEFAULT — today's behaviour, unchanged."""
        db_path = tmp_path / "probe.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            # Rebuild the table the way the real migrations do, in order, up to 085.
            for path in sorted((_MIGRATIONS / "sqlite").glob("*.sql")):
                if path.name >= _NAME:
                    continue
                for statement in path.read_text(encoding="utf-8").split(";"):
                    if "ai_invoke_paused_chains" not in statement:
                        continue
                    stripped = statement.strip()
                    if stripped.upper().startswith(("CREATE TABLE", "ALTER TABLE")):
                        conn.execute(stripped)
            columns_before = {r[1] for r in conn.execute(
                "PRAGMA table_info(ai_invoke_paused_chains)")}
            assert "continuation_step_timeout_sec" in columns_before, (
                "the pre-086 schema was not rebuilt — this guard would prove nothing")
            for column in REVIEW_KWARGS:
                assert column not in columns_before

            conn.execute(
                "INSERT INTO ai_invoke_paused_chains"
                "(group_id, doc_ref, paused_by, paused_at, docs_reached, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 0, ?, ?)",
                (GROUP, SPINE, USER, "2026-08-22T10:00:00+09:00",
                 "2026-08-22T10:00:00+09:00", "2026-08-22T10:00:00+09:00"))
            conn.commit()

            conn.executescript((_MIGRATIONS / "sqlite" / _NAME).read_text(encoding="utf-8"))

            columns_after = {r[1] for r in conn.execute(
                "PRAGMA table_info(ai_invoke_paused_chains)")}
            assert set(REVIEW_KWARGS) <= columns_after
            legacy = conn.execute(
                f"SELECT {', '.join(REVIEW_KWARGS)} FROM ai_invoke_paused_chains"
                " WHERE group_id = ?", (GROUP,)).fetchone()
            assert legacy == (None, None), "no backfill: a pre-086 row resumes unreviewed"
            assert svc.resolve_review_count(db_paused.load_json_map(legacy[0]), 5) == 0

            conn.execute(
                f"UPDATE ai_invoke_paused_chains SET {REVIEW_KWARGS[0]} = ?,"
                f" {REVIEW_KWARGS[1]} = ? WHERE group_id = ?",
                (db_paused.dump_json_map({"5": 2, "7": -1}),
                 db_paused.dump_json_map({"5": "aip_rev"}), GROUP))
            conn.commit()
            stored = conn.execute(
                f"SELECT {', '.join(REVIEW_KWARGS)} FROM ai_invoke_paused_chains"
                " WHERE group_id = ?", (GROUP,)).fetchone()
            assert db_paused.load_json_map(stored[0]) == {"5": 2, "7": -1}
            assert db_paused.load_json_map(stored[1]) == {"5": "aip_rev"}
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════════════════
# L0008 §4.3 — [AI 검토 모드] outranks the gate
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReviewModePriority:
    def test_review_mode_stops_first_and_never_consults_the_gate(self, monkeypatch):
        consulted: list = []
        monkeypatch.setattr(svc, "active_review_selection",
                            lambda group_id: consulted.append(group_id) or (None, None))
        monkeypatch.setattr(svc, "stamp_chain_stop",
                            lambda envelope, stop_code, **kw: {**envelope,
                                                               "continuation_stop_code": stop_code})
        result = inbox_routes._continuation_self_chain(
            _Req(),
            {"continuation_target_seq": 9, "continuation_review_mode": True,
             "continuation_instruction_mode": "auto_approved", "doc_ref": SPINE,
             "issued_to": USER, "group_id": GROUP, "token_id": "tok_1",
             "continuation_auto_approve_item_seqs": []},
            "flowgate", "flowgate.default.0414.0005-TR", "TR",
        )
        assert result["continuation_stop_code"] == "review_hold"
        assert consulted == [], "the gate must not run while the chain is not allowed to advance"

    def test_the_selection_still_reaches_the_paused_row_in_review_mode(self, paused):
        """§4.3 근거 3: turning review mode off and resuming must start reviewing, so the
        maps are stored on a review_hold row too."""
        svc._write_handoff_row(GROUP, {**_pending(), "review_mode": True}, _run(),
                               stop_code="review_hold")
        assert json.loads(
            paused.rows[GROUP]["continuation_review_count_overrides"]) == {"5": 2, "7": 3}

    def test_a_copy_mention_chain_is_never_gated(self, monkeypatch):
        """L0008 §2.8 조건 3: with no engine run there is nobody to launch a review hop, so
        the old path is kept rather than silently deciding to review and then not."""
        monkeypatch.setattr(svc, "_runs", {})
        assert svc.has_active_run(GROUP) is False
        assert svc.active_review_selection(GROUP) == (None, None)

    def test_the_inbox_gate_sits_before_the_auto_approve(self):
        import inspect

        source = inspect.getsource(inbox_routes._continuation_self_chain)
        gate_at = source.index("active_review_selection")
        settle_at = source.index("settled = settle_completed_step")
        head_at = source.index('_stop("head_slot_mismatch")')
        assert head_at < gate_at < settle_at, (
            "L0008 §2.8: review mode -> head slot -> [검수 게이트] -> auto-approve")

    def test_both_engine_boundaries_share_one_handoff(self):
        import inspect

        source = inspect.getsource(inbox_routes._continuation_self_chain)
        assert source.count("_hand_off_to_engine(review_pending=True)") == 1
        assert source.count("_hand_off_to_engine(review_pending=False)") == 1
        assert source.count('return _stop("hop_handoff", item_seq=completed_seq)') == 1


class TestSettleExtraction:
    def test_the_settle_helper_is_shared_by_both_callers(self):
        import inspect

        assert callable(inbox_routes.settle_completed_step)
        gate_source = inspect.getsource(svc._settle_gate_pass)
        assert "settle_completed_step" in gate_source
        chain_source = inspect.getsource(inbox_routes._continuation_self_chain)
        assert "settle_completed_step" in chain_source
        # ...and the sequence exists in exactly one place
        assert "transition_document_review" not in chain_source

    def test_the_reworked_revision_is_in_a_state_approve_accepts(self):
        """0414 M0020: the advance branch approves a document that was REJECTED and then
        reworked, not one that passed. The rework hop re-submits through the inbox, so the
        row settle_completed_step receives is 'revised' — ('revised', 'approve') has to be a
        legal transition or every finite chain would park on approve_failed instead of
        moving on, which is the exact failure the memo would see as \"still not fixed\"."""
        from modules.flow_gate.workflow.transition_rules import get_doc_review_rule

        assert get_doc_review_rule("rejected", "submit") == "revised"
        assert get_doc_review_rule("revised", "approve") == "approved"
        assert "revised" in svc.REVIEW_PENDING_DOC_STATUSES, (
            "a reworked-but-unapproved slot must still be found as the waiting slot")

    def test_the_target_check_runs_after_approval(self):
        import inspect

        source = inspect.getsource(inbox_routes.settle_completed_step)
        approve_at = source.index('action="approve"')
        target_at = source.index("completed_seq >= target_seq")
        pause_at = source.index("user_paused_probe()")
        assert approve_at < target_at < pause_at, (
            "the last step must end approved, and a pause is checked after that (P0008 S4/S5)")


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.1/2.3 — the review_id anchor AND the current-revision guard, combined (A1-A9)
#
# One document_reviews ROW makes at most one automatic rejection -- even across a human
# mark_revised that leaves the revision unchanged (A6, RED on main before this change).
# _auto_reject and transition_document_review run for REAL here (real_gate_exec below):
# a fake _auto_reject (as gate_exec uses) would make every one of these tests vacuous,
# since the whole point is whether a SECOND gate call sees what the FIRST call's real
# write left in rejection_history.
# ══════════════════════════════════════════════════════════════════════════════════════

_REAL_AUTO_REJECT = svc._auto_reject


@pytest.fixture
def real_gate_exec(monkeypatch, world, gate_exec):
    """gate_exec's spawn captures, with _auto_reject restored to the real function and
    just enough of transition_document_review's dependencies patched (state-preserving,
    T0005 2.3) that it writes review_id-tagged rejection_history into world.docs."""
    from modules.flow_gate.workflow import pipeline_service as ps
    from modules.flow_gate.workflow.routers import workflow as wf_router

    def _update(doc_id, fields):
        doc = world.docs.setdefault(doc_id, {})
        doc.update(fields)
        return doc

    monkeypatch.setattr(svc.db_docs, "update", _update)
    monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
    monkeypatch.setattr(wf_router, "_get_user_permissions",
                        lambda user: {"document.approve", "document.reject",
                                       "document.update", "own.draft"})
    monkeypatch.setattr(ps, "log_state_changed", lambda **kw: {"id": 1})
    monkeypatch.setattr(ps, "_require_document_body_for_approval",
                        lambda doc, locale="ko": None)

    def _real_auto_reject(slot, review, b):
        gate_exec["rejected"].append(slot["doc_id"])
        return _REAL_AUTO_REJECT(slot, review, b)

    monkeypatch.setattr(svc, "_auto_reject", _real_auto_reject)
    return gate_exec


def _history_of(world, doc_id):
    raw = world.docs[doc_id].get("rejection_history")
    if raw is None:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


class TestT0005ReviewIdAnchor:
    """A1-A9 (T0005 3항)."""

    def test_a1_one_rejection_per_review_row_then_round_two_after_rework(
            self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "fix x"}])
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == ["doc-5"]
        assert world.docs["doc-5"]["doc_review_status"] == "rejected"
        assert len(_history_of(world, "doc-5")) == 1

        world.rework("doc-5", 1)   # ('rejected','submit') -> 'revised', revision_no 1
        real_gate_exec["rejected"].clear()

        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "review" and gate["round_no"] == 2
        assert "reject_first" not in gate

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == [], "_auto_reject total stays at 1"
        assert len(_history_of(world, "doc-5")) == 1

    def test_a2_two_review_rows_each_earn_their_own_rejection(self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "a"}])
        svc.run_review_gate(GROUP, bundle(), _run())
        world.rework("doc-5", 1)

        world.review("doc-5", "issues", revision_no=1,
                     findings=[{"locus": "§2", "note": "b"}])
        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is True
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True

        history = _history_of(world, "doc-5")
        assert len(history) == 2
        ids = sorted(int(item["review_id"]) for item in history)
        reviews = sorted(int(r["id"]) for r in world.reviews["doc-5"])
        assert ids == reviews
        assert len({item["review_id"] for item in history}) == 2, "one item per row"

    def test_a3_a_pass_after_rework_finds_the_document_still_revised(self, world, real_gate_exec):
        from modules.flow_gate.workflow.transition_rules import get_doc_review_rule

        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        svc.run_review_gate(GROUP, bundle(), _run())
        world.rework("doc-5", 1)
        world.review("doc-5", "pass", revision_no=1)

        assert world.docs["doc-5"]["doc_review_status"] == "revised"
        assert get_doc_review_rule("revised", "approve") == "approved"
        assert get_doc_review_rule("rejected", "approve") is None

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["parked"] == []
        assert real_gate_exec["settled"] == ["doc-5"]
        assert len(real_gate_exec["work"]) == 1

    def test_a4_a_cold_resume_onto_the_landed_rework_does_not_inflate_the_count(
            self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        svc.run_review_gate(GROUP, bundle(), _run())
        world.rework("doc-5", 1)
        real_gate_exec["rejected"].clear()

        # A bundle with no last_stage IS the cold-resume shape already (P0007/L0008 2.9) --
        # re-derive it twice, exactly as a restart followed by a resume would.
        gate1 = svc.resolve_review_gate(bundle())
        gate2 = svc.resolve_review_gate(bundle())
        assert gate1["stage"] == gate2["stage"] == "review" and gate1["round_no"] == 2
        assert real_gate_exec["rejected"] == []
        assert len(_history_of(world, "doc-5")) == 1

    def test_a5_two_consecutive_run_review_gate_calls_on_the_same_state_stay_idempotent(
            self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0)
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == ["doc-5"], "the second call rejects nothing new"
        assert len(_history_of(world, "doc-5")) == 1

    def test_a6_mark_revised_at_the_same_revision_does_not_re_reject(self, world, real_gate_exec):
        """The money test (T0005): RED on main before the review_id anchor. A human's
        mark_revised puts the document back at pending_review WITHOUT bumping the
        revision -- exactly the state a landed rework does NOT produce -- so the old
        status-only + revision-match guard alone re-opens it for a second rejection."""
        from modules.flow_gate.workflow import pipeline_service as ps

        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "x"}])
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == ["doc-5"]
        assert world.docs["doc-5"]["doc_review_status"] == "rejected"
        assert world.docs["doc-5"]["revision_no"] == 0
        real_gate_exec["rejected"].clear()

        ps.transition_document_review(
            doc_id="doc-5", action="mark_revised", actor_user_id=USER,
            user_permissions={"document.update", "own.draft"},
        )
        assert world.docs["doc-5"]["doc_review_status"] == "pending_review"
        assert world.docs["doc-5"]["revision_no"] == 0, "revision is UNCHANGED -- the A6 trap"

        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework"
        assert gate["reject_first"] is False, (
            "the same review row already produced one rejection; mark_revised must not "
            "re-open it for a second")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == [], "no second _auto_reject call"
        assert len(_history_of(world, "doc-5")) == 1, "history stays at one entry"

    def test_a7_a_human_rejection_is_never_re_rejected_and_its_history_is_preserved(
            self, world, real_gate_exec):
        world.fill(5, "doc-5", status="rejected")
        world.docs["doc-5"]["rejection_history"] = json.dumps([{
            "rejection_id": "rej_human0001", "reason": "human says fix Y",
            "rejected_at": "2026-08-26T00:00:00+09:00", "rejected_by": USER,
            "ai_response": None, "responded_at": None,
            "response_recorded_by": None, "response_revision_no": None,
        }])
        world.review("doc-5", "issues", revision_no=0)

        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is False
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == []
        history = _history_of(world, "doc-5")
        assert len(history) == 1
        assert history[0]["reason"] == "human says fix Y"
        assert "review_id" not in history[0]

    def test_a8_byte_identical_reasons_from_two_different_rows_both_reject(
            self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "same note"}], comment="same comment")
        svc.run_review_gate(GROUP, bundle(), _run())
        world.rework("doc-5", 1)
        real_gate_exec["rejected"].clear()

        world.review("doc-5", "issues", revision_no=1,
                     findings=[{"locus": "§1", "note": "same note"}], comment="same comment")
        r1 = dict(world.reviews["doc-5"][1])   # the round-1 row, oldest
        r2 = dict(world.reviews["doc-5"][0])   # the round-2 row, newest
        reason1 = svc.build_auto_reject_reason(r1, {"doc_id": "doc-5"}, API_BASE)
        reason2 = svc.build_auto_reject_reason(r2, {"doc_id": "doc-5"}, API_BASE)
        assert reason1 == reason2, "the premise: two rows, byte-identical reason text"

        gate = svc.resolve_review_gate(bundle())
        assert gate["stage"] == "rework" and gate["reject_first"] is True, (
            "a different row's identical text must not be read as already applied")
        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == ["doc-5"]

        history = _history_of(world, "doc-5")
        assert len(history) == 2
        assert {item["review_id"] for item in history} == {r1["id"], r2["id"]}

    def test_a9_a_keyless_legacy_item_suppresses_by_reason_but_a_null_keyed_item_never_does(
            self, world, real_gate_exec):
        # -- 9a: an OLD item with no review_id key at all, exact-reason match suppresses --
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "x"}], comment="c")
        gate0 = svc.resolve_review_gate(bundle())
        legacy_reason = svc.build_auto_reject_reason(
            svc._latest_review_of(gate0["slot"]), gate0["slot"], API_BASE)
        world.docs["doc-5"]["rejection_history"] = json.dumps([{
            "rejection_id": "rej_legacy001", "reason": legacy_reason,
            "rejected_at": "2026-08-26T00:00:00+09:00", "rejected_by": USER,
            "ai_response": None, "responded_at": None,
            "response_recorded_by": None, "response_revision_no": None,
        }])

        gate = svc.resolve_review_gate(bundle())
        assert gate["reject_first"] is False, "exact reason match on a KEYLESS item suppresses"
        svc.run_review_gate(GROUP, bundle(), _run())
        assert real_gate_exec["rejected"] == []

        # -- 9b: items that DO carry the key, but an unusable value, never suppress --
        for bad_value in (None, "", "   "):
            world.reviews.pop("doc-7", None)
            world.fill(7, "doc-7", status="pending_review", revision_no=0)
            world.review("doc-7", "issues", revision_no=0,
                         findings=[{"locus": "§1", "note": "y"}], comment="c2")
            gate0b = svc.resolve_review_gate(bundle(review_count_overrides={"7": 2}))
            reason_b = svc.build_auto_reject_reason(
                svc._latest_review_of(gate0b["slot"]), gate0b["slot"], API_BASE)
            world.docs["doc-7"]["rejection_history"] = json.dumps([{
                "rejection_id": "rej_bad0001", "review_id": bad_value, "reason": reason_b,
                "rejected_at": "2026-08-26T00:00:00+09:00", "rejected_by": USER,
                "ai_response": None, "responded_at": None,
                "response_recorded_by": None, "response_revision_no": None,
            }])
            real_gate_exec["rejected"].clear()
            gate_b = svc.resolve_review_gate(bundle(review_count_overrides={"7": 2}))
            assert gate_b["reject_first"] is True, (
                f"review_id={bad_value!r} present-but-invalid must not suppress")


class TestReviewFindingsToNextStepEndToEnd0476:
    """0476 T0009: findings -> auto-reject -> rework -> pass -> settle, as one chain.

    TestGateDerivation.test_count_one_issues_rejects_reworks_then_advances only watches
    resolve_review_gate's derivation, and TestT0005ReviewIdAnchor.test_a3_a_pass_after_
    rework_finds_the_document_still_revised only watches the pass-then-settle half. Neither
    ties findings actually causing the auto-reject write to the rejection actually landing a
    rework hop, nor the rework landing to the pass actually launching item_seq 6.
    """

    def test_findings_reject_rework_pass_settle_reach_next_step(self, world, real_gate_exec):
        world.fill(5, "doc-5")
        world.review("doc-5", "issues", revision_no=0,
                     findings=[{"locus": "§1", "note": "fix x"}])

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["rejected"] == ["doc-5"]
        assert len(real_gate_exec["rework"]) == 1

        world.rework("doc-5", 1)
        assert world.docs["doc-5"]["revision_no"] == 1
        assert world.docs["doc-5"]["doc_review_status"] == "revised"

        world.review("doc-5", "pass", revision_no=1)

        assert svc.run_review_gate(GROUP, bundle(), _run()) is True
        assert real_gate_exec["settled"] == ["doc-5"]
        assert len(real_gate_exec["work"]) == 1
        assert real_gate_exec["parked"] == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 0458 T0007 §3.1 — the failure detail survives all the way to the sentence a human reads
# ══════════════════════════════════════════════════════════════════════════════════════


class TestApproveAndAdvanceStopDetail:
    """`approve_failed` / `advance_blocked` used to read ONE detail key — the inbox's.

    The AI review gate has a live run to hang its failure on, so it stores the exception on
    `review_reject_detail` (_settle_gate_pass); the inbox self-chain has only an envelope and
    goes through stop_reason_text(..., detail=...), which lands on `inbox_stop_detail`. With
    only the second key read, every gate-side failure printed "unknown error" while the real
    cause sat one key away (0003-NR §11-1).
    """

    def test_approve_failed_carries_the_review_gates_own_detail(self):
        run = {"review_reject_detail": "Invalid review transition: rejected -> approve"}
        assert svc._stop_reason_text("approve_failed", run) == (
            "Auto-approval failed: Invalid review transition: rejected -> approve"
        )

    def test_advance_blocked_carries_the_review_gates_own_detail(self):
        run = {"review_reject_detail": "sequence exhausted"}
        assert svc._stop_reason_text("advance_blocked", run) == (
            "Could not advance to the next step: sequence exhausted"
        )

    def test_the_gate_detail_outranks_the_inbox_one_when_both_are_set(self):
        """A run can carry both keys — an earlier inbox stop, then this gate's own failure.
        The gate's is the one describing THIS stop, so it wins."""
        run = {"review_reject_detail": "gate said why",
               "inbox_stop_detail": "an older inbox stop"}
        assert svc._stop_reason_text("approve_failed", run) == "Auto-approval failed: gate said why"
        assert svc._stop_reason_text("advance_blocked", run) == (
            "Could not advance to the next step: gate said why"
        )

    def test_the_inbox_self_chains_detail_is_untouched(self):
        """stop_reason_text() builds a run dict with `inbox_stop_detail` only, which is the
        exact shape test_inbox_chain_stop_0359's two detail tests assert. The new key must
        not change a word of it."""
        assert svc.stop_reason_text("approve_failed", detail="head already advanced") == (
            "Auto-approval failed: head already advanced"
        )
        assert svc.stop_reason_text("advance_blocked", detail="sequence exhausted") == (
            "Could not advance to the next step: sequence exhausted"
        )

    def test_only_a_missing_detail_falls_back_to_unknown_error(self):
        for run in ({}, {"review_reject_detail": None, "inbox_stop_detail": None},
                    {"review_reject_detail": "", "inbox_stop_detail": ""}):
            assert svc._stop_reason_text("approve_failed", run) == (
                "Auto-approval failed: unknown error")
            assert svc._stop_reason_text("advance_blocked", run) == (
                "Could not advance to the next step: unknown error")

    def test_the_fixed_english_prefixes_and_the_stop_contract_are_unchanged(self):
        run = {"review_reject_detail": "boom"}
        assert svc._stop_reason_text("approve_failed", run).startswith("Auto-approval failed:")
        assert svc._stop_reason_text("advance_blocked", run).startswith(
            "Could not advance to the next step:")
        # Neither code is resumable — a human has to clean these up (L0007 §4.2).
        assert not svc.is_resumable("approve_failed")
        assert not svc.is_resumable("advance_blocked")

    # ── the storage half: the gate must PUT the detail on the run before it parks ──

    def _settle(self, monkeypatch, result):
        parked = {}
        monkeypatch.setattr(inbox_routes, "settle_completed_step", lambda **_kw: result)
        monkeypatch.setattr(
            svc, "_park_handoff",
            lambda run, pending, stop_code: parked.update(run=run, stop_code=stop_code),
        )
        run = {"group_id": GROUP, "run_id": "run_detail"}
        slot = {"doc_id": SPINE, "doc_type": "TR", "item_seq": 5}
        outcome = svc._settle_gate_pass(GROUP, slot, bundle(), run)
        return outcome, run, parked

    def test_the_gate_stores_the_settle_failures_detail_before_parking(self, monkeypatch):
        outcome, run, parked = self._settle(monkeypatch, {
            "outcome": "stopped",
            "stop_code": "approve_failed",
            "reason": "auto-approve failed: Invalid review transition",
            "detail": "Invalid review transition",
        })
        assert outcome == "stopped"
        assert parked["stop_code"] == "approve_failed"
        assert run["review_reject_detail"] == "Invalid review transition"
        # ...and the parked run renders the sentence with that detail, not "unknown error".
        assert svc._stop_reason_text("approve_failed", parked["run"]) == (
            "Auto-approval failed: Invalid review transition"
        )

    def test_a_stopped_settle_with_no_detail_key_still_stores_its_reason(self, monkeypatch):
        """approve_denied carries `reason` and no `detail`. Storing None there would be the
        same information loss one branch further along, so the contract is detail-or-reason."""
        _outcome, run, parked = self._settle(monkeypatch, {
            "outcome": "stopped",
            "stop_code": "approve_denied",
            "reason": "issuer lacks document.approve; awaiting human approval before continuing.",
        })
        assert parked["stop_code"] == "approve_denied"
        assert run["review_reject_detail"] == (
            "issuer lacks document.approve; awaiting human approval before continuing."
        )

    def test_an_advance_blocked_settle_would_be_stored_the_same_way(self, monkeypatch):
        """settle_completed_step is the ONLY entry point through which this gate can reach
        either code, so one storage line covers both — no advance path parks empty-handed."""
        _outcome, run, parked = self._settle(monkeypatch, {
            "outcome": "stopped",
            "stop_code": "advance_blocked",
            "detail": "sequence exhausted",
        })
        assert parked["stop_code"] == "advance_blocked"
        assert svc._stop_reason_text("advance_blocked", run) == (
            "Could not advance to the next step: sequence exhausted"
        )

    def test_a_continuing_settle_parks_nothing_and_stores_nothing(self, monkeypatch):
        outcome, run, parked = self._settle(monkeypatch, {"outcome": "continue"})
        assert outcome == "continue"
        assert parked == {}
        assert "review_reject_detail" not in run


# ══════════════════════════════════════════════════════════════════════════════════════
# 0458 T0007 §3.1 / §4-1 — the REAL order: finalize closes the run, THEN the gate stops it
#
# The worker tail is `_finalize_run(run)` and then `_maybe_auto_resume_hop(run)`. Finalize
# sees the queued next hop, so it decides `hop_handoff` — "this hop produced its document;
# the next hop starts in a new worker" — and with that verdict it writes the ai_invoke_runs
# row, fires the finished payload the miniplayer keeps, and settles the failure
# notification. ONLY AFTER all of that does the gate run, and only there can the auto
# approval fail. Fixing _stop_reason_text alone left every one of those three surfaces
# holding the handoff sentence while the durable row said `approve_failed`, so the human
# still never saw the exception.
#
# Nothing below mocks _park_handoff or hand-feeds a dict to _stop_reason_text: the tests
# drive the two functions the worker calls, in the order the worker calls them, and read the
# surfaces a person actually reads.
# ══════════════════════════════════════════════════════════════════════════════════════


APPROVE_FAILURE = {
    "outcome": "stopped",
    "stop_code": "approve_failed",
    "reason": "auto-approve failed: Invalid review transition: rejected -> approve",
    "detail": "Invalid review transition: rejected -> approve",
}
APPROVE_FAILED_SENTENCE = (
    "Auto-approval failed: Invalid review transition: rejected -> approve"
)
HANDOFF_SENTENCE = "This hop produced its document; the next hop starts in a new worker."


@pytest.fixture
def lifecycle(monkeypatch, world, paused, tmp_path):
    """The worker tail with only the OUTSIDE world stubbed — records, feed, SSE, leases.

    `world` and `paused` already stand in for the sequence/document/review tables and the
    paused-chain table, so what runs here is the production code path from _finalize_run
    through _maybe_auto_resume_hop, run_review_gate, _settle_gate_pass and _park_handoff.
    """
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    from modules.flow_gate.workflow import event_logger

    seen = {"records": [], "notified": [], "events": [], "leases": []}
    stored: dict[str, dict] = {}

    def _upsert(row):
        stored[row["run_id"]] = dict(row)
        seen["records"].append(dict(row))

    monkeypatch.setattr(db_runs, "upsert", _upsert)
    monkeypatch.setattr(db_runs, "maybe_purge", lambda: None)
    monkeypatch.setattr(db_runs, "get",
                        lambda run_id: dict(stored[run_id]) if run_id in stored else None)
    monkeypatch.setattr(event_logger, "log_continuous_work_failed",
                        lambda **kw: seen["notified"].append(kw))
    monkeypatch.setattr(svc, "_broadcast",
                        lambda run, event_type, payload: seen["events"].append(
                            (event_type, dict(payload))))
    for name in ("begin_handoff", "release"):
        monkeypatch.setattr(svc.db_group_ai_leases, name,
                            lambda *a, _n=name, **kw: seen["leases"].append(_n))
    seen["stored"] = stored
    seen["scratch"] = tmp_path
    yield seen
    svc.clear_auto_resume(GROUP)


def _live_run(tmp_path, **overrides):
    """A hop that has just exited, exactly as the worker holds it when it calls finalize."""
    run = _run(
        status="running",
        outcome="none",
        end_reason="exited",
        project_id="flowgate",
        scratch_dir=str(tmp_path / "scratch"),
        started_mono=time.monotonic(),
        cancel_event=None,
        stop_code=None,
        resumable=False,
        failure_signal_sent=False,
        # no review selection anywhere, so the gate resolves to "approve and continue" —
        # the branch that calls settle_completed_step (L0008 §2.3, count 0).
        continuation_review_count_overrides=None,
        continuation_reviewer_overrides=None,
        # finished_payload reads these straight off the run.
        reached_doc_ids=["doc-5"],
        exit_code=0,
        last_message_received=True,
        last_message="done",
        provider_id="aip_step5",
        provider={"id": "aip_step5", "name": "Claude Sonnet 5"},
        attempt_no=1,
        attempts_used=1,
        fallback_history=[],
        source_dirty=None,
    )
    run.update(overrides)
    return run


def _drive_the_tail(monkeypatch, lifecycle, settle_result, **run_over):
    """_finalize_run → _maybe_auto_resume_hop, the two calls the worker makes in that order."""
    monkeypatch.setattr(inbox_routes, "settle_completed_step", lambda **_kw: settle_result)
    run = _live_run(lifecycle["scratch"], **run_over)
    # The inbox self-chain queued the next hop while this one was still running — the ONLY
    # reason finalize resolves `hop_handoff` instead of an ordinary ending.
    svc.request_auto_resume(GROUP, {**_pending(), "review_count_overrides": None,
                                    "reviewer_overrides": None})
    svc._finalize_run(run)
    after_finalize = dict(run)
    svc._maybe_auto_resume_hop(run)
    return run, after_finalize


class TestGateStopReachesEverySurfaceAfterFinalize:
    def test_finalize_really_does_settle_the_handoff_verdict_first(
        self, monkeypatch, world, paused, lifecycle
    ):
        """The premise, asserted rather than assumed: by the time the gate is even consulted,
        a full stop verdict has already been computed, persisted and broadcast."""
        world.fill(5, "doc-5")
        _run_dict, after_finalize = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        assert after_finalize["stop_code"] == "hop_handoff"
        assert after_finalize["stop_reason"] == HANDOFF_SENTENCE
        assert lifecycle["records"][0]["stop_code"] == "hop_handoff"
        assert lifecycle["records"][0]["stop_reason"] == HANDOFF_SENTENCE
        assert lifecycle["events"][0][0] == "ai_invoke_finished"
        assert lifecycle["events"][0][1]["stop_reason"] == HANDOFF_SENTENCE

    def test_the_gates_failure_replaces_that_verdict_on_the_run(
        self, monkeypatch, world, paused, lifecycle
    ):
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        assert run["stop_code"] == "approve_failed"
        assert run["stop_reason"] == APPROVE_FAILED_SENTENCE
        assert run["resumable"] is False       # re-derived, not left over from the handoff

    def test_the_persisted_record_ends_on_the_real_exception(
        self, monkeypatch, world, paused, lifecycle
    ):
        """The row a human opens tomorrow. It was written before the gate ran, so the repair
        is a SECOND write to the same run_id — not an extra row, and not the handoff text."""
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        stored = lifecycle["stored"][run["run_id"]]
        assert stored["stop_code"] == "approve_failed"
        assert stored["stop_reason"] == APPROVE_FAILED_SENTENCE
        assert stored["resumable"] is False
        assert "unknown error" not in (stored["stop_reason"] or "")
        assert set(lifecycle["stored"]) == {run["run_id"]}
        assert [r["stop_code"] for r in lifecycle["records"]] == [
            "hop_handoff", "approve_failed",
        ]

    def test_the_card_is_told_again_with_the_real_stop(
        self, monkeypatch, world, paused, lifecycle
    ):
        """The miniplayer is sitting on a `hop_handoff` payload waiting for a successor hop
        that is never coming. A second finished event — same run_id — is what replaces it."""
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        finished = [p for kind, p in lifecycle["events"] if kind == "ai_invoke_finished"]
        assert len(finished) == 2
        assert finished[-1]["run_id"] == run["run_id"]
        assert finished[-1]["stop_code"] == "approve_failed"
        assert finished[-1]["stop_reason"] == APPROVE_FAILED_SENTENCE
        assert ("group_view_refresh", {"group_id": GROUP,
                                       "reason": "ai_invoke_finished"}) in lifecycle["events"]

    def test_the_notification_feed_gets_the_exception_not_a_bare_code(
        self, monkeypatch, world, paused, lifecycle
    ):
        """§2.11's set split leaves approve_failed to the inbox — which never sees this stop,
        because no request arrives: the engine settled the step itself. Nobody spoke at all
        before this. Exactly one notification, and it carries the sentence."""
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        assert len(lifecycle["notified"]) == 1
        signal = lifecycle["notified"][0]
        assert signal["run_id"] == run["run_id"]
        assert signal["error"] == APPROVE_FAILED_SENTENCE
        assert signal["extra"]["stop_code"] == "approve_failed"
        assert signal["extra"]["stop_reason"] == APPROVE_FAILED_SENTENCE

    def test_the_paused_card_the_user_sees_carries_the_same_sentence(
        self, monkeypatch, world, paused, lifecycle
    ):
        """active_all is the refresh-proof bootstrap: after the browser reloads, THIS is the
        only place the stop exists. The row has a stop_code column and no stop_reason one, so
        the payload reads the sentence back off the run that parked it."""
        world.fill(5, "doc-5")
        _run_dict, _after = _drive_the_tail(monkeypatch, lifecycle, APPROVE_FAILURE)

        payload = svc.active_all(USER)
        assert len(payload["paused"]) == 1
        card = payload["paused"][0]
        assert card["stop_kind"] == "system"
        assert card["stop_code"] == "approve_failed"
        assert card["stop_reason"] == APPROVE_FAILED_SENTENCE

    def test_an_advance_failure_travels_the_same_way(
        self, monkeypatch, world, paused, lifecycle
    ):
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, {
            "outcome": "stopped",
            "stop_code": "advance_blocked",
            "detail": "sequence exhausted",
        })
        sentence = "Could not advance to the next step: sequence exhausted"

        assert run["stop_reason"] == sentence
        assert lifecycle["stored"][run["run_id"]]["stop_reason"] == sentence
        assert lifecycle["notified"][0]["error"] == sentence
        assert svc.active_all(USER)["paused"][0]["stop_reason"] == sentence

    def test_a_denied_approval_keeps_its_own_words_too(
        self, monkeypatch, world, paused, lifecycle
    ):
        """approve_denied carries `reason` and no `detail`; §4.3 gives it a fixed sentence of
        its own. What matters here is that the run stops saying "handoff"."""
        world.fill(5, "doc-5")
        run, _after = _drive_the_tail(monkeypatch, lifecycle, {
            "outcome": "stopped",
            "stop_code": "approve_denied",
            "reason": "issuer lacks document.approve; awaiting human approval before continuing.",
        })

        assert run["stop_code"] == "approve_denied"
        assert run["stop_reason"] != HANDOFF_SENTENCE
        assert lifecycle["stored"][run["run_id"]]["stop_code"] == "approve_denied"
        assert svc.active_all(USER)["paused"][0]["stop_reason"] == run["stop_reason"]

    def test_a_settle_that_continues_leaves_the_handoff_verdict_alone(
        self, monkeypatch, world, paused, lifecycle
    ):
        """The ordinary hop: the gate approves and the next worker starts. Nothing is parked,
        so nothing is re-decided — one record write, one finished event, no notification."""
        world.fill(5, "doc-5")
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, b: None)
        run, _after = _drive_the_tail(monkeypatch, lifecycle, {"outcome": "continue"})

        assert run["stop_code"] == "hop_handoff"
        assert len(lifecycle["records"]) == 1
        assert len([k for k, _p in lifecycle["events"] if k == "ai_invoke_finished"]) == 1
        assert lifecycle["notified"] == []

    def test_a_park_that_changes_nothing_writes_nothing_twice(
        self, monkeypatch, world, paused, lifecycle
    ):
        """A run that ALREADY ended on the code it is parked with — a cancel, an
        inbox-tagged stop — must not get a second record write, a second finished event or a
        second notification. Re-settlement corrects a verdict; it does not repeat one."""
        world.fill(5, "doc-5")
        run = _live_run(lifecycle["scratch"])
        run["stop_code"] = "timeout"
        run["stop_reason"] = svc._stop_reason_text("timeout", run)
        run["status"] = "finished"
        before_records = len(lifecycle["records"])

        svc._park_handoff(run, _pending(), "timeout")

        assert len(lifecycle["records"]) == before_records
        assert lifecycle["events"] == []
        assert lifecycle["notified"] == []

    def test_the_worker_tail_still_calls_these_two_in_this_order(self):
        """The premise of every test above, guarded at the source: finalize first, then the
        auto-resume that reaches the gate. If that ever inverts, the re-settlement is dead
        code and these assertions would keep passing against a shape production dropped."""
        import inspect

        source = inspect.getsource(svc._worker)
        finalize_at = source.index("_finalize_run(run)")
        resume_at = source.index("_maybe_auto_resume_hop(run)")
        assert finalize_at < resume_at
        park_source = inspect.getsource(svc._park_handoff)
        assert "_resettle_stop_after_park(run, stop_code)" in park_source
