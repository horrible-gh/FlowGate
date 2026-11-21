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

def test_a_path_that_does_not_exist_is_refused(review_env):
    missing = str(review_env["scratch"] / "never-written.json")

    response = post_inbox(
        _body(doc_path=missing),
    )

    assert response.status_code == 422
    assert "doc_path file does not exist" in response.json()["error_message"]
    review_env["insert"].assert_not_called()
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
    """"use the same wording as the new/edit paths". Two implementations of the same
    contract drift apart the moment their strings differ, so the strings are pinned here."""
    from flowgate.card_document_edit import legacy_edit
    source = Path(inbox_routes.__file__).read_text(encoding="utf-8")
    legacy = Path(legacy_edit.__file__).read_text(encoding="utf-8")
    # L#2 §2.2/2.6: edit moved to its service; new and review remain in inbox.
    assert source.count('return _fail(400, "Exactly one of doc_path or content must be provided")') == 2
    assert 'inbox._fail(400, "Exactly one of doc_path or content must be provided")' in legacy
    for wording in ('doc_path is not accessible: ', 'doc_path file does not exist: '):
        assert source.count(wording) == 2
        assert legacy.count(wording) == 1
