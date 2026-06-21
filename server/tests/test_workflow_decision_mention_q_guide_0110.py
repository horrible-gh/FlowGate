"""Group 0110 B0001/NR0003: the non-continuous workflow-decision mention must carry
the same Q-registration guidance the other worker mentions do.

Before this fix, ``build_workflow_decision_mention``'s non-continuous branch used inline
text that only told the worker to "explain what context is missing" — it carried neither
the embedded ``POST .../q/{R}/questions`` nor the "register a Q" guidance, and it leaked
English in ko/ja. The bottom Reminder section was also continuous-only. These tests pin
the alignment with build_mention / build_review_mention (shared _clarification_guide_body
+ _no_choices_reminder), the R-root anchor, and the locale routing — without regressing
the continuous (delegation/unmanned) branch.
"""

from __future__ import annotations

import os

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.services import mention_service


_TARGET_DOC = {
    "doc_id": "flowgate.default.0110.0001-B",
    "module": "default",
    "type_code": "R",
    "seq": 1,
    "title": "워크플로 결정 멘트복사에 Q 안내가 없음",
}
_TOKEN_REC = {
    "project": "flowgate",
    "group_id": "flowgate.default.0110",
    "scratch_dir": "C:/scratch/tok-1",
}
_API_BASE = "http://localhost/flowgate/api/v1"
_RAW_TOKEN = "worker-token-xyz"


def _build(locale: str = "ko", *, continuous: bool = False, review: bool = False) -> str:
    return mention_service.build_workflow_decision_mention(
        token_rec=_TOKEN_REC,
        target_doc=_TARGET_DOC,
        api_base_url=_API_BASE,
        raw_token=_RAW_TOKEN,
        locale=locale,
        continuous=continuous,
        continuous_review_mode=review,
    )


def test_non_continuous_embeds_q_post_on_r_root_anchor():
    mention = _build("ko")
    # Q registration POST embedded, anchored on the R-root doc_id (= the workflow_decide
    # token's doc_ref, accepted by the Q endpoint per NR0003 §타당성 검증).
    assert (
        "POST http://localhost/flowgate/api/v1/q/flowgate.default.0110.0001-B/questions"
        in mention
    )
    # token embedded so it is ready-to-use
    assert f"Authorization: Bearer {_RAW_TOKEN}" in mention
    # the positive "register a Q" guidance is present (was missing before)
    assert "register a Q" in mention


def test_non_continuous_has_bottom_reminder():
    mention = _build("ko")
    # The bottom Reminder section existed only for the continuous branch before.
    assert "## Reminder" in mention
    # recency repeat carries the Q POST too
    assert mention.count(
        "q/flowgate.default.0110.0001-B/questions"
    ) >= 2


def test_locale_routing_no_english_leak():
    ko = _build("ko")
    ja = _build("ja")
    en = _build("en")
    # locale-specific lead lines from _CLARIFY_TEXT
    assert "추측하거나 가정으로 진행하지 마십시오" in ko
    assert "推測せず" in ja
    assert "do NOT guess and do NOT proceed on assumptions" in en
    # the old English-fixed inline sentence must be gone in every locale
    for mention in (ko, ja, en):
        assert "Explain what context is missing so the user can clarify it" not in mention


def test_continuous_branch_keeps_delegation_block_no_q_guide():
    mention = _build("ko", continuous=True)
    # continuous mode REPLACES the Q guide with the unmanned/delegation block
    assert "무인(UNMANNED)" in mention
    assert "register a Q" not in mention
    # and does not embed the Q-registration POST
    assert "/questions" not in mention


def test_continuous_review_branch_keeps_review_block():
    mention = _build("ko", continuous=True, review=True)
    # review mode keeps the pre-flight review (Q-latitude) block, not the inline guide
    assert "사전 검토 단계" in mention
    assert "Explain what context is missing so the user can clarify it" not in mention
