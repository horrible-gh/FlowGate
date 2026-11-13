"""flowgate.v02gui.0009 (chat 0002-CH) -- question marks in documents, language-neutral.

Requirement A: a document whose text contains ordinary question marks ("??", "?? ??? ?")
failed to register. The question-mark heuristic (``_text_is_corrupted``) ran on every
token path *and* on the Designer's own card / document edits, although a browser posts
UTF-8 JSON whatever the language. This suite pins the replacement:

- trusted transports (Designer session, FlowGate itself) are never corruption-checked;
- a worker proves its path with the per-token encoding probe on the last body line --
  intact: no corruption check on the body, the line is removed before anything else
  sees it and the rest is stored exactly as written; damaged: refused even with a force
  reason;
- a worker without the probe keeps today's rules plus the language-neutral signals.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox
from store_transaction_support import install_null_transaction_store

from flowgate.card_document_edit import common_checks
from flowgate.card_document_edit.contracts import Origin
from flowgate.card_document_edit.errors import GraphApiError
from modules.flow_gate.api import inbox_routes
from modules.flow_gate.services import encoding_probe

TOKEN_ID = "tok-v02gui-0009-doc"
PROBE = encoding_probe.probe_for(TOKEN_ID)
QUESTION_BODY = (
    "# 질문 정리\n\n"
    "왜 안되지??\n"
    "?? ??? ?\n"
    "What now????\n"
    "日本語も同じ？？ 駅 込 𠮷 😀\n"
)
DOC = {"doc_id": "flowgate.v02gui.0009.0003-NR", "project_id": "flowgate", "type_code": "NR", "revision_no": 1}


def _guard(**kwargs):
    base = dict(fields={"body": QUESTION_BODY}, fingerprint_field="body", body_sha256=None,
                body_chars=None, force_encoding_reason=None, locale="en")
    base.update(kwargs)
    return inbox_routes._encoding_guard(**base)


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _message(response):
    return json.loads(response.body)["error_message"]


# -- encoding_probe.split_document_probe -------------------------------------------

@pytest.mark.parametrize("sent, stored", [
    (QUESTION_BODY + PROBE + "\n", QUESTION_BODY),
    (QUESTION_BODY + PROBE, QUESTION_BODY),
    ("a\r\nb\r\n" + PROBE + "\r\n\n", "a\r\nb\r\n"),
    ("no newline" + "\n" + PROBE, "no newline\n"),
    (PROBE + "\n", ""),
])
def test_document_split_keeps_everything_before_the_probe_line(sent, stored):
    text, status, _received = encoding_probe.split_document_probe(sent, PROBE)
    assert status == encoding_probe.PROBE_MATCHED
    assert text == stored


def test_document_split_leaves_absent_and_broken_text_untouched():
    broken = QUESTION_BODY + PROBE.replace(encoding_probe.PROBE_GLYPH, "??") + "\n"
    assert encoding_probe.split_document_probe(broken, PROBE)[:2] == (broken, encoding_probe.PROBE_BROKEN)
    assert encoding_probe.split_document_probe(QUESTION_BODY, PROBE)[:2] == (QUESTION_BODY, encoding_probe.PROBE_ABSENT)


# -- inbox_routes._encoding_guard --------------------------------------------------

def test_trusted_transport_checks_nothing():
    assert _guard(fields={"body": QUESTION_BODY + "�", "title": "?? ??"}, trusted_transport=True) is None


def test_matched_probe_lets_question_marks_through_without_fingerprint():
    assert _guard(probe_status=encoding_probe.PROBE_MATCHED, probe_received=PROBE) is None


def test_matched_probe_does_not_cover_separate_json_fields():
    response = _guard(fields={"body": QUESTION_BODY, "title": "?? ??? ?"},
                      probe_status=encoding_probe.PROBE_MATCHED)
    assert response is not None and response.status_code == 422


def test_broken_probe_is_refused_even_with_a_force_reason():
    received = PROBE.replace(encoding_probe.PROBE_GLYPH, "??")
    response = _guard(probe_status=encoding_probe.PROBE_BROKEN, probe_received=received,
                      force_encoding_reason="this is a long enough force reason")
    assert response.status_code == 422
    message = _message(response)
    assert "probe" in message and ascii(received) in message


def test_without_probe_the_legacy_question_mark_rule_still_applies():
    response = _guard()
    assert response is not None and response.status_code == 422


def test_fingerprint_may_be_computed_with_or_without_the_probe_line():
    sent = QUESTION_BODY + PROBE + "\n"
    for basis in (sent, QUESTION_BODY):
        assert _guard(probe_status=encoding_probe.PROBE_MATCHED, body_sha256=_sha(basis),
                      body_chars=len(basis), fingerprint_texts=[sent, QUESTION_BODY]) is None
    response = _guard(probe_status=encoding_probe.PROBE_MATCHED, body_sha256=_sha("other"),
                      fingerprint_texts=[sent, QUESTION_BODY])
    assert response.status_code == 422
    assert _sha(sent) in _message(response)  # the mismatch names the file as sent


def test_fingerprint_without_probe_still_meets_the_hard_signals():
    body = "clean line\nbroken � line\n"
    response = _guard(fields={"body": body}, body_sha256=_sha(body))
    assert response is not None and response.status_code == 422
    assert _guard(fields={"body": body}, body_sha256=_sha(body),
                  force_encoding_reason="genuinely part of the document") is None


def test_review_keeps_the_legacy_rule_only():
    fields = {"comment": "a comment quoting � from a log"}
    assert _guard(fields=fields, fingerprint_field="comment", fingerprint_bypasses_corruption=False,
                  allow_force_bypass=False, hard_signals=False) is None


# -- card document edit (Designer / chat / agent) ----------------------------------

@pytest.mark.parametrize("origin, trusted", [
    (Origin(), True),                                    # Designer session
    (Origin("wp_deploy"), True),
    (Origin("recovery"), True),
    (Origin("agent", token_id=TOKEN_ID), False),
    (Origin("chat", token_id=TOKEN_ID), False),          # CLI chat: the worker's own transport
    (Origin("chat", token_id=TOKEN_ID, server_call_id="call-1"), True),  # API run, FlowGate posts
    (None, False),
])
def test_transport_trust_by_origin(origin, trusted):
    assert common_checks.transport_trusted(origin) is trusted


def test_designer_edit_with_question_marks_is_accepted():
    common_checks.encoding_guard(QUESTION_BODY, DOC, None, "ko", origin=Origin())


def test_worker_edit_without_probe_keeps_the_legacy_rule():
    with pytest.raises(GraphApiError):
        common_checks.encoding_guard(QUESTION_BODY, DOC, None, "ko", origin=Origin("agent", token_id=TOKEN_ID))


def test_worker_edit_with_probe_is_accepted_and_stored_without_it():
    origin = Origin("agent", token_id=TOKEN_ID)
    probe = common_checks.split_probe(QUESTION_BODY + PROBE + "\n", origin)
    assert probe.text == QUESTION_BODY and probe.status == encoding_probe.PROBE_MATCHED
    common_checks.encoding_guard(probe.text, DOC, {"body_sha256": _sha(probe.raw)}, "ko",
                                 origin=origin, probe=probe)


def test_worker_edit_with_broken_probe_is_refused():
    origin = Origin("chat", token_id=TOKEN_ID)
    probe = common_checks.split_probe(QUESTION_BODY + PROBE.replace(encoding_probe.PROBE_GLYPH, "?") + "\n", origin)
    assert probe.status == encoding_probe.PROBE_BROKEN
    with pytest.raises(GraphApiError):
        common_checks.encoding_guard(probe.text, DOC, None, "ko", origin=origin, probe=probe)


def test_trusted_origin_never_strips_text():
    text = QUESTION_BODY + PROBE + "\n"
    assert common_checks.split_probe(text, Origin()).text == text


# -- POST /inbox action=new --------------------------------------------------------

def _patch_new(monkeypatch):
    install_null_transaction_store(monkeypatch)
    token = {
        "token_id": TOKEN_ID, "project": "flowgate", "issued_to": "worker-v02gui-0009",
        "group_id": "flowgate.v02gui.0009", "action_scope": "new",
        "doc_ref": "flowgate.v02gui.0009.0001-R", "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda _p, _m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda _g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes, "_resolve_group", lambda *_a, **_k: {"group_id": "flowgate.v02gui.0009"})
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id",
                        lambda doc_id: {"doc_id": doc_id, "doc_review_status": "pending_review"})
    monkeypatch.setattr(inbox_routes, "_find_body_twin", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_wfseq, "get_pending_head_by_group", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.worker_step_type_hooks, "token_step_type", lambda _t: None)
    seen = []
    original = inbox_routes._normalize_submission_body

    def normalize(text, **kwargs):
        seen.append(text)
        return original(text, **kwargs)

    monkeypatch.setattr(inbox_routes, "_normalize_submission_body", normalize)
    mocks = {"reserve": MagicMock(return_value="NR0003"), "create": MagicMock(),
             "consume": MagicMock(), "increment": MagicMock(), "seen": seen}
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", mocks["reserve"])
    monkeypatch.setattr(inbox_routes.db_docs, "create", mocks["create"])
    monkeypatch.setattr(inbox_routes.token_service, "consume", mocks["consume"])
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", mocks["increment"])
    return mocks


def _new(content, **extra):
    body = {"action": "new", "project": "flowgate", "module": "v02gui",
            "group_name": "flowgate.v02gui.0009", "prev_doc_id": "flowgate.v02gui.0009.0001-R",
            "doc_type": "NR", "title": "물음표 문서", "content": content, "dry_run": True}
    body.update(extra)
    return body


def test_inbox_new_with_probe_accepts_question_marks_and_drops_only_the_probe(monkeypatch):
    mocks = _patch_new(monkeypatch)
    sent = QUESTION_BODY + PROBE + "\n"
    response = post_inbox(_new(sent, body_sha256=_sha(sent), body_chars=len(sent)))
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert mocks["seen"] == [QUESTION_BODY]  # what every guard and the stored file see
    mocks["reserve"].assert_not_called()


def test_inbox_new_without_probe_keeps_the_legacy_rule(monkeypatch):
    mocks = _patch_new(monkeypatch)
    response = post_inbox(_new(QUESTION_BODY))
    assert response.status_code == 422
    mocks["reserve"].assert_not_called()


def test_inbox_new_with_broken_probe_is_refused_before_numbering(monkeypatch):
    mocks = _patch_new(monkeypatch)
    sent = QUESTION_BODY + PROBE.replace(encoding_probe.PROBE_GLYPH, "??") + "\n"
    response = post_inbox(_new(sent, dry_run=False, force_encoding_reason="really needs to go through"))
    assert response.status_code == 422
    assert "probe" in response.json()["error_message"]
    mocks["reserve"].assert_not_called()
    mocks["create"].assert_not_called()
    mocks["consume"].assert_not_called()


# -- worker prompts / API runs -----------------------------------------------------

def test_api_run_attaches_the_probe_and_the_server_splits_it_back():
    from modules.flow_gate.services.ai_invoke import worker
    for content in (QUESTION_BODY, "no trailing newline"):
        body = {"content": content}
        worker._attach_document_probe(body, {"token_id": TOKEN_ID})
        stored, status, _ = encoding_probe.split_document_probe(body["content"], PROBE)
        assert status == encoding_probe.PROBE_MATCHED
        assert stored == (content if content.endswith("\n") else content + "\n")
    empty = {"content": "  "}
    worker._attach_document_probe(empty, {"token_id": TOKEN_ID})
    assert empty == {"content": "  "}


def test_submit_help_names_this_tokens_probe(monkeypatch):
    from modules.flow_gate.services import help_catalog
    monkeypatch.setattr(help_catalog, "_prev_doc_id", lambda _ctx: "flowgate.v02gui.0009.0001-R")
    ctx = {"principal_kind": "worker", "base_url": "https://fg/api/v1", "locale": "ko",
           "action_scope": "new", "doc_type": "NR", "group_id": "flowgate.v02gui.0009",
           "project": "flowgate", "doc_id": "flowgate.v02gui.0009.0001-R",
           "token_rec": {"token_id": TOKEN_ID}}
    payload = help_catalog._content_submit(ctx)
    assert payload["encoding_probe"]["line"] == PROBE
    assert payload["encoding_probe"]["rule"].isascii()
    ctx_session = {**ctx, "principal_kind": "user_session"}
    assert "encoding_probe" not in help_catalog._content_submit(ctx_session)
