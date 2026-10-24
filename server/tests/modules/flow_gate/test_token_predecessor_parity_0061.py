"""0061 T0004 — token-path predecessor parity with advance_workflow.

NR0003 found that `advance_workflow` merges the two most recent predecessor
result docs into Section 3 'Reference documents' (previous + previous-previous
+ R), but the token-issuance path (`_build_mention_for_token`, used by the
NextActionModal flow) did not. The client only ticks the spine R and the step's
own instruction, so without a server-side merge the 2-predecessor was dropped
(e.g. NR is lost when building TR). These tests pin the merged ref_doc_ids that
`_build_mention_for_token` forwards to mention_service.
"""
from modules.flow_gate.api import token_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import mention_service


class _FakeRequest:
    base_url = "http://192.168.0.250:8089/"


def _capture(monkeypatch):
    """Wire up a TR-building scenario; return a dict that receives the kwargs
    passed to build_mention_from_token_rec."""
    captured: dict = {}

    parent_doc = {"doc_id": "G-R0001", "seq": 1, "revision_no": 0}
    docs = {
        "G-R0001": parent_doc,
        "G-T0004": {"doc_id": "G-T0004", "seq": 4},
        "G-NR0003": {"doc_id": "G-NR0003", "seq": 3},
    }
    monkeypatch.setattr(db_documents, "get_by_id", lambda did: docs.get(did))
    monkeypatch.setattr(db_documents, "get_group_max_seq", lambda gid: 4)
    monkeypatch.setattr(db_documents, "fetch_recent_group_docs", lambda **k: [])

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda did: {"id": 7})
    monkeypatch.setattr(
        db_wfseq, "get_effective_head", lambda sid: {"id": 4, "type": "TR"}
    )
    monkeypatch.setattr(
        db_wfseq, "get_predecessor_result_doc_id", lambda sid, hid: "G-T0004"
    )
    monkeypatch.setattr(
        db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda sid, hid, limit=2: ["G-T0004", "G-NR0003"],
    )

    def _fake_build(**kwargs):
        captured.update(kwargs)
        return "MENTION"

    monkeypatch.setattr(
        mention_service, "build_mention_from_token_rec", _fake_build
    )
    return captured


def test_token_path_merges_two_predecessors(monkeypatch):
    captured = _capture(monkeypatch)
    # Client ticked only the spine R + the step's own instruction (T0004).
    out = token_routes._build_mention_for_token(
        doc_ref="G-R0001",
        group_id="G",
        project_id="flowgate",
        scratch_dir="/tmp",
        raw_token="tok",
        request=_FakeRequest(),
        ref_doc_ids=["G-R0001", "G-T0004"],
    )
    assert out == "MENTION"
    # NR0003 must be appended; T0004 already present must not duplicate; order =
    # client selection first, then any missing predecessors.
    assert captured["ref_doc_ids"] == ["G-R0001", "G-T0004", "G-NR0003"]


def test_token_path_first_report_step_has_single_predecessor(monkeypatch):
    captured = _capture(monkeypatch)
    # First report step: only one predecessor produced so far.
    monkeypatch.setattr(
        db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda sid, hid, limit=2: ["G-T0004"],
    )
    token_routes._build_mention_for_token(
        doc_ref="G-R0001",
        group_id="G",
        project_id="flowgate",
        scratch_dir="/tmp",
        raw_token="tok",
        request=_FakeRequest(),
        ref_doc_ids=["G-R0001"],
    )
    assert captured["ref_doc_ids"] == ["G-R0001", "G-T0004"]


def test_token_path_no_sequence_passes_selection_through(monkeypatch):
    captured = _capture(monkeypatch)
    # 0084 TR0005 (B): _build_mention_for_token now resolves the sequence via the
    # member-doc-aware helper, so the no-sequence seam to stub is get_sequence_for_member_doc.
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda did: None)
    token_routes._build_mention_for_token(
        doc_ref="G-R0001",
        group_id="G",
        project_id="flowgate",
        scratch_dir="/tmp",
        raw_token="tok",
        request=_FakeRequest(),
        ref_doc_ids=["G-R0001"],
    )
    assert captured["ref_doc_ids"] == ["G-R0001"]
