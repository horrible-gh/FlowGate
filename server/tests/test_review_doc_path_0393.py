"""A review verdict may be submitted as a file, like every other document (0393 T0005 §2-7).

B0001's third suspicion — "문서처럼 파일형태 지원 부족?" — was not the cause of the three
failures (NR0003 §4-4), but it was a real gap: document registration has had `doc_path`
since D020 §7-5 while a review verdict could only be typed inline. The overall comment on a
review is long Korean prose; pushed through a command line it turns into the ???? the
encoding guard then rejects, which is a registration lost for a formatting reason.

Covered here, matching T0005 §3 criterion 5 exactly:
  * success — the verdict is read out of a file inside the token's scratch directory,
  * a path outside the scratch directory → 422,
  * `content` and `doc_path` sent together → 400,
plus the two neighbours that make the contract usable: a missing file → 422, and the
backward-compatible inline shape (neither field) still registering, because T0005 §1 puts
the working [멘트 복사] review path out of bounds.

The implementation reuses `validate_doc_path` and `_submission_text` rather than growing a
second answer to "is this path inside the scratch dir" — a duplicate is how one of the two
drifts open — and this file asserts the two error strings are the same ones _handle_new
already returns, so they cannot diverge either.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import document_reviews as db_reviews  # noqa: E402
from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402
from store_transaction_support import install_null_transaction_store  # noqa: E402

DOC_ID = "flowgate.v02.0003.0005-NR"
GROUP_ID = "flowgate.v02.0003"
PROJECT = "flowgate"
USER = "user-1"

# The shape NR0003 recovered from the dead runs' scratch folders: a long Korean overall
# comment plus per-finding notes. This is the payload that could not be typed inline.
VERDICT_PAYLOAD = {
    "verdict": "issues",
    "findings": [
        {"locus": "3-2. 메인 대시보드 내부 기능 영역",
         "note": "선정 기준이 문서 어디에도 없고 실제 화면 대비 누락이 큽니다."},
        {"locus": "1. 조사 배경", "note": "'전수 확인'이라는 표현이 실제 조사 범위와 맞지 않습니다."},
    ],
    "comment": "조사 밀도와 정확도 자체는 높습니다. 다만 목록의 누락이 그대로 결과물의 누락이 됩니다.",
}


@pytest.fixture
def review_env(monkeypatch, tmp_path):
    """Everything _handle_review crosses except the doc_path logic under test."""
    scratch = tmp_path / "tok_review"
    scratch.mkdir()
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {
        "token_id": "tok-review-0393",
        "project": PROJECT,
        "issued_to": USER,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "scratch_dir": str(scratch),
        "ai_run_id": "air_review_0393",
    })
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 2,
        "title": "GUI 모드 전환 대상 화면 목록",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    insert = MagicMock()
    monkeypatch.setattr(db_reviews, "insert_review", insert)
    monkeypatch.setattr(inbox_routes.token_service, "consume", MagicMock())
    # 0535 T0007 §3: the review path claims its token and stores the review inside one
    # store.transaction(). Both writes are mocked here (this file is about doc_path),
    # but the transaction itself is real, so the store has to be able to open one.
    install_null_transaction_store(monkeypatch)
    return {"scratch": scratch, "insert": insert, "outside": tmp_path / "outside"}


def _body(**overrides) -> dict:
    body = {"action": "review", "project": PROJECT, "doc_id": DOC_ID}
    body.update(overrides)
    return body


def _write_payload(directory: Path, name: str = "verdict.json", payload=None) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(
        json.dumps(payload if payload is not None else VERDICT_PAYLOAD, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(target)


# ── criterion 5-1: success ────────────────────────────────────────────────────────────

def test_a_verdict_file_inside_the_scratch_directory_registers(review_env):
    doc_path = _write_payload(review_env["scratch"])

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs["verdict"] == "issues"
    assert kwargs["comment"] == VERDICT_PAYLOAD["comment"]
    # The Korean survives the round trip byte for byte — the whole point of the file form.
    findings = json.loads(kwargs["findings_json"])
    assert len(findings) == 2
    assert findings[0]["locus"] == VERDICT_PAYLOAD["findings"][0]["locus"]
    assert findings[1]["note"] == VERDICT_PAYLOAD["findings"][1]["note"]
    assert kwargs["doc_id"] == DOC_ID
    assert kwargs["reviewer_id"] == USER


def test_the_same_payload_inline_as_content_also_registers(review_env):
    """`content` is the other half of the pair; a worker that already has the JSON in hand
    should not have to write a file just to use the new door."""
    response = post_inbox(
        _body(content=json.dumps(VERDICT_PAYLOAD, ensure_ascii=False)),
    )

    assert response.status_code == 201, response.text
    assert review_env["insert"].call_args.kwargs["verdict"] == "issues"


def test_review_submission_snapshots_requested_and_actual_provider_mismatch(review_env, monkeypatch):
    """The inbox binds server-owned run evidence; the model cannot claim its provider."""
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_opus",
        "provider": {"id": "aip_opus", "name": "Opus at review time"},
        "selected_provider_source": "reviewer_override",
        "attempt_no": 2,
    })

    response = post_inbox(_body(verdict="pass", findings=[], comment="ok"))

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs == {
        "doc_id": DOC_ID,
        "revision_no": 2,
        "reviewer_id": USER,
        "verdict": "pass",
        "findings_json": "[]",
        "comment": "ok",
        "reviewed_at": kwargs["reviewed_at"],
        "review_run_id": "air_review_0393",
        "requested_provider_id": "aip_sonnet",
        "actual_provider_id": "aip_opus",
        "actual_provider_name": "Opus at review time",
        "provider_source": "fallback",
        "attempt_no": 2,
        "fallback_used": True,
    }


def test_review_submission_keeps_project_default_source_when_provider_matches(review_env, monkeypatch):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_sonnet",
        "provider": {"id": "aip_sonnet", "name": "Sonnet snapshot"},
        "selected_provider_source": "project_default",
        "attempt_no": 1,
    })

    response = post_inbox(_body(verdict="pass", findings=[], comment="ok"))

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs["review_run_id"] == "air_review_0393"
    assert kwargs["requested_provider_id"] == kwargs["actual_provider_id"] == "aip_sonnet"
    assert kwargs["actual_provider_name"] == "Sonnet snapshot"
    assert kwargs["provider_source"] == "project_default"
    assert kwargs["attempt_no"] == 1
    assert kwargs["fallback_used"] is False


# ── criterion 5-2: outside the scratch directory ──────────────────────────────────────

def test_a_path_outside_the_scratch_directory_is_refused(review_env):
    doc_path = _write_payload(review_env["outside"])

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 422
    assert "doc_path is not accessible" in response.json()["error_message"]
    review_env["insert"].assert_not_called()


def test_a_path_that_does_not_exist_is_refused(review_env):
    missing = str(review_env["scratch"] / "never-written.json")

    response = post_inbox(
        _body(doc_path=missing),
    )

    assert response.status_code == 422
    assert "doc_path file does not exist" in response.json()["error_message"]
    review_env["insert"].assert_not_called()


# ── criterion 5-3: both sources at once ───────────────────────────────────────────────

def test_sending_both_a_file_and_inline_content_is_refused(review_env):
    doc_path = _write_payload(review_env["scratch"])

    response = post_inbox(
        _body(doc_path=doc_path, content=json.dumps(VERDICT_PAYLOAD, ensure_ascii=False)),
    )

    assert response.status_code == 400
    assert response.json()["error_message"] == (
        "Exactly one of doc_path or content must be provided"
    )
    review_env["insert"].assert_not_called()


def test_the_error_wording_matches_the_document_paths(review_env):
    """T0005 §2-7: "new/edit 경로와 같은 문구를 쓴다". Two implementations of the same
    contract drift apart the moment their strings differ, so the strings are pinned here."""
    source = Path(inbox_routes.__file__).read_text(encoding="utf-8")
    assert source.count('return _fail(400, "Exactly one of doc_path or content must be provided")') == 3
    assert source.count('doc_path is not accessible: ') == 3
    assert source.count('doc_path file does not exist: ') == 3


# ── the shape that must NOT change (T0005 §1: the copy-mention review path) ───────────

def test_an_inline_verdict_with_no_source_field_still_registers(review_env):
    response = post_inbox(
        _body(verdict="pass", findings=[], comment="ok"),
    )

    assert response.status_code == 201, response.text
    assert review_env["insert"].call_args.kwargs["verdict"] == "pass"


def test_a_file_holding_a_bad_verdict_is_still_refused(review_env):
    doc_path = _write_payload(
        review_env["scratch"], payload={"verdict": "looks-fine", "findings": []},
    )

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 400
    assert "verdict must be one of" in response.json()["error_message"]


def test_a_file_that_is_not_json_is_refused(review_env):
    target = review_env["scratch"] / "prose.md"
    target.write_text("# 검토 결과\n\nverdict: issues\n", encoding="utf-8")

    response = post_inbox(
        _body(doc_path=str(target)),
    )

    assert response.status_code == 400
    assert "not valid JSON" in response.json()["error_message"]


def test_the_file_cannot_repoint_the_review_at_another_document(review_env):
    """The token was already checked against the request's project/doc_id, so a doc_id in
    the file must not be able to move the verdict onto a different document."""
    doc_path = _write_payload(
        review_env["scratch"],
        payload={**VERDICT_PAYLOAD, "doc_id": "flowgate.v02.0003.0001-R", "project": "other"},
    )

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 201, response.text
    assert review_env["insert"].call_args.kwargs["doc_id"] == DOC_ID


# ── §2-7: the server accepting it is only half — the worker has to be told ────────────

def test_the_help_submit_item_advertises_the_file_form_for_review_tokens():
    content = help_catalog._content_submit({
        "base_url": "http://127.0.0.1:8089/flowgate/api/v1",
        "action_scope": "review",
        "doc_id": DOC_ID,
        "project": PROJECT,
        "group_id": GROUP_ID,
        "locale": "ko",
        "scratch_dir": "C:/scratch/tok-review-0393",
    })
    assert "doc_path" in content["source_choice"]
    # T0004 (0506): source_choice keeps the generic scratch-directory guidance but no
    # longer interpolates the actual server-local absolute path.
    assert "C:/scratch/tok-review-0393" not in content["source_choice"]
    assert "scratch directory" in content["source_choice"]


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_the_review_mention_tells_the_worker_about_the_file_form(locale):
    mention = mention_service.build_review_mention(
        token_rec={"project": PROJECT, "group_id": GROUP_ID,
                   "scratch_dir": "C:/scratch/tok-review-0393"},
        target_doc={"doc_id": DOC_ID, "type_code": "NR", "seq": 5,
                    "title": "GUI 모드 전환 대상 화면 목록", "module": "v02",
                    "project_id": PROJECT},
        api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        raw_token="RAW",
        locale=locale,
    )
    assert "doc_path" in mention
    # The submission section stays "address + pointer" (0372 set 3) — one added sentence,
    # not a JSON body creeping back in.
    assert '"action": "review"' not in mention


def test_the_review_mention_does_not_leak_korean_into_en_or_ja():
    """0372's locale hygiene rule, re-asserted for the sentence this group adds."""
    import re

    hangul = re.compile(r"[가-힣]")
    for locale in ("en", "ja"):
        line = mention_service._REVIEW_FILE_SUBMIT_TEXT[locale]
        assert not hangul.search(line), f"{locale}: {line!r}"
    assert hangul.search(mention_service._REVIEW_FILE_SUBMIT_TEXT["ko"])
