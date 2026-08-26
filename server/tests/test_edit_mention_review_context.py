from __future__ import annotations

import json

import pytest

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
        # 0391 T0005 §7-2: optional body fingerprint (proposal 4) + bypass door.
        "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
        "body_chars": "<optional: character count of content>",
        "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
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


# ══════════════════════════════════════════════════════════════════════════════════════
# T0005 2.2.1 — the rejected-edit mention names the review row it answers, from ONE query
# ══════════════════════════════════════════════════════════════════════════════════════

def _build_rejected_edit_mention(current_review: dict | None, *, parent_revision_no: int = 0) -> str:
    mention = build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="Rejected design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        parent_revision_no=parent_revision_no,
        head_type="",
        head_status="",
        scratch_dir="D:/scratch/token",
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
        edit_reason="rejected",
        current_review=current_review,
    )
    assert mention is not None
    return mention


def _payload(mention: str) -> dict:
    registration = mention[mention.index("## Artifact registration"):]
    return json.loads(registration[registration.index("{"):registration.index("}") + 1])


def _anchor_doc(review_id, *, revision_no: int = 0):
    """A document whose rejection_history's LAST item is keyed to review_id (or not, if
    review_id is None — a keyless/human item instead)."""
    item = {"reason": "r", "rejected_at": "2026-08-26T00:00:00"}
    if review_id is not None:
        item["review_id"] = review_id
    return {
        "doc_id": "test.none.0002.0004-D",
        "revision_no": revision_no,
        "rejection_history": json.dumps([item], ensure_ascii=False),
    }


def test_rejected_edit_names_the_review_row_from_the_authoritative_query(monkeypatch):
    """The id in the POST and the content under '## Review feedback' must come from the
    SAME row — here, the row resolve_current_review_and_row_id re-reads, not the caller's
    (stale/placeholder) current_review bundle."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "id": 55, "revision_no": 0, "verdict": "issues",
            "findings": '[{"locus":"Scope","note":"authoritative note"}]',
            "comment": "authoritative comment", "reviewed_at": "2026-08-26T00:00:00",
        },
    )
    # T0005 rework: the row must also anchor the document's OPEN (last) rejection_history
    # item — this document's is keyed to the same row (55), the ordinary A2-style shape.
    monkeypatch.setattr(
        "modules.flow_gate.db.documents.get_by_id",
        lambda _doc_id: _anchor_doc(55),
    )
    mention = _build_rejected_edit_mention({
        "revision_no": 0, "verdict": "issues", "findings": [], "comment": "stale placeholder",
    })
    assert _payload(mention)["review_id"] == 55
    assert "authoritative comment" in mention
    assert "stale placeholder" not in mention


def test_rejected_edit_omits_review_id_when_the_row_targets_a_different_revision(monkeypatch):
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {"id": 55, "revision_no": 5, "verdict": "issues",
                        "findings": "[]", "comment": "wrong revision"},
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "caller bundle"},
        parent_revision_no=0,
    )
    assert "review_id" not in _payload(mention)
    # the caller's own bundle is still shown — no content was invented either
    assert "caller bundle" in mention


def test_rejected_edit_omits_review_id_when_the_row_id_is_not_a_positive_integer(monkeypatch):
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {"id": 0, "revision_no": 0, "verdict": "issues",
                        "findings": "[]", "comment": "c"},
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "c"},
    )
    assert "review_id" not in _payload(mention)


def test_rejected_edit_omits_review_id_when_the_lookup_raises(monkeypatch):
    def _boom(_doc_id):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc", _boom,
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "c"},
    )
    assert "review_id" not in _payload(mention)
    assert "rejection_response" in _payload(mention), "the rest of the mention still builds"


def test_non_rejected_edit_never_looks_up_a_review_row(monkeypatch):
    """A normal edit answers no rejection — the resolver must not even query, so a broken
    lookup here must not be able to break an ordinary edit mention."""
    def _boom(_doc_id):
        raise RuntimeError("must not be called")

    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc", _boom,
    )
    mention = _build_edit_mention({
        "revision_no": 0, "verdict": "issues", "findings": [], "comment": "c",
    })
    assert "review_id" not in _payload(mention)


# ══════════════════════════════════════════════════════════════════════════════════════
# Rejection review feedback (group 0466 TR0006 rev0) — a current-revision row must also
# ANCHOR the document's open rejection before its id is trusted, not merely match the
# revision. Otherwise a current `issues` row sitting next to a keyless HUMAN rejection
# gets its id attached to a response meant for that human rejection, and
# record_rejection_response finds no keyed item to update and silently drops it.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_rejected_edit_omits_review_id_for_a_keyless_human_rejection_even_with_a_current_issues_row(
    monkeypatch,
):
    """A7 shape: a current-revision `issues` row exists, but the document's actual open
    rejection is a keyless human one (mark_revised / reject button, no review_id passed).
    The mention must NOT attach the issues row's id — doing so sends
    record_rejection_response looking for a keyed item that was never written, and the
    response is silently dropped instead of landing on history[-1] via the legacy path."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "id": 91, "revision_no": 0, "verdict": "issues",
            "findings": "[]", "comment": "an issues row exists",
        },
    )
    monkeypatch.setattr(
        "modules.flow_gate.db.documents.get_by_id",
        lambda _doc_id: _anchor_doc(None),  # last item has no review_id key at all
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "caller bundle"},
    )
    assert "review_id" not in _payload(mention)
    # no id was invented, but the mention still shows a review — the caller's own bundle.
    assert "caller bundle" in mention


def test_rejected_edit_omits_review_id_when_the_open_rejection_is_anchored_to_a_different_row(
    monkeypatch,
):
    """A stale current-revision row: a NEWER issues row exists but the open rejection in
    history was produced by an OLDER one (e.g. a human rejected again after a fresh
    review landed). The newer row's id must not be borrowed for that older rejection."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "id": 92, "revision_no": 0, "verdict": "issues",
            "findings": "[]", "comment": "newer row",
        },
    )
    monkeypatch.setattr(
        "modules.flow_gate.db.documents.get_by_id",
        lambda _doc_id: _anchor_doc(41),  # open rejection belongs to a DIFFERENT row
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "caller bundle"},
    )
    assert "review_id" not in _payload(mention)


@pytest.mark.parametrize("verdict", ["pass", "hold"])
def test_rejected_edit_omits_review_id_when_the_current_row_is_not_an_issues_verdict(
    monkeypatch, verdict,
):
    """resolve_review_gate only ever auto-rejects on an `issues` verdict — a pass/hold row
    structurally cannot have produced the open rejection, so its id must never be sent
    even if (defensively) the history happened to name it."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: {
            "id": 77, "revision_no": 0, "verdict": verdict,
            "findings": "[]", "comment": "not an issues row",
        },
    )
    monkeypatch.setattr(
        "modules.flow_gate.db.documents.get_by_id",
        lambda _doc_id: _anchor_doc(77),
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": verdict, "findings": [], "comment": "caller bundle"},
    )
    assert "review_id" not in _payload(mention)


def test_rejected_edit_review_id_resolves_from_a_single_query_with_no_stale_content_leak(
    monkeypatch,
):
    """Guards the two-query race directly: `document_reviews.get_latest_by_doc` must be
    called exactly ONCE. A second (later) call standing in for "a new row landed between
    two reads" would return byte-identical content under a DIFFERENT id — if the resolver
    ever queried twice and used the second answer's content with the first answer's id
    (or vice versa), this would catch the mismatch; querying once makes the mismatch
    structurally impossible instead of merely uncaught."""
    calls: list = []

    def _get_latest(_doc_id):
        calls.append(_doc_id)
        # Every call — first or a hypothetical second — returns the SAME content with a
        # DIFFERENT id, so any second call would be silently indistinguishable from the
        # first by content alone. Only a call-count assertion catches a reintroduced race.
        return {
            "id": 55 + len(calls) - 1, "revision_no": 0, "verdict": "issues",
            "findings": "[]", "comment": "identical content every time",
        }

    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc", _get_latest,
    )
    monkeypatch.setattr(
        "modules.flow_gate.db.documents.get_by_id",
        lambda _doc_id: _anchor_doc(55),
    )
    mention = _build_rejected_edit_mention(
        {"revision_no": 0, "verdict": "issues", "findings": [], "comment": "caller bundle"},
    )
    assert len(calls) == 1, "the resolver must read document_reviews exactly once"
    assert _payload(mention)["review_id"] == 55
    assert "identical content every time" in mention
