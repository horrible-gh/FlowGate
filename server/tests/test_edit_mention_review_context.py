from __future__ import annotations

import json

from modules.flow_gate.api.token_routes import _load_current_revision_review
from modules.flow_gate.api.v1.document_routes import _shape_review
from modules.flow_gate.services.mention_service import build_mention


def _build_edit_mention(current_review: dict | None = None) -> str:
    mention = build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="FlowGate API verification design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        parent_revision_no=0,
        head_type="",
        head_status="",
        scratch_dir="D:/scratch/token",
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
        current_review=current_review,
    )
    assert mention is not None
    return mention


def test_edit_mention_includes_target_review_and_valid_payload():
    mention = _build_edit_mention({
        "revision_no": 0,
        "verdict": "issues",
        "findings": [
            {"locus": "Verification method", "note": "Document the one-time token constraint."},
        ],
        "comment": "Make each scenario executable.",
    })

    assert "## Revision instructions" in mention
    assert "## Instruction to include next document header" not in mention
    assert "test.none.0002.0004-D: GET http://127.0.0.1:8088/flowgate/api/v1/document/test.none.0002.0004-D" in mention
    assert "## Review feedback" in mention
    assert "Verdict: issues" in mention
    assert "- Verification method: Document the one-time token constraint." in mention
    assert "GET http://127.0.0.1:8088/flowgate/api/v1/document/test.none.0002.0004-D/reviews" in mention

    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert payload == {
        "action": "edit",
        "project": "test",
        "module": "none",
        "group_name": "test.none.0002",
        "doc_id": "test.none.0002.0004-D",
        "edit_reason": "user_comment",
        "content": "<Complete revised document content>",
    }


def test_edit_mention_without_review_still_references_target():
    mention = _build_edit_mention()
    assert "## Review feedback" not in mention
    assert "test.none.0002.0004-D: GET " in mention


def test_edit_mention_accepts_rejected_edit_reason():
    mention = build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="Rejected design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        head_type="",
        head_status="",
        scratch_dir="D:/scratch/token",
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
        edit_reason="rejected",
    )
    assert mention is not None
    assert '"edit_reason": "rejected"' in mention


def test_rejected_edit_mention_includes_rejection_response_field():
    """The copy mention's POST template must carry a rejection_response slot so the
    AI fills in how it addressed the rejection (TR0007 rev2 fix)."""
    mention = build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="Rejected design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        head_type="",
        head_status="",
        scratch_dir="D:/scratch/token",
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
        edit_reason="rejected",
    )
    assert mention is not None
    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert "rejection_response" in payload
    assert payload["edit_reason"] == "rejected"


def test_non_rejected_edit_mention_omits_rejection_response_field():
    """A normal (user_comment) edit must not advertise rejection_response."""
    mention = _build_edit_mention()
    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert "rejection_response" not in payload


def test_current_revision_review_ignores_stale_revision(monkeypatch):
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "revision_no": 1,
            "verdict": "issues",
            "findings": "[]",
            "comment": "stale",
        },
    )
    assert _load_current_revision_review({
        "doc_id": "test.none.0002.0004-D",
        "revision_no": 2,
    }) is None


def test_current_revision_review_parses_findings(monkeypatch):
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "revision_no": 2,
            "verdict": "issues",
            "findings": '[{"locus":"Scope","note":"Add pass criteria"}]',
            "comment": "revise",
            "reviewed_at": "2026-06-10T00:00:00",
        },
    )
    review = _load_current_revision_review({
        "doc_id": "test.none.0002.0004-D",
        "revision_no": 2,
    })
    assert review is not None
    assert review["findings"] == [{"locus": "Scope", "note": "Add pass criteria"}]


def test_outbound_review_shape_exposes_structured_findings():
    shaped = _shape_review({
        "id": 7,
        "revision_no": 2,
        "reviewer_id": "reviewer",
        "verdict": "issues",
        "findings": '[{"locus":"Scope","note":"Add pass criteria"}]',
        "comment": "revise",
        "reviewed_at": "2026-06-10T00:00:00",
        "created_at": "2026-06-10T00:00:00",
    })
    assert shaped["finding_count"] == 1
    assert shaped["findings"] == [{"locus": "Scope", "note": "Add pass criteria"}]
