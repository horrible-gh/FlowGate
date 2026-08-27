from __future__ import annotations

import json
import os

# token_routes imports `config.settings`, which is a pydantic Settings with four required
# fields. Every module in this suite that reaches the same import declares them first; this
# one relied on an earlier-collected module having done it, so running the T0007 §4-4
# command (this file plus two others) on its own died at collection instead of at a test.
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from modules.flow_gate.api.token_routes import _load_current_revision_review  # noqa: E402
from modules.flow_gate.api.v1.document_routes import _shape_review  # noqa: E402
from modules.flow_gate.services.mention_service import (  # noqa: E402
    build_mention,
    build_mention_from_token_rec,
)


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


# ── 0458 T0007 §3.2-1: the rejected edit mention names the review row it answers ──────
#
# `rejection_response` alone told the server WHAT the worker did, never WHICH rejection it
# was answering, so the recorder could only guess "the last one". A human rejection landing
# after the reviewed one then collected the AI's answer.


def _rejected_edit_payload(current_review: dict | None = None, locale: str = "ko",
                           revision_no: int = 0) -> dict:
    mention = build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="Rejected design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        parent_revision_no=revision_no,
        head_type="",
        head_status="",
        scratch_dir="D:/scratch/token",
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
        edit_reason="rejected",
        current_review=current_review,
        locale=locale,
    )
    assert mention is not None
    registration = mention[mention.index("## Artifact registration"):]
    return json.loads(registration[registration.index("{"):registration.index("}") + 1])


def test_rejected_edit_mention_names_the_review_row_it_answers():
    payload = _rejected_edit_payload({
        "id": 244,
        "revision_no": 0,
        "verdict": "issues",
        "findings": [{"locus": "Scope", "note": "Add pass criteria"}],
        "comment": "revise",
    })
    assert payload["review_id"] == 244
    assert payload["edit_reason"] == "rejected"
    assert "rejection_response" in payload


def test_a_normal_edit_mention_names_no_review_row():
    """review_id belongs to the rejection-rework path only — a user_comment edit answers
    no rejection, so advertising the field would invite a meaningless value."""
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
        current_review={"id": 244, "revision_no": 0, "verdict": "issues", "findings": []},
    )
    assert mention is not None
    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert "review_id" not in payload
    assert "rejection_response" not in payload


def test_an_unidentifiable_review_row_adds_no_field(monkeypatch):
    """No current review, no id, a blank id, a bool — none of these identify a row, and a
    made-up identifier is worse than none: it would match nothing and silently drop the
    response instead of falling back to the legacy latest-item policy."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc", lambda _doc_id: None
    )
    assert "review_id" not in _rejected_edit_payload(None)
    for review in ({"verdict": "issues"}, {"id": None}, {"id": ""}, {"id": "   "},
                   {"id": True}, {"id": "abc"}, {"id": "244a"}, {"id": 3.5}, {"id": -1},
                   {"id": 0}):
        payload = _rejected_edit_payload({"revision_no": 0, "findings": [], **review})
        assert "review_id" not in payload, review


def test_a_lookup_failure_costs_the_field_not_the_mention(monkeypatch):
    """The id is an optional field on an otherwise complete mention. A store that raises must
    cost that field and nothing else — a worker with no mention has no work at all."""
    def _boom(_doc_id):
        raise RuntimeError("document_reviews is unreachable")

    monkeypatch.setattr("modules.flow_gate.db.document_reviews.get_latest_by_doc", _boom)
    payload = _rejected_edit_payload({"revision_no": 0, "verdict": "issues", "findings": []})
    assert "review_id" not in payload
    assert "rejection_response" in payload


def test_a_review_id_that_round_tripped_as_a_string_is_kept_as_sent():
    payload = _rejected_edit_payload({"id": "244", "revision_no": 0, "findings": []})
    assert payload["review_id"] == "244"


def test_the_field_is_locale_independent():
    """The placeholder text is localized; the identifier is not a sentence."""
    for locale in ("ko", "ja", "en"):
        payload = _rejected_edit_payload({"id": 244, "revision_no": 0, "findings": []},
                                         locale=locale)
        assert payload["review_id"] == 244


def _review_row(**over) -> dict:
    row = {
        "id": 244,
        "revision_no": 2,
        "verdict": "issues",
        "findings": '[{"locus":"Scope","note":"Add pass criteria"}]',
        "comment": "revise",
        "reviewed_at": "2026-06-10T00:00:00",
    }
    row.update(over)
    return row


def test_the_mention_resolves_the_row_id_itself_on_the_real_path(monkeypatch):
    """The REAL `current_review` — the dict `_load_current_revision_review` hands the mention
    builder — has no `id` in it: it is a display bundle (verdict, findings, comment). So the
    mention cannot wait to be given the id; it resolves the row itself, from the same table
    and by the same rule (the document's latest review, accepted only if it targets the
    revision this mention is about). Without this the field has no producer on the real path
    and the server is back to answering whatever rejection happens to be last."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(),
    )
    review = _load_current_revision_review({
        "doc_id": "test.none.0002.0004-D",
        "revision_no": 2,
    })
    assert review is not None
    assert "id" not in review                     # the caller really does not carry it
    assert _rejected_edit_payload(review, revision_no=2)["review_id"] == 244


def test_a_second_row_landing_between_the_caller_read_and_the_mention_is_shown_consistently(
    monkeypatch,
):
    """0458 T0008 rev4 rework: rev1-rev3 tried to run the display fetch (`_load_current_revision_
    review`, called by the token boundary) and a resolver fallback SELECT as two INDEPENDENT
    queries, then compare their fields to decide whether the second one could be trusted — but
    two distinct rows for the same revision can be byte-identical in every field a mention
    prints while still holding different ids (proven directly by
    `test_identical_content_rows_still_get_their_own_id_not_a_borrowed_one` below), so no amount
    of field comparison closes the race. This TR removes the comparison instead of tightening
    it: `resolve_current_review_and_row_id` performs exactly ONE query and uses ITS OWN content
    for everything the mention prints, so the printed content and the id can never disagree
    about which row they name — whichever row a second, later-landing review event makes
    "latest" by the time the mention builder runs is the row shown, in full, together with its
    own id.

    This reproduces the real shape of the former race (`token_routes.py` is out of this TR's
    assigned scope, T0007 §1, so the caller's own read cannot carry the id forward): row A (id
    244) is what the caller displayed a moment ago; by the time the mention builder queries,
    row B (id 999, same `reviewed_at`, a different `comment`) has landed for the same revision.
    The mention must show row B's own content (not row A's stale content) paired with id 999 —
    never a mix of the two."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(),
    )
    review = _load_current_revision_review({          # the caller's own read: row A (id 244)
        "doc_id": "test.none.0002.0004-D",
        "revision_no": 2,
    })
    assert review is not None
    assert "id" not in review

    # A second review row for the same revision, same reviewed_at, different content and id
    # lands before the mention builder's own SELECT.
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(id=999, comment="a different rejection entirely"),
    )
    mention = build_mention(
        project="test", module="none", group="0002", parent_type="D",
        parent_doc_number="D0004", parent_title="Rejected design", parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D", parent_revision_no=2,
        head_type="", head_status="", scratch_dir="D:/scratch/token", raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1", action_scope="edit",
        edit_reason="rejected", current_review=review,
    )
    assert mention is not None
    assert "Overall comment: a different rejection entirely" in mention   # row B's own content
    assert "Overall comment: revise" not in mention                       # never row A's (stale)
    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert payload["review_id"] == 999              # row B's own id — paired with its own content


def test_identical_content_rows_still_get_their_own_id_not_a_borrowed_one(monkeypatch):
    """The regression the reviewer asked for directly: two document_reviews rows for the same
    revision can be byte-identical in every field the mention prints (`verdict`/`comment`/
    `findings`/`reviewed_at` all equal — `reviewed_at` is JST second-precision with no
    uniqueness constraint, so two automated reviews a moment apart, or a retry that reproduces
    the same finding text, really can collide) while still being different physical rows. A
    comparison-based guard (rev1-rev3) cannot tell these apart by definition — there is nothing
    to compare that differs. This design does not need to: whichever of the two indistinguishable
    rows the single query actually reads back is the one named, every time, because there is
    only ever one read backing both the content and the id."""
    for row_id in (244, 999):
        monkeypatch.setattr(
            "modules.flow_gate.db.document_reviews.get_latest_by_doc",
            lambda _doc_id, _row_id=row_id: _review_row(id=_row_id),
        )
        payload = _rejected_edit_payload({
            "revision_no": 2, "verdict": "issues",
            "findings": [{"locus": "Scope", "note": "Add pass criteria"}],
            "comment": "revise",
            "reviewed_at": "2026-06-10T00:00:00",
        }, revision_no=2)
        assert payload["review_id"] == row_id


def test_the_content_and_the_id_come_from_the_same_single_query(monkeypatch):
    """Structural guarantee behind the two tests above: when `current_review` carries no id (the
    real path), the mention builder must read `document_reviews` exactly ONCE to answer both
    "what do I print" and "what id do I send" — a second, independent query is exactly the shape
    of race this TR removes."""
    calls = []

    def _tracked(doc_id):
        calls.append(doc_id)
        return _review_row()

    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc", _tracked
    )
    payload = _rejected_edit_payload(
        {"revision_no": 2, "verdict": "issues", "findings": []}, revision_no=2
    )
    assert payload["review_id"] == 244
    assert len(calls) == 1


def test_a_newer_review_event_for_the_same_revision_is_shown_and_named_together(monkeypatch):
    """A genuinely newer review row for the same revision (different `reviewed_at`, not a
    same-second collision) is not a stale-vs-new inconsistency under this design — it is simply
    the row the mention builder's single query reads, shown with its own content and its own id
    together. There is no `reviewed_at` comparison left to fail: matching `revision_no` and
    reading the row's own fields is the whole rule."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(id=999, reviewed_at="2026-06-11T00:00:00"),
    )
    mention = build_mention(
        project="test", module="none", group="0002", parent_type="D",
        parent_doc_number="D0004", parent_title="Rejected design", parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D", parent_revision_no=2,
        head_type="", head_status="", scratch_dir="D:/scratch/token", raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1", action_scope="edit",
        edit_reason="rejected",
        current_review={
            "revision_no": 2, "verdict": "issues", "findings": [],
            "reviewed_at": "2026-06-10T00:00:00",   # what the caller's own stale read had
        },
    )
    assert mention is not None
    registration = mention[mention.index("## Artifact registration"):]
    payload = json.loads(registration[registration.index("{"):registration.index("}") + 1])
    assert payload["review_id"] == 999


def test_the_fallback_still_names_the_row_when_no_reviewed_at_was_displayed(monkeypatch):
    """A caller with no `reviewed_at` in its display bundle at all (not even the key) is still
    named correctly — the single query does not look at `reviewed_at` to decide anything, only
    `revision_no`, so a caller that never carried the key behaves exactly like one that did."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(),
    )
    payload = _rejected_edit_payload(
        {"revision_no": 2, "verdict": "issues", "findings": []}, revision_no=2
    )
    assert payload["review_id"] == 244


def test_a_review_row_from_another_revision_is_not_the_one_being_answered(monkeypatch):
    """The same guard the caller applies before deciding there is a review to show at all: a
    row left over from an earlier revision answers a rejection this rework is not about."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(revision_no=1),
    )
    payload = _rejected_edit_payload({"revision_no": 2, "verdict": "issues", "findings": []},
                                     revision_no=2)
    assert "review_id" not in payload


def test_an_id_supplied_by_the_caller_outranks_the_lookup(monkeypatch):
    """If a caller ever does carry the id, that is the review the mention is printing and no
    query may talk it out of it."""
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda _doc_id: _review_row(id=999),
    )
    payload = _rejected_edit_payload({"id": 244, "revision_no": 0, "findings": []})
    assert payload["review_id"] == 244


def test_the_lookup_never_runs_for_a_normal_edit(monkeypatch):
    """A user_comment edit answers no rejection, so it must not even ask."""
    asked = []
    monkeypatch.setattr(
        "modules.flow_gate.db.document_reviews.get_latest_by_doc",
        lambda doc_id: asked.append(doc_id) or _review_row(),
    )
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
        current_review={"revision_no": 0, "verdict": "issues", "findings": []},
    )
    assert mention is not None
    assert asked == []


def _token_path_payload(history: list, current_review: dict | None = None) -> dict:
    mention = build_mention_from_token_rec(
        token_rec={
            "project": "test", "group_id": "test.none.0002",
            "scratch_dir": "D:/scratch/token",
        },
        head_type="", head_status="",
        parent_doc={
            "doc_id": "test.none.0002.0004-D", "type_code": "D", "seq": 4,
            "title": "Rejected design", "module": "none", "revision_no": 2,
            "rejection_history": json.dumps(history),
        },
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        raw_token="raw-token", action_scope="edit", edit_reason="rejected",
        current_review=current_review,
    )
    assert mention is not None
    registration = mention[mention.index("## Artifact registration"):]
    return json.loads(registration[registration.index("{"):registration.index("}") + 1])


def test_latest_manual_rejection_ignores_current_automatic_review_id():
    payload = _token_path_payload(
        [
            {"rejection_id": "rej_auto", "review_id": 244},
            {"rejection_id": "rej_manual"},
        ],
        current_review={"id": 244, "revision_no": 2, "findings": []},
    )
    assert payload["rejection_id"] == "rej_manual"
    assert "review_id" not in payload


def test_manual_rejection_without_review_row_carries_rejection_id():
    payload = _token_path_payload([{"rejection_id": "rej_manual"}])
    assert payload["rejection_id"] == "rej_manual"
    assert "review_id" not in payload


def test_automatic_rejection_carries_both_ids_from_same_history_item():
    payload = _token_path_payload(
        [{"rejection_id": "rej_auto", "review_id": 244}],
        current_review={"id": 999, "revision_no": 2, "findings": []},
    )
    assert payload["rejection_id"] == "rej_auto"
    assert payload["review_id"] == 244
