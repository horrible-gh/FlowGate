"""Independently selectable scenarios; shared fixtures remain in test_review_doc_path_0393.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_review_doc_path_0393 import (
    DOC_ID,
    GROUP_ID,
    MagicMock,
    PROJECT,
    Path,
    USER,
    VERDICT_PAYLOAD,
    _SERVER_DIR,
    _body,
    _raw_post_inbox,
    _write_payload,
    ai_runtime,
    build_live_sqlite_db,
    datetime,
    db_reviews,
    help_catalog,
    inbox_routes,
    install_live_sqlite_store,
    json,
    mention_service,
    os,
    post_inbox,
    pytest,
    review_env,
    sys,
    timezone,
)

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
    # : source_choice keeps the generic scratch-directory guidance but no
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
