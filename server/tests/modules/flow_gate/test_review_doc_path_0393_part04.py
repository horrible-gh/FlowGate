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
