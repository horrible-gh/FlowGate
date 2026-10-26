"""build_review_mention: the [Request review] worker mention.

Distinct genre from build_mention: it asks a worker to REVIEW the target document and
submit a verdict via inbox action:review, rather than to CREATE the next document.
"""
import json

from modules.flow_gate.services import mention_service


_TOKEN_REC = {
    "project": "test",
    "group_id": "test.none.0002",
    "scratch_dir": "D:\\test\\storage2\\work\\test\\tok_x",
}
_TARGET = {
    "doc_id": "test.none.0002.0003-DS",
    "type_code": "DS",
    "seq": 3,
    "title": "플로게이트 검증 테스트 설계지시",
    "module": "none",
    "project_id": "test",
}


def _build():
    return mention_service.build_review_mention(
        token_rec=_TOKEN_REC,
        target_doc=_TARGET,
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        raw_token="RAWTOKEN123",
        group_recent_docs=None,
        ref_doc_ids=None,
    )


def test_review_mention_is_review_genre_not_create_next():
    m = _build()
    # It's a review request, not a create-next handoff.
    assert "## Review submission" in m
    assert "## Review instructions" in m
    assert "Instruction to include next document header" not in m
    assert "next_type" not in m


def test_review_mention_submission_payload_is_action_review():
    m = _build()
    # group 0372 set 3 (D-0003 §3-2 "결과 등록: 남김(최소)"): the submission section
    # keeps the address + credential and points at the `submit` help item for the
    # action:review body; the verdict semantics stay inline in the Verdict guide.
    assert "Submit your review: POST http://127.0.0.1:8088/flowgate/api/v1/inbox" in m
    assert "help/items/submit" in m
    assert '"action": "review"' not in m
    # Verdict guide still names the enum inline (L-0006 keeps this purpose slot).
    assert "verdict values:" in m
    for value in ("- pass", "- issues", "- hold"):
        assert value in m
    assert "findings" in m
    # No <Sequence undecided> placeholder — review needs no sequence resolution.
    assert "<Sequence undecided>" not in m


def test_review_mention_includes_target_get_and_token():
    m = _build()
    # Worker must be able to read the doc and authenticate the submission.
    assert "GET http://127.0.0.1:8088/flowgate/api/v1/document/test.none.0002.0003-DS" in m
    assert "Bearer RAWTOKEN123" in m
    # T0004 (0506): the Scratch directory section stays active (workers still need
    # {SCRATCH} guidance for file artifacts) but no longer interpolates the actual
    # server-local absolute path — that stays internal/runtime metadata only.
    assert "D:\\test\\storage2\\work\\test\\tok_x" not in m
    assert "{SCRATCH}" in m
