"""Group 0099 B0001 — auto-approved instruction docs must honor the chosen locale.

Two root causes were fixed:
  #1  _auto_approved_title / _auto_approved_body ignored their `locale` arg and hardcoded
      Korean ("{label} 승인" / "{label} 가 승인되었습니다.").
  #2  the chosen locale was not persisted on the continuation token, so the unmanned
      self-chain (whose AI worker sends no x-locale header) always folded to 'ko'.

These tests pin both: the generator is now locale-branched (ko unchanged → no regression),
the continuation token carries continuation_locale, and advance_workflow stamps it.
"""
from __future__ import annotations


# ── #1 generator localization ───────────────────────────────────────────────────

def test_auto_approved_title_localized():
    from modules.flow_gate.documents.routers.documents import _auto_approved_title
    assert _auto_approved_title("조사지시", "ko") == "조사지시 승인"          # ko unchanged
    assert _auto_approved_title("Investigation", "en") == "Investigation approved"
    assert _auto_approved_title("調査指示", "ja") == "調査指示 承認"


def test_auto_approved_body_localized():
    from modules.flow_gate.documents.routers.documents import _auto_approved_body
    assert _auto_approved_body("조사지시", "ko") == "조사지시 가 승인되었습니다."  # ko unchanged
    assert _auto_approved_body("Investigation", "en") == "Investigation has been approved."
    assert _auto_approved_body("調査指示", "ja") == "調査指示 が承認されました。"


def test_auto_approved_body_no_korean_particle_for_en():
    """The Korean subject particle 가 / verb 승인 must NOT leak into non-ko copy (the bug)."""
    from modules.flow_gate.documents.routers.documents import _auto_approved_body
    out = _auto_approved_body("Investigation", "en")
    assert "가" not in out
    assert "승인" not in out


def test_auto_approved_unsupported_locale_folds_to_ko():
    from modules.flow_gate.documents.routers.documents import (
        _auto_approved_title,
        _auto_approved_body,
    )
    assert _auto_approved_title("조사지시", "zh") == "조사지시 승인"
    assert _auto_approved_body("조사지시", "fr") == "조사지시 가 승인되었습니다."


# ── #2 continuation_locale persistence ───────────────────────────────────────────

def _stub_token_io(monkeypatch):
    """Stub the filesystem / event side effects of token_service.issue."""
    from pathlib import Path
    from modules.flow_gate.services import token_service
    monkeypatch.setattr(token_service, "_scratch_dir", lambda *a, **k: Path("."))
    monkeypatch.setattr(token_service, "to_storage_relative", lambda *a, **k: ".")
    monkeypatch.setattr(token_service, "_active_pepper", lambda: ("p1", "pepper"))
    monkeypatch.setattr(token_service, "_next_token_id", lambda: "tok_test")
    monkeypatch.setattr(token_service.db_events, "create", lambda *a, **k: None)


def test_issue_passes_continuation_locale_to_create(monkeypatch):
    """token_service.issue(continuation_locale=...) reaches db_tokens.create + the return."""
    from modules.flow_gate.services import token_service
    _stub_token_io(monkeypatch)
    captured = {}
    monkeypatch.setattr(token_service.db_tokens, "create",
                        lambda data: captured.update(data) or {})

    res = token_service.issue(
        project="flowgate", group_id="g", action_scope="new", doc_ref="d",
        issued_to="u1", continuation_target_seq=4, continuation_locale="en",
    )
    assert captured["continuation_locale"] == "en"
    assert res["continuation_locale"] == "en"


def test_issue_ordinary_token_locale_none(monkeypatch):
    from modules.flow_gate.services import token_service
    _stub_token_io(monkeypatch)
    captured = {}
    monkeypatch.setattr(token_service.db_tokens, "create",
                        lambda data: captured.update(data) or {})

    token_service.issue(project="flowgate", group_id="g", action_scope="new",
                        doc_ref="d", issued_to="u1")
    assert captured["continuation_locale"] is None


def test_migration_051_adds_column_and_round_trips(test_db):
    """Migration 051 adds tokens.continuation_locale and it round-trips."""
    cols = {r["name"] for r in test_db.execute("PRAGMA table_info(tokens)").fetchall()}
    assert "continuation_locale" in cols
    # FK-free round-trip (foreign_keys off so we don't need project/user seed rows).
    test_db.execute("PRAGMA foreign_keys = OFF")
    test_db.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, action_scope, "
        "issued_to, created_at, expires_at, continuation_target_seq, "
        "continuation_review_mode, continuation_locale) "
        "VALUES ('t_loc','h','p','flowgate','new','u1','c','e',4,0,'ja')"
    )
    row = test_db.execute(
        "SELECT continuation_locale FROM tokens WHERE token_id='t_loc'"
    ).fetchone()
    assert row["continuation_locale"] == "ja"
    test_db.execute("DELETE FROM tokens WHERE token_id='t_loc'")
    test_db.execute("PRAGMA foreign_keys = ON")


# ── #2 advance_workflow stamps the locale onto the next continuation token ────────

def test_advance_continuous_stamps_continuation_locale(monkeypatch):
    from modules.flow_gate.services import workflow_decision_service as svc
    doc = {"doc_id": "flowgate.default.0099.0001-B", "group_id": "flowgate.default.0099",
           "project_id": "flowgate", "type_code": "B", "seq": 1}
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                        lambda _sid: {"type": "NR", "item_seq": 2, "label": "조사레포트",
                                      "result_doc_id": None, "result_doc_review_status": None,
                                      "id": 1})
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h=None: None)
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_ids",
                        lambda _s, _h=None, limit=2: [])
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    issue_kw = {}
    monkeypatch.setattr(svc.token_service, "issue",
                        lambda **k: issue_kw.update(k) or
                        {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                         "token_id": "tok", "expires_at": "2026-06-20"})
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: "M")

    svc.advance_workflow(
        doc_id="flowgate.default.0099.0001-B", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        locale="en", continuous=True, continuation_target_seq=6,
    )
    assert issue_kw["continuation_locale"] == "en"


def test_advance_managed_continuation_locale_none(monkeypatch):
    """Managed (non-continuous) advance leaves continuation_locale NULL."""
    from modules.flow_gate.services import workflow_decision_service as svc
    doc = {"doc_id": "flowgate.default.0099.0001-B", "group_id": "flowgate.default.0099",
           "project_id": "flowgate", "type_code": "B", "seq": 1}
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                        lambda _sid: {"type": "NR", "item_seq": 2, "label": "조사레포트",
                                      "result_doc_id": None, "result_doc_review_status": None,
                                      "id": 1})
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h=None: None)
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_ids",
                        lambda _s, _h=None, limit=2: [])
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    issue_kw = {}
    monkeypatch.setattr(svc.token_service, "issue",
                        lambda **k: issue_kw.update(k) or
                        {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                         "token_id": "tok", "expires_at": "2026-06-20"})
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: "M")

    svc.advance_workflow(
        doc_id="flowgate.default.0099.0001-B", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1", locale="en",
    )
    assert issue_kw["continuation_locale"] is None
