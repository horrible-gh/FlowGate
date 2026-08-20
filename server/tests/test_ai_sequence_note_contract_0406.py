"""0406 T0011: AI sequence-edit metadata and stored step-note contracts."""
from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import workflow_decision_routes  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as ai_svc  # noqa: E402
from modules.flow_gate.services import help_catalog, mention_service  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wf_svc  # noqa: E402
from routers.main import app  # noqa: E402

ROOT = "flowgate.default.0406.0001-B"
PATH = "/flowgate/api/v1/workflow/sequence"
HEADERS = {"Authorization": "Bearer test-token"}
BASE_MENTION = "## 지시\n문서를 작성하세요.\n"
INITIAL = [
    {
        "id": 1, "item_seq": 1, "type": "M", "label": "Memo", "doc_class": "B",
        "sort_order": 1, "status": "pending", "result_doc_id": None,
        "note": "  Keep this handoff  ", "source_doc_id": "flowgate.default.0406.0004-WP",
        "source_revision_no": 7,
    },
    {
        "id": 2, "item_seq": 2, "type": "D", "label": "Design", "doc_class": "B",
        "sort_order": 2, "status": "pending", "result_doc_id": None,
        "note": "Design handoff", "source_doc_id": "flowgate.default.0406.0004-WP",
        "source_revision_no": 7,
    },
]


class MemoryStore:
    @contextmanager
    def transaction(self):
        yield


@pytest.fixture
def sequence_world(monkeypatch):
    state = [dict(row) for row in INITIAL]
    sequence = {"id": 406, "doc_id": ROOT, "head_advanced_at": None}
    owner = {
        "doc_id": ROOT, "type_code": "B", "seq": 1, "title": "Root",
        "project_id": "flowgate", "group_id": "flowgate.default.0406",
        "doc_review_status": "wf_in_progress",
    }
    monkeypatch.setattr(
        workflow_decision_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_test", "is_admin": True},
    )
    monkeypatch.setattr(workflow_decision_routes, "_active_ai_run_response_for_user", lambda d, a: None)
    monkeypatch.setattr(wf_svc.db_documents, "get_by_id", lambda doc_id: dict(owner) if doc_id == ROOT else None)
    monkeypatch.setattr(wf_svc.db_documents, "update", lambda doc_id, values: None)
    monkeypatch.setattr(wf_svc.db_documents, "list_documents", lambda **kwargs: [])
    monkeypatch.setattr(wf_svc.db_documents, "delete", lambda doc_id: None)
    monkeypatch.setattr(wf_svc.db_wfseq, "get_sequence_by_doc_id", lambda doc_id: dict(sequence))
    monkeypatch.setattr(wf_svc.db_wfseq, "get_sequence_items", lambda seq_id: [dict(row) for row in state])
    monkeypatch.setattr(
        wf_svc.db_wfseq, "get_max_item_seq",
        lambda seq_id: max((row["item_seq"] for row in state), default=0),
    )
    monkeypatch.setattr(
        wf_svc.db_wfseq, "delete_pending_items",
        lambda seq_id: state.__setitem__(slice(None), [r for r in state if r["status"] != "pending"]),
    )

    def insert_sequence_item(**values):
        state.append({
            "id": 100 + len(state), "item_seq": values["item_seq"], "type": values["type_"],
            "label": values["label"], "doc_class": values["doc_class"],
            "sort_order": values["sort_order"], "status": "pending", "result_doc_id": None,
            "note": values["note"], "source_doc_id": values["source_doc_id"],
            "source_revision_no": values["source_revision_no"],
        })

    monkeypatch.setattr(wf_svc.db_wfseq, "insert_sequence_item", insert_sequence_item)
    monkeypatch.setattr(wf_svc, "get_store", lambda: MemoryStore())
    monkeypatch.setattr(
        wf_svc.token_service, "issue",
        lambda **kwargs: {
            "raw_token": "tok_raw", "token_id": "tok_id", "expires_at": None,
            "scratch_dir": "C:/scratch",
        },
    )
    return state


def extract_pending_json(mention: str) -> list[dict]:
    fence = chr(96) * 3
    match = re.search(re.escape(fence) + r"json\n(\[.*?\])\n" + re.escape(fence), mention, re.S)
    assert match, mention
    return json.loads(match.group(1))


def issue_mention() -> str:
    return wf_svc.request_sequence_edit(
        ROOT, "worker", "http://127.0.0.1:8089/flowgate/api/v1", locale="en",
    )["mention"]


PAYLOAD_KEYS = {
    "type", "label", "note", "source_doc_id", "source_revision_no",
    # 0444 T0007 (NR0003 §4-6): note/source were not the only values edit_workflow_pending()
    # rewrites from the payload — the provider pair is rewritten the same way, and until this
    # change it was in none of the five places that tell a worker what to send back. Widened
    # from five keys to seven; the three this test is named for still round-trip below.
    "provider_id", "provider_display_name",
}


def test_mention_json_round_trips_three_metadata_fields_through_real_patch_route(sequence_world):
    mention = issue_mention()
    items = extract_pending_json(mention)
    assert all(set(item) == PAYLOAD_KEYS for item in items)
    assert items[0]["note"] == "Keep this handoff"

    response = TestClient(app, raise_server_exceptions=False).patch(
        PATH, json={"doc_id": ROOT, "items": items}, headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    metadata = [
        (row["note"], row["source_doc_id"], row["source_revision_no"])
        for row in sequence_world
    ]
    assert metadata == [
        ("Keep this handoff", "flowgate.default.0406.0004-WP", 7),
        ("Design handoff", "flowgate.default.0406.0004-WP", 7),
    ]
    print("MENTION_PATCH_METADATA=" + json.dumps(metadata, ensure_ascii=False))


def test_retyped_row_and_automatic_report_have_empty_metadata(sequence_world):
    items = extract_pending_json(issue_mention())
    items[1] = {
        "type": "T", "label": "Retyped task", "note": "",
        "source_doc_id": None, "source_revision_no": None,
    }
    response = TestClient(app, raise_server_exceptions=False).patch(
        PATH, json={"doc_id": ROOT, "items": items}, headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert [row["type"] for row in sequence_world] == ["M", "T", "TR"]
    for row in sequence_world[1:]:
        assert row["note"] == ""
        assert row["source_doc_id"] is None
        assert row["source_revision_no"] is None


def test_metadata_free_mention_is_byte_identical_to_legacy_shape():
    common = dict(
        token_rec={"project": "flowgate", "group_id": "flowgate.default.0406"},
        target_doc={"doc_id": ROOT, "type_code": "B", "seq": 1, "title": "Root"},
        api_base_url="http://x/flowgate/api/v1", raw_token="tok", locale="en",
    )
    legacy = [{"type": "M", "label": "Memo", "status": "pending"}]
    extended = [{
        "item_seq": 1, "type": "M", "label": "Memo", "status": "pending",
        "note": "", "source_doc_id": None, "source_revision_no": None,
    }]
    assert mention_service.build_sequence_edit_mention(
        **common, sequence_items=extended,
    ) == mention_service.build_sequence_edit_mention(**common, sequence_items=legacy)


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_help_submit_declares_the_same_seven_keys_and_rules(locale):
    """The help example and the mention payload must describe ONE contract.

    Old expectation (0406 T0011): exactly the five keys
    type/label/note/source_doc_id/source_revision_no, and a rules sentence naming the three
    metadata values.

    New expectation (0444 T0007): those five plus provider_id/provider_display_name, and a
    rules sentence that also says what an omitted provider key means (keep what is stored)
    and how to actually clear it (an explicit null).

    Basis: NR0003 §4-6 / 0444 T0007 §4-1. The five coordinates that describe this PATCH to an
    AI worker — request_sequence_edit's row list, the mention payload, the mention rules, this
    help example and its guidance note — all named the same five keys, so the worker had no
    way to return a provider, and edit_workflow_pending() then deleted it for not being sent.
    Widening the contract in one place only would leave the worker reading a different one
    from the four others, so the assertion moves with it rather than being deleted.
    """
    content = help_catalog._content_submit({
        "base_url": "/flowgate/api/v1", "action_scope": "workflow_sequence_edit",
        "doc_id": ROOT, "locale": locale,
    })
    assert set(content["body"]["items"][0]) == PAYLOAD_KEYS
    assert "note/source_doc_id/source_revision_no" in content["guidance"]
    assert "provider_id" in content["guidance"]
    assert "null" in content["guidance"]

def wire_note_rows(monkeypatch, *, stored="저장 멘트"):
    rows = [
        {"item_seq": 3, "type": "T", "status": "pending", "note": stored},
        {"item_seq": 4, "type": "TR", "status": "pending", "note": "레포트 멘트"},
    ]
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_for_member_doc", lambda doc: {"id": 1})
    monkeypatch.setattr(ai_svc.db_wfseq, "get_effective_head", lambda seq_id: dict(rows[0]))
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_items", lambda seq_id: [dict(r) for r in rows])


@pytest.mark.parametrize(
    "overrides,stored,expected",
    [
        ({"3": "요청 멘트"}, "저장 멘트", "요청 멘트"),
        ({"3": ""}, "저장 멘트", None),
        ({}, "저장 멘트", "저장 멘트"),
        ({}, "", None),
    ],
)
def test_step_note_priority_table(monkeypatch, overrides, stored, expected):
    wire_note_rows(monkeypatch, stored=stored)
    got = ai_svc._inject_hop_notes(
        BASE_MENTION, ROOT, default_note=None, note_overrides=overrides,
        instruction_mode=None, locale="ko", fold_worker_item_seq=False,
    )
    wanted = (
        invoke_mention_service.prepend_messages_section(BASE_MENTION, [expected], "ko")
        if expected else BASE_MENTION
    )
    assert got == wanted


def test_single_uses_head_note_while_continuous_uses_folded_report_note(monkeypatch):
    wire_note_rows(monkeypatch)
    single = ai_svc._inject_hop_notes(
        BASE_MENTION, ROOT, default_note=None, note_overrides=None,
        instruction_mode=None, locale="ko", fold_worker_item_seq=False,
    )
    continuous = ai_svc._inject_hop_notes(
        BASE_MENTION, ROOT, default_note=None, note_overrides=None,
        instruction_mode="auto_approved", locale="ko", fold_worker_item_seq=True,
    )
    assert "저장 멘트" in single and "레포트 멘트" not in single
    assert "레포트 멘트" in continuous and "저장 멘트" not in continuous
    print("SINGLE_PROMPT=" + json.dumps({"before": BASE_MENTION, "after": single}, ensure_ascii=False))


@pytest.mark.parametrize(
    "scope", ["edit", "review", "chat", "test_run", "resolve_conflict", "workflow_sequence_edit"],
)
def test_unrelated_single_retry_prompt_is_byte_identical(monkeypatch, scope):
    monkeypatch.setattr(ai_svc.db_tokens, "get_by_id", lambda token_id: None)
    monkeypatch.setattr(
        ai_svc.db_wfseq, "get_sequence_for_member_doc",
        lambda doc: (_ for _ in ()).throw(AssertionError("unrelated scope queried sequence note")),
    )
    run = {
        "run_id": "run-1", "token_id": None, "mode": "single", "action_scope": scope,
        "doc_ref": ROOT, "issue_builder": lambda **kwargs: {
            "mention": BASE_MENTION, "token_id": "tok-2", "raw_token": "raw",
        },
        "group_id": None,
    }
    assert ai_svc._prepare_retry_token(run)["mention"] == BASE_MENTION


def test_stored_note_lookup_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(
        ai_svc.db_wfseq, "get_sequence_for_member_doc",
        lambda doc: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert ai_svc._inject_hop_notes(
        BASE_MENTION, ROOT, default_note=None, note_overrides=None,
        instruction_mode=None, locale="ko", fold_worker_item_seq=False,
    ) == BASE_MENTION


def test_pause_json_round_trip_preserves_blank_tombstone_key():
    dumped = db_paused.dump_json_map({"3": "", "4": "keep"})
    assert db_paused.load_json_map(dumped) == {"3": "", "4": "keep"}