"""Group the earlier implementation: the worker prescription now points at options-carrying Qs.

Only the PRESCRIPTION (the positive/guidance half) changes. The ⚠️ prohibition block is
untouched: a worker demanding an interactive choice through its own output is still banned,
and options do not soften that — the choice must travel as Q data the user answers in the
[질의 응답] panel. B0001 recurred once already when that line blurred, so these tests pin
both halves: the prohibition survives verbatim, and the prescription names `options`.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.services import mention_service

_API_BASE = "http://localhost/flowgate/api/v1"
_ANCHOR = "flowgate.default.0243.0001-R"
_RAW_TOKEN = "worker-token-xyz"

_LOCALES = ["ko", "ja", "en"]


def _clarify(locale: str) -> str:
    return mention_service._clarification_guide_body(_API_BASE, _ANCHOR, _RAW_TOKEN, locale)


@pytest.mark.parametrize("locale", _LOCALES)
def test_prescription_directs_to_a_single_options_carrying_q(locale):
    body = _clarify(locale)
    assert "options" in mention_service._CLARIFY_TEXT[locale]["positive"]
    # Remove the old workaround of splitting each option into a separate Q.
    assert "각 옵션을 질문으로 변환" not in body
    assert "turn each option into a question" not in body
    assert "各オプションを質問に変換" not in body


@pytest.mark.parametrize("locale", _LOCALES)
def test_prohibition_block_is_unchanged(locale):
    """Preserve every byte of the prohibition block; changing this assertion violates §2.6."""
    warn = mention_service._CLARIFY_TEXT[locale]["warn"]
    assert "⚠️" in warn
    assert "Do NOT present choices" in warn
    assert "force-terminated" in warn
    assert warn in _clarify(locale)


@pytest.mark.parametrize("locale", _LOCALES)
def test_options_example_moved_behind_the_question_help_item(locale):
    """set 3 (D-0003 "move question-submission examples to help"): the guide no longer
    embeds the placeholder POST JSON — it keeps the address + credential and points at
    the `question` help item, whose example now carries the §2.6 duty of demonstrating
    the optional `options` field."""
    from modules.flow_gate.services import help_catalog

    body = _clarify(locale)
    assert f"POST {_API_BASE}/q/{_ANCHOR}/questions" in body
    assert '"asker_kind"' not in body
    assert "help/items/question" in body

    example = help_catalog.build_question_content(locale)["example"]["body"]
    with_options = [q for q in example["questions"] if q.get("options")]
    assert with_options, "the question example must demonstrate the options array"
    assert len(with_options[0]["options"]) == 2
    # The example itself shows that the field is optional.
    assert any(word in with_options[0]["options"][0] for word in ("선택", "任意", "optional"))


@pytest.mark.parametrize("locale", _LOCALES)
def test_reminder_keeps_prohibition_and_gains_options_prescription(locale):
    reminder = mention_service._no_choices_reminder(_API_BASE, _ANCHOR, locale)
    assert "⚠️" in reminder
    assert "options" in reminder


@pytest.mark.parametrize("locale", _LOCALES)
def test_review_mode_guidance_mentions_options(locale):
    text = mention_service._CONTINUOUS_REVIEW_TEXT[locale]
    assert "options" in text
    # Preserve the review rule: do not proceed before a person gives the go-ahead.
    assert "go" in text
