"""단계별 확인 (step verification) section check — flowgate.default.0467 R0001/T0002."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import step_verification_service as svs  # noqa: E402

_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")


# ── parse_step_verification ───────────────────────────────────────────────────

def test_section_absent_is_not_found():
    parsed = svs.parse_step_verification("no such section here")
    assert parsed.found is False
    assert parsed.sections == []
    assert parsed.declared_none is False


def test_none_marker_is_an_empty_but_present_report():
    parsed = svs.parse_step_verification("## 단계별 확인\n\n없음\n")
    assert parsed.found is True
    assert parsed.declared_none is True
    assert parsed.sections == []
    assert parsed.format_errors == []


def test_section_stops_at_next_level_1_or_2_heading_but_not_at_level_3():
    body = (
        "## 단계별 확인\n\n"
        "### 첫 섹션\n"
        "- 개요: 한줄\n"
        "- 스텝: 해봐라\n"
        "  - 기대치: 이렇게 된다\n"
        "\n"
        "## 다음 절\n\n이건 다른 절이다\n"
    )
    parsed = svs.parse_step_verification(body)
    assert parsed.found is True
    assert len(parsed.sections) == 1
    assert parsed.sections[0].title == "첫 섹션"


def test_a_single_section_with_multiple_steps_and_expectations():
    body = (
        "## 단계별 확인\n\n"
        "### 로그인 확인\n"
        "- 개요: 잘못된 비밀번호면 오류가 뜬다\n"
        "- 스텝: 아무 비밀번호로 로그인 시도\n"
        "  - 기대치: 오류 문구가 뜬다\n"
        "  - 기대치: 로그인은 되지 않는다\n"
        "- 스텝: 올바른 비밀번호로 재시도\n"
        "  - 기대치: 로그인된다\n"
    )
    parsed = svs.parse_step_verification(body)
    assert parsed.found is True
    assert parsed.declared_none is False
    assert len(parsed.sections) == 1
    section = parsed.sections[0]
    assert section.title == "로그인 확인"
    assert section.summary == "잘못된 비밀번호면 오류가 뜬다"
    assert [s.description for s in section.steps] == ["아무 비밀번호로 로그인 시도", "올바른 비밀번호로 재시도"]
    assert section.steps[0].expectations == ["오류 문구가 뜬다", "로그인은 되지 않는다"]
    assert section.steps[1].expectations == ["로그인된다"]
    assert parsed.format_errors == []


def test_multiple_sections_are_all_parsed():
    body = (
        "## 단계별 확인\n\n"
        "### 첫 섹션\n"
        "- 개요: 하나\n"
        "- 스텝: 해봐라\n"
        "  - 기대치: 된다\n"
        "\n"
        "### 둘째 섹션\n"
        "- 개요: 둘\n"
        "- 스텝: 또 해봐라\n"
        "  - 기대치: 또 된다\n"
    )
    parsed = svs.parse_step_verification(body)
    assert [s.title for s in parsed.sections] == ["첫 섹션", "둘째 섹션"]


def test_missing_summary_is_a_format_error():
    body = "## 단계별 확인\n\n### 섹션\n- 스텝: 해봐라\n  - 기대치: 된다\n"
    parsed = svs.parse_step_verification(body)
    assert any("개요" in e for e in parsed.format_errors)


def test_multiple_summary_lines_is_a_format_error():
    body = (
        "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n- 개요: 둘\n"
        "- 스텝: 해봐라\n  - 기대치: 된다\n"
    )
    parsed = svs.parse_step_verification(body)
    assert any("두 번 이상" in e for e in parsed.format_errors)
    assert parsed.sections[0].summary == "하나"  # first one wins


def test_section_with_no_steps_is_a_format_error():
    body = "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n"
    parsed = svs.parse_step_verification(body)
    assert any("스텝" in e and "없습니다" in e for e in parsed.format_errors)


def test_step_without_expectation_is_a_format_error():
    body = "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n- 스텝: 해봐라\n"
    parsed = svs.parse_step_verification(body)
    assert any("기대치" in e for e in parsed.format_errors)


def test_orphan_expectation_before_any_step_is_a_format_error():
    body = "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n  - 기대치: 앞선 스텝이 없다\n- 스텝: 해봐라\n  - 기대치: 된다\n"
    parsed = svs.parse_step_verification(body)
    assert any("앞선 스텝 없이" in e for e in parsed.format_errors)


def test_unrecognized_line_is_a_format_error():
    body = "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n- 스텝: 해봐라\n  - 기대치: 된다\n아무 줄\n"
    parsed = svs.parse_step_verification(body)
    assert any("알아볼 수 없는 줄" in e for e in parsed.format_errors)


def test_declaring_none_alongside_real_sections_is_a_format_error():
    body = "## 단계별 확인\n\n없음\n\n### 섹션\n- 개요: 하나\n- 스텝: 해봐라\n  - 기대치: 된다\n"
    parsed = svs.parse_step_verification(body)
    assert any("함께 선언" in e for e in parsed.format_errors)
    # sections win — declared_none is not honored once a real section exists
    assert parsed.declared_none is False
    assert len(parsed.sections) == 1


def test_english_heading_and_field_aliases_are_accepted():
    body = (
        "## Step Verification\n\n"
        "### Login check\n"
        "- Summary: an error shows on a wrong password\n"
        "- Step: try logging in with any wrong password\n"
        "  - Expected: an error message is shown\n"
    )
    parsed = svs.parse_step_verification(body, "en")
    assert parsed.found is True
    assert parsed.format_errors == []
    assert parsed.sections[0].summary == "an error shows on a wrong password"
    assert parsed.sections[0].steps[0].expectations == ["an error message is shown"]


def test_none_aliases_none_and_n_a_are_accepted():
    for marker in ["없음", "none", "None", "N/A", "- none"]:
        parsed = svs.parse_step_verification(f"## 단계별 확인\n\n{marker}\n")
        assert parsed.declared_none is True, marker


# ── evaluate ─────────────────────────────────────────────────────────────────

def test_evaluate_rejects_a_missing_section():
    result = svs.evaluate("just a body, no section")
    assert result["verdict"] == svs.VERDICT_REJECT
    assert svs.SVV_MISSING_SECTION in result["codes"]
    assert result["notice"]


def test_evaluate_rejects_a_present_but_empty_section():
    result = svs.evaluate("## 단계별 확인\n\n")
    assert result["verdict"] == svs.VERDICT_REJECT
    assert svs.SVV_MISSING_SECTION in result["codes"]


def test_evaluate_passes_a_declared_none():
    result = svs.evaluate("## 단계별 확인\n\n없음\n")
    assert result["verdict"] == svs.VERDICT_PASS
    assert result["codes"] == []
    assert "notice" not in result


def test_evaluate_passes_a_well_formed_section():
    body = (
        "## 단계별 확인\n\n### 섹션\n- 개요: 하나\n"
        "- 스텝: 해봐라\n  - 기대치: 된다\n"
    )
    result = svs.evaluate(body)
    assert result["verdict"] == svs.VERDICT_PASS
    assert result["sections"][0]["title"] == "섹션"


def test_evaluate_rejects_on_format_error_even_when_section_exists():
    body = "## 단계별 확인\n\n### 섹션\n- 스텝: 해봐라\n  - 기대치: 된다\n"  # no summary
    result = svs.evaluate(body)
    assert result["verdict"] == svs.VERDICT_REJECT
    assert svs.SVV_FORMAT in result["codes"]


# ── locale text: no Korean leak in the English branch ───────────────────────

def _assert_no_korean(text: str) -> None:
    assert not _HANGUL.search(text), text


def test_notice_en_has_no_korean():
    result = svs.evaluate("no section", "en")
    _assert_no_korean(result["notice"])
    assert "## Step Verification" in result["notice"]


def test_section_guide_en_has_no_korean():
    _assert_no_korean(svs.section_guide("en"))


def test_section_placeholder_en_has_no_korean():
    _assert_no_korean(svs.section_placeholder("en"))


def test_section_guide_and_placeholder_ko_default_unchanged():
    assert svs.SECTION_HEADING in svs.section_guide("ko")
    assert svs.SECTION_HEADING in svs.section_placeholder("ko")
    assert svs.NONE_MARKER in svs.section_placeholder("ko")


def test_unknown_locale_falls_back_to_korean():
    assert svs.section_guide("zh") == svs.section_guide("ko")
    assert svs.build_notice({"codes": [svs.SVV_MISSING_SECTION]}, "zh") == \
        svs.build_notice({"codes": [svs.SVV_MISSING_SECTION]}, "ko")


# ── GET /{doc_id}/step-verification route (documents.py) ────────────────────
# Same harness shape as test_tr_scope_0299.py's _call_get_document/_stub_document_detail
# — the route handler is called directly, with only document_service.get_document and
# _document_file_path stubbed, so the real parser runs against a real temp file.

def _call_get_step_verification(documents_router, doc_id: str) -> dict:
    handler = getattr(
        documents_router.get_step_verification, "__wrapped__", documents_router.get_step_verification
    )
    return handler(doc_id, current_user={"user_id": "u"})


def _stub_document(monkeypatch, documents_router, row: dict, body: str, tmp_path):
    doc_file = tmp_path / "doc.md"
    doc_file.write_text(body, encoding="utf-8")
    monkeypatch.setattr(documents_router.document_service, "get_document", lambda doc_id: dict(row))
    monkeypatch.setattr(documents_router, "_document_file_path", lambda doc: doc_file)


def test_route_reports_found_false_when_the_document_has_no_section(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as documents_router

    row = {"doc_id": "flowgate.default.0467.0001-TR", "type_code": "TR", "file_path": "x"}
    _stub_document(monkeypatch, documents_router, row, "# body only, no section\n", tmp_path)

    out = _call_get_step_verification(documents_router, row["doc_id"])

    assert out["data"]["found"] is False
    assert out["data"]["sections"] == []


def test_route_serves_parsed_sections_live_from_the_body_file(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as documents_router

    row = {"doc_id": "flowgate.default.0467.0002-TR", "type_code": "TR", "file_path": "x"}
    body = (
        "# TR\n\n## 단계별 확인\n\n### 로그인 확인\n"
        "- 개요: 요약\n- 스텝: 해봐라\n  - 기대치: 된다\n"
    )
    _stub_document(monkeypatch, documents_router, row, body, tmp_path)

    out = _call_get_step_verification(documents_router, row["doc_id"])

    assert out["data"]["found"] is True
    assert out["data"]["declared_none"] is False
    assert out["data"]["sections"][0]["title"] == "로그인 확인"
    assert out["data"]["sections"][0]["steps"][0]["expectations"] == ["된다"]


def test_route_reports_declared_none(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as documents_router

    row = {"doc_id": "flowgate.default.0467.0003-TR", "type_code": "TR", "file_path": "x"}
    _stub_document(monkeypatch, documents_router, row, "## 단계별 확인\n\n없음\n", tmp_path)

    out = _call_get_step_verification(documents_router, row["doc_id"])

    assert out["data"]["found"] is True
    assert out["data"]["declared_none"] is True


# ── inbox gate wiring (TR-only, both new and edit) ───────────────────────────

def test_inbox_new_rejects_a_tr_missing_the_section():
    from modules.flow_gate.api import inbox_routes

    result = inbox_routes.step_verification_service.evaluate("no section here", locale="ko")
    assert result["verdict"] == svs.VERDICT_REJECT


def test_inbox_gate_is_scoped_to_tr_only():
    """T/TSR/TS are not gated by this check even though TR shares MUTATING_STEP_TYPES with
    them — R0001's complaint was specifically about work reports (TR)."""
    from modules.flow_gate.services import help_catalog

    base = {"principal_kind": "worker_token", "action_scope": "new"}
    assert help_catalog.decide_visibility(
        "step_verification_format", dict(base, doc_type="TR")
    ).visible is True
    for other in ("T", "TSR", "TS"):
        decision = help_catalog.decide_visibility(
            "step_verification_format", dict(base, doc_type=other)
        )
        assert decision.visible is False, other
