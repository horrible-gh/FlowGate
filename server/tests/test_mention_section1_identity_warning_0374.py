"""Identity warning for report predecessors in advance/new worker mentions."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402


_BASE = "http://h/flowgate/api/v1"
_WARNINGS = {
    "ko": (
        "이 정보는 앞 문서(참고용)이며 지금 작성할 문서의 신원이 아닙니다. "
        "실제로 제출할 문서 타입은 아래 next_type을 따르십시오."
    ),
    "ja": (
        "この情報は前の文書（参照用）のものであり、現在作成する文書の識別情報ではありません。"
        "実際に提出する文書タイプは、下の next_type に従ってください。"
    ),
    "en": (
        "This information identifies the preceding document for reference; it is not the "
        "identity of the document you are writing now. Follow next_type below for the "
        "document type to submit."
    ),
}


@pytest.fixture(autouse=True)
def _remote_mode(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)


def _build(
    predecessor_type: str | None,
    *,
    locale: str = "ko",
    action_scope: str = "new",
) -> str:
    text = mention_service.build_mention(
        project="flowgate",
        module="default",
        group="0374",
        parent_type="R",
        parent_doc_number="R0001",
        parent_title="root",
        parent_doc_id="flowgate.default.0374.0001-B",
        head_type="T",
        head_status="pending",
        scratch_dir="S",
        raw_token="RAW",
        api_base_url=_BASE,
        locale=locale,
        action_scope=action_scope,
        head_doc_type=predecessor_type,
        head_doc_number=f"{predecessor_type}0001" if predecessor_type else None,
        head_doc_title="predecessor" if predecessor_type else None,
    )
    assert text is not None
    return text


@pytest.mark.parametrize("predecessor_type", ["TR", "NR", "TSR"])
def test_report_predecessor_adds_identity_warning(predecessor_type):
    text = _build(predecessor_type)

    assert f"type: {predecessor_type}\n" in text
    assert _WARNINGS["ko"] in text
    assert "next_type: T" in text


@pytest.mark.parametrize("predecessor_type", ["N", "T", "TS", "R"])
def test_instruction_predecessor_does_not_add_identity_warning(predecessor_type):
    assert _WARNINGS["ko"] not in _build(predecessor_type)


def test_first_step_without_predecessor_override_has_no_warning():
    text = _build(None)

    assert "type: R\n" in text
    assert _WARNINGS["ko"] not in text


def test_edit_path_ignores_predecessor_override_and_has_no_warning():
    text = _build("TR", action_scope="edit")

    assert "type: R\n" in text
    assert _WARNINGS["ko"] not in text


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_identity_warning_follows_locale(locale):
    text = _build("TR", locale=locale)

    assert _WARNINGS[locale] in text
    for other_locale, warning in _WARNINGS.items():
        if other_locale != locale:
            assert warning not in text
