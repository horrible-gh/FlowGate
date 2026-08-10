"""0370 T0012 — the worker must be able to make bounded document queries.

0394 T0004 (NR0003 §4.2 S5) rewrote the first case here. 0370 satisfied this by printing
the whole lookup catalogue into every mention; the 0372 mention-reduction chain then took
that block out of the document-workflow mentions and left the addresses in the help
catalog, which those mentions link to. The old assertions searched the mention text for
`[문서 조회 도구]`, so they went red for the reduction itself while the capability they
guard was intact — the worker still reaches every endpoint, one GET further along.

What is worth guarding is the capability, so this file now checks it end to end instead
of checking one wording:

  * the mention hands over the help catalog and the token that opens it, and
  * the catalog's `document_access` item really does carry the bounded reads.

Either half alone is worthless — a link to a catalog that lost the addresses, or
addresses no mention points at — so both are asserted, and the endpoint list itself is
still pinned exactly as 0370 wrote it.

The chat and Q-answer mentions are unchanged: they still inline the catalogue (they are
not document-workflow mentions and the reduction did not touch them), and the second case
below still checks them that way.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402

BASE = "http://host/flowgate/api/v1"
PROJECT = "flowgate"
GROUP_ID = "flowgate.default.0370"
DOC_ID = "flowgate.default.0370.0001-R"
TOKEN = "worker-token"


def _assert_reaches_the_catalog(text: str) -> None:
    """The mention hands over the help catalog and the credential that opens it."""
    assert f"GET {BASE}/help" in text
    assert f"{BASE}/help/items/" in text
    assert f"Authorization: Bearer {TOKEN}" in text


def _assert_inline_lookup(text: str, doc_id: str = DOC_ID) -> None:
    """The mention spells the bounded endpoints out in its own body."""
    assert "Efficient document lookup" in text or "[문서 조회 도구]" in text or "Document search and lookup rules" in text
    _assert_bounded_endpoints(text, doc_id)
    assert f"Authorization: Bearer {TOKEN}" in text


def _assert_bounded_endpoints(text: str, doc_id: str) -> None:
    """The five bounded reads 0370 added, spelled exactly as a caller must send them."""
    assert f"/document/{doc_id}/meta" in text
    assert f"/document/{doc_id}/outline" in text
    assert f"/document/{doc_id}/section?section_id=<section_id>" in text
    assert f"/document/{doc_id}/relations" in text
    assert "/search/documents/content?q=<keyword>&project=flowgate" in text
    assert "include_matches=true&context_lines=2&hits_per_doc=5" in text


def test_the_help_catalog_carries_the_bounded_reads(monkeypatch):
    """The other end of the link: what a worker gets when it follows the mention.

    Reading the item through `build_item` rather than the private content builder keeps
    this honest about visibility — an item that stopped being served to workers would
    not be reachable here either.
    """
    monkeypatch.setattr(
        help_catalog.tool_registry,
        "resolve_registry",
        lambda *_a, **_k: {"kind": "read_only", "source_mode": "remote", "reason": None},
    )
    ctx = help_catalog.resolve_context(
        {"project": PROJECT, "group_id": GROUP_ID, "doc_ref": DOC_ID, "action_scope": "new"},
        "ko",
        BASE,
    )
    assert "document_access" in help_catalog.visible_names(ctx)

    partial = help_catalog.build_item("document_access", ctx)["content"]["partial"]
    urls = "\n".join(
        entry["url"] for entry in partial.values() if isinstance(entry, dict) and "url" in entry
    )
    # The catalog templates the doc id; the caller substitutes it.
    _assert_bounded_endpoints(urls.replace("{doc_id}", DOC_ID), DOC_ID)


def test_document_creation_edit_and_workflow_mentions_expose_bounded_queries(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)
    monkeypatch.setattr(mention_service.template_provision, "is_design_type", lambda _type: False)

    ordinary = mention_service.build_mention(
        project=PROJECT,
        module="default",
        group="0370",
        parent_type="R",
        parent_doc_number="R0001",
        parent_title="Document lookup",
        parent_doc_id=DOC_ID,
        parent_canonical_doc_id=DOC_ID,
        head_type="TR",
        head_status="pending",
        scratch_dir="C:/scratch",
        raw_token=TOKEN,
        api_base_url=BASE,
        group_id=GROUP_ID,
        ref_doc_ids=[DOC_ID],
    )
    assert ordinary is not None
    _assert_reaches_the_catalog(ordinary)
    assert "successful new/edit response includes `change_summary`" in ordinary

    token_rec = {"project": PROJECT, "group_id": GROUP_ID, "scratch_dir": "C:/scratch"}
    target_doc = {
        "doc_id": DOC_ID,
        "type_code": "R",
        "seq": 1,
        "title": "Document lookup",
        "module": "default",
    }
    decision = mention_service.build_workflow_decision_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
    )
    _assert_reaches_the_catalog(decision)

    sequence_edit = mention_service.build_sequence_edit_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
        sequence_items=[],
    )
    _assert_reaches_the_catalog(sequence_edit)

    review = mention_service.build_review_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
    )
    assert review is not None
    _assert_reaches_the_catalog(review)


def test_chat_and_q_answer_mentions_expose_the_same_queries(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)

    chat_sections = invoke_mention_service._chat_lookup_sections(
        base=BASE,
        raw_token=TOKEN,
        project=PROJECT,
        group_name=GROUP_ID,
    )
    chat_text = "\n".join(chat_sections)
    _assert_inline_lookup(chat_text, "<doc_id>")

    monkeypatch.setattr(q_answer_invoke_service.db_groups, "get_by_id", lambda _gid: None)
    answer = q_answer_invoke_service.build_answer_mention(
        doc={
            "doc_id": DOC_ID,
            "group_id": GROUP_ID,
            "project_id": PROJECT,
            "title": "Document lookup",
        },
        item={"id": 7, "seq": 1, "title": "Question", "body": "What changed?", "options": []},
        raw_token=TOKEN,
        scratch_dir="C:/scratch",
        api_base_url=BASE,
    )
    _assert_inline_lookup(answer)
