"""0391 T0005 -- corrupted-body guard (proposal 3) + body-fingerprint match (proposal 4).

B0001: chat/document submissions land with Hangul mangled to '?' (ascii-replace
mojibake) because nothing rejects it at the real registration path -- the existing
detector only ran on a workflow-step-name swap and on chat's dry-run, and the chat
mention never even mentioned dry-run existed (NR0004). This suite covers the three
inbox_routes.py real-registration sites (new/edit/review) added in T0005 Step 5.9,
plus the new line-based detector in workflow_decision_service.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox
from store_transaction_support import install_null_transaction_store
from modules.flow_gate.services import workflow_decision_service as wf_decision

CORRUPT = "??? ?? ? 0082(??3) ?? ? ?? ??"
CLEAN_KO = "정상적인 한글 본문입니다"
CLEAN_EN = "a perfectly normal ASCII message"


# ── workflow_decision_service._text_is_corrupted (new line-based detector) ──────────

def test_text_is_corrupted_flags_a_single_bad_line():
    assert wf_decision._text_is_corrupted(CORRUPT)


def test_text_is_corrupted_passes_clean_multiline_body():
    body = "project: flowgate\nmodule: default\n---\n\n# 제목\n\n" + CLEAN_KO
    assert not wf_decision._text_is_corrupted(body)


def test_text_is_corrupted_none_and_empty():
    assert not wf_decision._text_is_corrupted(None)
    assert not wf_decision._text_is_corrupted("")


def test_text_is_corrupted_catches_what_the_whole_body_ratio_misses():
    """NR0004 §4: B0001's own body measured 0.404 on the whole-body ratio (frontmatter
    + code blocks + English identifiers dilute it) -- below the 0.5 threshold, so the
    OLD whole-body check (_label_is_corrupted) would have let it through. One corrupted
    line among many clean ones must still trip the NEW line-based check."""
    clean_filler = "\n".join([f"line {i}: some ordinary english filler text here" for i in range(20)])
    body = clean_filler + "\n" + CORRUPT + "\n" + clean_filler
    assert not wf_decision._label_is_corrupted(body)  # whole-body ratio: diluted, misses it
    assert wf_decision._text_is_corrupted(body)        # line-based: still catches it


# ── inbox_routes._handle_new (Step 5.9) ──────────────────────────────────────────

def _new_body(*, title=None, content="clean body", questions=None, dry_run=False, **extra):
    body = {
        "action": "new",
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0391",
        "prev_doc_id": "flowgate.default.0391.0001-B",
        "doc_type": "NR",
        "content": content,
        "dry_run": dry_run,
    }
    if title is not None:
        body["title"] = title
    if questions is not None:
        body["questions"] = questions
    body.update(extra)
    return body


def _patch_new_validation(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    token = {
        "token_id": "tok-0391-new",
        "project": "flowgate",
        "issued_to": "worker-0391",
        # 0492 T0018: `group_id` is a real tokens column (migration 075a) and inbox
        # Step 3 now compares it as the `group` axis.
        "group_id": "flowgate.default.0391",
        "action_scope": "new",
        "doc_ref": "flowgate.default.0391.0001-B",
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda _p, _m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda _g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(
        inbox_routes, "_resolve_group", lambda *_a, **_k: {"group_id": "flowgate.default.0391"}
    )
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "doc_review_status": "pending_review"},
    )
    monkeypatch.setattr(inbox_routes, "_find_body_twin", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_wfseq, "get_pending_head_by_group", lambda *_a, **_k: None)
    reserve = MagicMock(return_value="NR0099")
    create = MagicMock()
    consume = MagicMock()
    increment = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    monkeypatch.setattr(inbox_routes.db_docs, "create", create)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", increment)
    return {"reserve": reserve, "create": create, "consume": consume, "increment": increment}


def test_new_corrupted_content_rejected_before_numbering(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_new_validation(monkeypatch)
    response = post_inbox(
        _new_body(content=CORRUPT)
    )
    payload = response.json()

    assert response.status_code == 422
    assert payload["ok"] is False
    mocks["reserve"].assert_not_called()  # no doc number burned
    mocks["create"].assert_not_called()   # nothing stored
    mocks["consume"].assert_not_called()  # token not consumed


def test_new_corrupted_title_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_new_validation(monkeypatch)
    response = post_inbox(
        _new_body(title=CORRUPT, content=CLEAN_KO)
    )

    assert response.status_code == 422
    mocks["create"].assert_not_called()


def test_new_corrupted_question_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_new_validation(monkeypatch)
    response = post_inbox(

        _new_body(content=CLEAN_KO, questions=[{"title": CORRUPT, "body": "x"}]),
    )

    assert response.status_code == 422
    mocks["create"].assert_not_called()


def test_new_clean_body_passes(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_new_validation(monkeypatch)
    response = post_inbox(
        _new_body(content=CLEAN_KO, dry_run=True)
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    mocks["increment"].assert_called_once()


def test_new_force_encoding_reason_bypasses_corruption_reject(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    response = post_inbox(

        _new_body(content=CORRUPT, dry_run=True, force_encoding_reason="worker confirms intentional"),
    )
    assert response.status_code == 200


def test_new_force_encoding_reason_too_short_still_rejects(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    response = post_inbox(

        _new_body(content=CORRUPT, dry_run=True, force_encoding_reason="short"),
    )
    assert response.status_code == 422


def test_new_fingerprint_mismatch_rejects_even_clean_looking_body(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    # T0004 작업 7: 한국어 문구를 직접 단언하는 테스트라 ko 로케일을 명시한다.
    response = post_inbox(
        _new_body(content=CLEAN_KO, dry_run=True, body_sha256="0" * 64),
        headers={"x-locale": "ko"},
    )
    payload = response.json()
    assert response.status_code == 422
    assert "지문" in payload["error_message"]
    assert "force_encoding_reason" in payload["error_message"]


def test_new_fingerprint_match_passes_even_when_body_looks_corrupted(monkeypatch):
    """T0005 §6-4's documented limit: a fingerprint proves transit integrity, not that
    the original text was clean -- if the sender hashed the already-corrupted text,
    the guard can never know. A matching fingerprint is trusted over the heuristic."""
    import hashlib

    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    digest = hashlib.sha256(CORRUPT.encode("utf-8")).hexdigest()
    response = post_inbox(

        _new_body(content=CORRUPT, dry_run=True, body_sha256=digest, body_chars=len(CORRUPT)),
    )
    assert response.status_code == 200


def test_new_char_count_mismatch_rejects(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    response = post_inbox(

        _new_body(content=CLEAN_KO, dry_run=True, body_chars=999999),
    )
    assert response.status_code == 422


# ── inbox_routes._handle_edit (Step 5.9) ─────────────────────────────────────────

def _edit_body(*, content="clean revised body", dry_run=False, **extra):
    body = {
        "action": "edit",
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0391",
        "doc_id": "flowgate.default.0391.0002-NR",
        "edit_reason": "user_comment",
        "content": content,
        "dry_run": dry_run,
    }
    body.update(extra)
    return body


def _patch_edit_validation(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.documents import document_service

    token = {
        "token_id": "tok-0391-edit",
        "project": "flowgate",
        "issued_to": "worker-0391",
        # 0492 T0018: `group_id` is a real tokens column (migration 075a) and inbox
        # Step 3 now compares it as the `group` axis.
        "group_id": "flowgate.default.0391",
        "action_scope": "edit",
        "doc_ref": "flowgate.default.0391.0002-NR",
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda _p, _m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda _g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda doc_id: {
            "doc_id": doc_id, "type_code": "NR", "status": "open",
            "doc_review_status": "pending_review",
        },
    )
    monkeypatch.setattr(document_service, "is_final_approved", lambda _doc: False)
    monkeypatch.setattr(document_service, "is_document_editable", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
    monkeypatch.setattr(
        inbox_routes, "_resolve_group", lambda *_a, **_k: {"group_id": "flowgate.default.0391"}
    )
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes, "_find_body_twin", lambda *_a, **_k: None)
    increment = MagicMock()
    update = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", increment)
    monkeypatch.setattr(inbox_routes.db_docs, "update", update)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    return {"increment": increment, "update": update, "consume": consume}


def test_edit_corrupted_content_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_edit_validation(monkeypatch)
    response = post_inbox(
        _edit_body(content=CORRUPT)
    )
    payload = response.json()

    assert response.status_code == 422
    mocks["update"].assert_not_called()
    mocks["consume"].assert_not_called()


def test_edit_corrupted_rejection_response_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_edit_validation(monkeypatch)
    response = post_inbox(

        _edit_body(content=CLEAN_KO, rejection_response=CORRUPT),
    )
    assert response.status_code == 422


def test_edit_clean_body_dry_run_passes(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_edit_validation(monkeypatch)
    response = post_inbox(
        _edit_body(content=CLEAN_KO, dry_run=True)
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    mocks["increment"].assert_called_once()


def test_edit_force_encoding_reason_bypasses(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_edit_validation(monkeypatch)
    response = post_inbox(

        _edit_body(content=CORRUPT, dry_run=True, force_encoding_reason="worker confirms intentional"),
    )
    assert response.status_code == 200


# ── inbox_routes._handle_review (Step 5.9) ───────────────────────────────────────

def _review_body(*, comment=None, findings=None, dry_run=False, **extra):
    body = {
        "action": "review",
        "project": "flowgate",
        "doc_id": "flowgate.default.0391.0002-NR",
        "verdict": "issues",
        "findings": findings or [],
        "dry_run": dry_run,
    }
    if comment is not None:
        body["comment"] = comment
    body.update(extra)
    return body


def _patch_review_validation(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import document_reviews as db_reviews

    token = {
        "token_id": "tok-0391-review",
        "project": "flowgate",
        "issued_to": "worker-0391",
        "action_scope": "review",
        "doc_ref": None,
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": "flowgate.default.0391", "revision_no": 0},
    )
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    insert_review = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(db_reviews, "insert_review", insert_review)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    # 0535 T0007 §3: review registration now runs inside one store.transaction().
    install_null_transaction_store(monkeypatch)
    return {"insert_review": insert_review, "consume": consume}


def test_review_corrupted_comment_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_review_validation(monkeypatch)
    response = post_inbox(
        _review_body(comment=CORRUPT)
    )

    assert response.status_code == 422
    mocks["insert_review"].assert_not_called()
    mocks["consume"].assert_not_called()


def test_review_corrupted_finding_note_rejected(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_review_validation(monkeypatch)
    response = post_inbox(

        _review_body(findings=[{"locus": "x", "note": CORRUPT}]),
    )

    assert response.status_code == 422
    mocks["insert_review"].assert_not_called()


def test_review_clean_comment_passes(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    mocks = _patch_review_validation(monkeypatch)
    response = post_inbox(
        _review_body(comment=CLEAN_EN)
    )

    assert response.status_code == 201
    mocks["insert_review"].assert_called_once()
    mocks["consume"].assert_called_once()


# ── workflow step labels: reject instead of silently swapping (T0005 §5-5/§5-6) ────
# NR0004 §2: decide/edit used to run the label through _safe_label(), replacing a
# corrupted label with the document type name. The sender was never told and the text
# they meant to write was gone. These pin the new contract: refuse, explain, and offer
# the same force_encoding_reason door the other four paths use.

CORRUPT_LABEL = "???? ?? ? ?? ???? ?? locus? ?? ??"
GOOD_REASON = "이 단계 이름은 원문에 실제로 물음표가 들어갑니다"


@contextmanager
def _noop_txn():
    yield


def _fake_store():
    store = MagicMock()
    store.transaction.side_effect = _noop_txn
    return store


def _patch_decide(monkeypatch, inserted):
    """Same shape as test_corrupted_label_guard_0114's harness (insert_sequence_item,
    a get_sequence_by_doc_id that returns None first and the row afterwards)."""
    calls = {"n": 0}

    def _by_doc(_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else {"id": 7}

    monkeypatch.setattr(wf_decision.db_documents, "get_by_id",
                        lambda d: {"doc_id": d, "project_id": "flowgate",
                                   "group_id": "flowgate.default.0391"})
    monkeypatch.setattr(wf_decision.db_wfseq, "get_sequence_by_doc_id", _by_doc)
    monkeypatch.setattr(wf_decision.db_wfseq, "insert_sequence", lambda _d: None)
    monkeypatch.setattr(wf_decision.db_wfseq, "insert_sequence_item",
                        lambda **kw: inserted.setdefault("rows", []).append(kw))
    monkeypatch.setattr(wf_decision.db_wfseq, "get_effective_head", lambda _sid: None)
    monkeypatch.setattr(wf_decision.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(wf_decision, "get_store", _fake_store)
    monkeypatch.setattr(wf_decision, "get_type_name",
                        lambda t, locale="ko": {"N": "조사지시", "NR": "조사레포트"}.get(t, t))


def test_decide_workflow_rejects_corrupted_label_with_an_actionable_message(monkeypatch):
    inserted: dict = {}
    _patch_decide(monkeypatch, inserted)

    with pytest.raises(ValueError) as excinfo:
        wf_decision.decide_workflow(
            "flowgate.default.0391.0001-B", "R",
            [{"id": 1, "type": "N", "label": CORRUPT_LABEL}],
        )

    message = str(excinfo.value)
    assert message.startswith("corrupted_label:")
    # §5-6: (가) what is wrong, (다) how to get through anyway.
    assert "깨진 글자" in message
    assert "force_encoding_reason" in message
    assert inserted.get("rows") is None  # nothing inserted


def test_decide_workflow_force_encoding_reason_lets_the_label_through(monkeypatch):
    inserted: dict = {}
    _patch_decide(monkeypatch, inserted)
    logged: list = []
    monkeypatch.setattr(wf_decision, "_log_force_encoding_reason",
                        lambda *a: logged.append(a))

    wf_decision.decide_workflow(
        "flowgate.default.0391.0001-B", "R",
        [{"id": 1, "type": "N", "label": CORRUPT_LABEL}],
        force_encoding_reason=GOOD_REASON,
    )

    labels = [row["label"] for row in inserted["rows"]]
    assert CORRUPT_LABEL in labels          # stored VERBATIM, not swapped for "조사지시"
    assert len(logged) == 1                 # bypass recorded (db_events, §5-6)


def test_decide_workflow_short_force_encoding_reason_is_not_a_bypass(monkeypatch):
    inserted: dict = {}
    _patch_decide(monkeypatch, inserted)

    with pytest.raises(ValueError, match="corrupted_label"):
        wf_decision.decide_workflow(
            "flowgate.default.0391.0001-B", "R",
            [{"id": 1, "type": "N", "label": CORRUPT_LABEL}],
            force_encoding_reason="  ok  ",
        )
    assert inserted.get("rows") is None


def test_edit_workflow_pending_rejects_corrupted_label(monkeypatch):
    monkeypatch.setattr(wf_decision.db_wfseq, "get_sequence_by_doc_id", lambda _d: {"id": 7})
    monkeypatch.setattr(wf_decision.db_wfseq, "get_sequence_items",
                        lambda _s: [{"item_seq": 1, "result_doc_id": "x", "type_": "N", "label": "ok"}])
    inserted: dict = {}
    monkeypatch.setattr(wf_decision.db_wfseq, "insert_sequence_item",
                        lambda **kw: inserted.setdefault("rows", []).append(kw))
    monkeypatch.setattr(wf_decision, "get_store", _fake_store)
    monkeypatch.setattr(wf_decision, "get_type_name",
                        lambda t, locale="ko": {"N": "조사지시", "NR": "조사레포트"}.get(t, t))

    with pytest.raises(ValueError, match="corrupted_label"):
        wf_decision.edit_workflow_pending(
            "flowgate.default.0391.0001-B",
            [{"type": "N", "label": CORRUPT_LABEL}],
        )
    assert inserted.get("rows") is None


def test_reject_helper_is_not_used_on_the_read_path(monkeypatch):
    """§5-5: 810/1018 (the healing read path) must stay a swap, not a rejection —
    otherwise the rows already corrupted in the DB become unreadable."""
    monkeypatch.setattr(wf_decision, "get_type_name",
                        lambda t, locale="ko": {"N": "조사지시"}.get(t, t))
    assert wf_decision._safe_label(CORRUPT_LABEL, "N") == "조사지시"


# ── T0004: submission validation errors follow the requested locale ─────────────
# NR0003 발견 2-5: _encoding_guard(), the Step 5.8 workflow-head 409, and the
# new/edit TR-scope empty-notice fallback used to ignore locale entirely and always
# answer in Korean. These pin the en/ja no-leak contract plus the continuation_locale
# > x-locale priority and the ko default's preserved meaning.

_INBOX_HANGUL_RE = re.compile(r"[가-힣]")


def _walk_strings_for_korean(payload):
    if isinstance(payload, str):
        if _INBOX_HANGUL_RE.search(payload):
            yield payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and _INBOX_HANGUL_RE.search(key):
                yield key
            yield from _walk_strings_for_korean(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            yield from _walk_strings_for_korean(value)


def _assert_no_korean(payload):
    leaks = list(_walk_strings_for_korean(payload))
    assert not leaks, f"Korean syllable leak(s): {leaks}"


# 선택된 로케일의 문구가 실제로 돌아왔는지까지 확인한다 — "한글만 없으면 통과"는
# 로케일 맵을 잘못 골라도 초록이라 T0004 완료 기준을 증명하지 못한다.
_LOCALE_SNIPPETS = {
    "corrupted": {"en": "looks like corrupted characters", "ja": "文字化け"},
    "fingerprint": {
        "en": "body fingerprint does not match",
        "ja": "本文の指紋が一致しません",
    },
    "workflow_head": {"en": "cannot be accepted", "ja": "受け付けられません"},
    "tr_scope_fallback": {
        "en": "TR scope validation rejected",
        "ja": "TR作業範囲検証却下",
    },
}


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_new_corrupted_content_locale_has_no_korean(monkeypatch, locale):
    _patch_new_validation(monkeypatch)
    response = post_inbox(_new_body(content=CORRUPT), headers={"x-locale": locale})
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["corrupted"][locale] in payload["error_message"]
    assert "force_encoding_reason" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_new_fingerprint_mismatch_locale_has_no_korean(monkeypatch, locale):
    _patch_new_validation(monkeypatch)
    response = post_inbox(
        _new_body(content=CLEAN_KO, dry_run=True, body_sha256="0" * 64),
        headers={"x-locale": locale},
    )
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["fingerprint"][locale] in payload["error_message"]
    assert "0" * 64 in payload["error_message"]  # 지문 세부가 로케일과 무관하게 남는다


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_edit_corrupted_content_locale_has_no_korean(monkeypatch, locale):
    _patch_edit_validation(monkeypatch)
    response = post_inbox(_edit_body(content=CORRUPT), headers={"x-locale": locale})
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["corrupted"][locale] in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_edit_fingerprint_mismatch_locale_has_no_korean(monkeypatch, locale):
    _patch_edit_validation(monkeypatch)
    response = post_inbox(
        _edit_body(content=CLEAN_KO, dry_run=True, body_chars=999999),
        headers={"x-locale": locale},
    )
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["fingerprint"][locale] in payload["error_message"]
    assert "999999" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_review_corrupted_comment_locale_has_no_korean(monkeypatch, locale):
    _patch_review_validation(monkeypatch)
    response = post_inbox(_review_body(comment=CORRUPT), headers={"x-locale": locale})
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["corrupted"][locale] in payload["error_message"]
    # 필드 식별자도 로케일 중립 내부 키라 en/ja 응답에 한글 라벨이 섞이지 않는다.
    assert "comment" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_review_fingerprint_mismatch_locale_has_no_korean(monkeypatch, locale):
    _patch_review_validation(monkeypatch)
    response = post_inbox(
        _review_body(comment=CLEAN_EN, body_sha256="0" * 64),
        headers={"x-locale": locale},
    )
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["fingerprint"][locale] in payload["error_message"]


def test_new_continuation_locale_takes_priority_over_x_locale_header(monkeypatch):
    """T0004 작업 2 / NR0003 발견 2: the token's continuation_locale (unmanned worker)
    must win over the request's x-locale header, matching every other locale
    resolution in this file (0355 L0007 §2-1)."""
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    token = {
        "token_id": "tok-0391-new-locale",
        "project": "flowgate",
        "issued_to": "worker-0391",
        # 0492 T0018: `group_id` is a real tokens column (migration 075a) and inbox
        # Step 3 now compares it as the `group` axis.
        "group_id": "flowgate.default.0391",
        "action_scope": "new",
        "doc_ref": "flowgate.default.0391.0001-B",
        "dry_run_count": 0,
        "continuation_locale": "ja",
    }
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    response = post_inbox(_new_body(content=CORRUPT), headers={"x-locale": "en"})
    payload = response.json()
    assert response.status_code == 422
    assert "文字化け" in payload["error_message"]  # ja wins over the en header
    _assert_no_korean(payload)


def test_new_ko_response_preserves_existing_meaning(monkeypatch):
    """No x-locale / continuation_locale falls back to ko (unchanged default), and
    the 422 keeps the fingerprint word the pre-T0004 behavior asserted on."""
    _patch_new_validation(monkeypatch)
    response = post_inbox(_new_body(content=CLEAN_KO, dry_run=True, body_sha256="0" * 64))
    payload = response.json()
    assert response.status_code == 422
    assert "지문" in payload["error_message"]
    # 지문 세부(기대/실제 sha256)와 강제 우회 안내가 ko 에서도 그대로 남는다.
    assert "sha256" in payload["error_message"]
    assert "0" * 64 in payload["error_message"]
    assert "force_encoding_reason" in payload["error_message"]
    assert "UTF-8" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_new_workflow_head_mismatch_locale_has_no_korean(monkeypatch, locale):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.db_wfseq, "get_pending_head_by_group",
        lambda *_a, **_k: {"type": "T"},
    )
    response = post_inbox(_new_body(content=CLEAN_KO), headers={"x-locale": locale})
    payload = response.json()
    assert response.status_code == 409
    _assert_no_korean(payload)
    assert _LOCALE_SNIPPETS["workflow_head"][locale] in payload["error_message"]
    assert "T" in payload["error_message"]  # expected_head_type
    assert "NR" in payload["error_message"]  # submitted_type


def test_new_workflow_head_mismatch_ko_default_preserves_meaning(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_new_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.db_wfseq, "get_pending_head_by_group",
        lambda *_a, **_k: {"type": "T"},
    )
    response = post_inbox(_new_body(content=CLEAN_KO))
    payload = response.json()
    assert response.status_code == 409
    assert "T" in payload["error_message"]
    assert "NR" in payload["error_message"]
    assert "등록되지" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_new_tr_scope_empty_notice_fallback_locale_has_no_korean(monkeypatch, locale):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import tr_scope_service

    _patch_new_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.tr_scope_service, "evaluate",
        lambda **_k: {"verdict": tr_scope_service.VERDICT_REJECT, "notice": ""},
    )
    monkeypatch.setattr(inbox_routes, "_prior_tr_declared", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda *_a, **_k: None)
    response = post_inbox(
        _new_body(content=CLEAN_KO, doc_type="TR"),
        headers={"x-locale": locale},
    )
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert payload["error_message"] == _LOCALE_SNIPPETS["tr_scope_fallback"][locale]


def test_new_tr_scope_empty_notice_fallback_ko_default_preserves_meaning(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import tr_scope_service

    _patch_new_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.tr_scope_service, "evaluate",
        lambda **_k: {"verdict": tr_scope_service.VERDICT_REJECT, "notice": ""},
    )
    monkeypatch.setattr(inbox_routes, "_prior_tr_declared", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda *_a, **_k: None)
    response = post_inbox(_new_body(content=CLEAN_KO, doc_type="TR"))
    payload = response.json()
    assert response.status_code == 422
    assert "TR" in payload["error_message"]
    assert "작업범위" in payload["error_message"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_edit_tr_scope_empty_notice_fallback_locale_has_no_korean(monkeypatch, locale):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import tr_scope_service

    _patch_edit_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.db_docs, "get_by_id",
        lambda doc_id: {
            "doc_id": doc_id, "type_code": "TR", "status": "open",
            "doc_review_status": "pending_review",
        },
    )
    monkeypatch.setattr(
        inbox_routes.tr_scope_service, "evaluate",
        lambda **_k: {"verdict": tr_scope_service.VERDICT_REJECT, "notice": ""},
    )
    monkeypatch.setattr(inbox_routes, "_prior_tr_declared", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda *_a, **_k: None)
    response = post_inbox(_edit_body(content=CLEAN_KO), headers={"x-locale": locale})
    payload = response.json()
    assert response.status_code == 422
    _assert_no_korean(payload)
    assert payload["error_message"] == _LOCALE_SNIPPETS["tr_scope_fallback"][locale]


def test_edit_tr_scope_notice_from_the_service_still_wins(monkeypatch):
    """T0004 작업 5: 폴백은 서비스가 빈 notice 를 줬을 때만 쓰인다 — 비어 있지 않은
    notice 는 로케일 폴백보다 먼저 이긴다(`or` 의 좌변)."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import tr_scope_service

    _patch_edit_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.db_docs, "get_by_id",
        lambda doc_id: {
            "doc_id": doc_id, "type_code": "TR", "status": "open",
            "doc_review_status": "pending_review",
        },
    )
    monkeypatch.setattr(
        inbox_routes.tr_scope_service, "evaluate",
        lambda **_k: {
            "verdict": tr_scope_service.VERDICT_REJECT,
            "notice": "service-provided notice",
        },
    )
    monkeypatch.setattr(inbox_routes, "_prior_tr_declared", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda *_a, **_k: None)
    response = post_inbox(_edit_body(content=CLEAN_KO), headers={"x-locale": "en"})
    payload = response.json()
    assert response.status_code == 422
    assert payload["error_message"] == "service-provided notice"


def test_edit_tr_scope_empty_notice_fallback_ko_default_preserves_meaning(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import tr_scope_service

    _patch_edit_validation(monkeypatch)
    monkeypatch.setattr(
        inbox_routes.db_docs, "get_by_id",
        lambda doc_id: {
            "doc_id": doc_id, "type_code": "TR", "status": "open",
            "doc_review_status": "pending_review",
        },
    )
    monkeypatch.setattr(
        inbox_routes.tr_scope_service, "evaluate",
        lambda **_k: {"verdict": tr_scope_service.VERDICT_REJECT, "notice": ""},
    )
    monkeypatch.setattr(inbox_routes, "_prior_tr_declared", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda *_a, **_k: None)
    response = post_inbox(_edit_body(content=CLEAN_KO))
    payload = response.json()
    assert response.status_code == 422
    assert payload["error_message"] == "TR 작업범위 검증 반려"
