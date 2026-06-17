"""R0001 #1 — advance no longer locks out for 24h on a stale unconsumed token.

The Q149 double-advance guard used to raise head_in_progress (-> HTTP 409) whenever an
unconsumed token already existed for the doc_ref. Because the token TTL is 24h, an
abandoned advance would block every re-advance on the same document for up to a day
("persistent 409"). The guard now *supersedes* the stale token: it revokes it and issues
a fresh one, preserving the "at most one active token per doc_ref" invariant.
"""
from unittest.mock import MagicMock

from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.services import workflow_decision_service as svc


def _wire_happy_path(monkeypatch):
    """Stub every advance_workflow dependency except the token-guard branch under test."""
    doc = {
        "doc_id": "flowgate.default.0001.0001-R",
        "group_id": "flowgate.default.0001",
        "project_id": "flowgate",
        "type_code": "R",
        "seq": 1,
    }
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 7})
    monkeypatch.setattr(
        svc.db_wfseq,
        "get_effective_head",
        lambda _sid: {"type": "T", "label": "작업", "result_doc_id": None,
                      "result_doc_review_status": None},
    )
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(
        svc.db_wfseq, "get_predecessor_result_doc_id", lambda _sid, _hid=None: None
    )
    monkeypatch.setattr(
        svc.db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda _sid, _hid=None, limit=2: [],
    )
    monkeypatch.setattr(
        svc.token_service,
        "issue",
        lambda **_k: {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                      "token_id": "tok-new", "expires_at": "2026-06-12T00:00:00"},
    )
    monkeypatch.setattr(
        svc.mention_service, "build_mention_from_token_rec", lambda **_k: "mention-text"
    )


def test_advance_supersedes_stale_unconsumed_token(monkeypatch):
    _wire_happy_path(monkeypatch)
    monkeypatch.setattr(
        db_tokens, "get_unconsumed_by_doc_ref", lambda _id: {"token_id": "tok-stale"}
    )
    revoke = MagicMock()
    monkeypatch.setattr(svc.token_service, "revoke", revoke)

    result = svc.advance_workflow(
        doc_id="flowgate.default.0001.0001-R",
        issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
    )

    # The stale token is revoked (not a 409) and a fresh token is handed back.
    revoke.assert_called_once()
    assert revoke.call_args.args[0] == "tok-stale"
    assert revoke.call_args.kwargs.get("reason") == "superseded_by_readvance"
    assert result["token"] == "RAW"
    assert result["token_id"] == "tok-new"


def test_advance_without_stale_token_does_not_revoke(monkeypatch):
    _wire_happy_path(monkeypatch)
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    revoke = MagicMock()
    monkeypatch.setattr(svc.token_service, "revoke", revoke)

    result = svc.advance_workflow(
        doc_id="flowgate.default.0001.0001-R",
        issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
    )

    revoke.assert_not_called()
    assert result["token_id"] == "tok-new"


def test_advance_seeds_two_predecessors_into_ref_doc_ids(monkeypatch):
    """R0001 #1: advance appends the two most recent predecessor result docs to the
    client-supplied ref_doc_ids, deduped, so the worker receives R + previous +
    previous-previous = 3 reference documents."""
    _wire_happy_path(monkeypatch)
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    # Two completed predecessors (newest first): the step's own instruction T (which the
    # client also passes) and the prior report NR.
    monkeypatch.setattr(
        svc.db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda _sid, _hid=None, limit=2: [
            "flowgate.default.0001.0003-T",
            "flowgate.default.0001.0002-NR",
        ],
    )
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return "mention-text"

    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec", _capture)

    svc.advance_workflow(
        doc_id="flowgate.default.0001.0001-R",
        issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        # Client passes the spine R + the step's own instruction T (the live behavior).
        ref_doc_ids=["flowgate.default.0001.0001-R", "flowgate.default.0001.0003-T"],
    )

    refs = captured["ref_doc_ids"]
    # T is supplied by both client and predecessor list — it must appear once.
    assert refs == [
        "flowgate.default.0001.0001-R",
        "flowgate.default.0001.0003-T",
        "flowgate.default.0001.0002-NR",
    ]


def test_advance_falls_back_to_two_docs_at_first_report_step(monkeypatch):
    """R0001 #1: at the first report step only one predecessor exists, so the worker gets
    R + that single predecessor = 2 docs (no fabricated third)."""
    _wire_happy_path(monkeypatch)
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    monkeypatch.setattr(
        svc.db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda _sid, _hid=None, limit=2: ["flowgate.default.0001.0002-N"],
    )
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return "mention-text"

    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec", _capture)

    svc.advance_workflow(
        doc_id="flowgate.default.0001.0001-R",
        issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        ref_doc_ids=["flowgate.default.0001.0001-R", "flowgate.default.0001.0002-N"],
    )

    assert captured["ref_doc_ids"] == [
        "flowgate.default.0001.0001-R",
        "flowgate.default.0001.0002-N",
    ]
