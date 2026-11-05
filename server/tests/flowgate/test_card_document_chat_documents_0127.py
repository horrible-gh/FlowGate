"""0127 CH0028: chat read / existing-body edit / new-document create by doc_id.

Real SQLite, files, CAS, revisions, events and contacts (the T#1/T#7 fixtures). The user and
the model send only a doc_id: the server resolves the card line or takes the non-card path.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_document_edit_0127 import env, F, D, HEADER  # noqa: F401
from test_card_document_chat_adapter_0127 import chat, error  # noqa: F401
from test_card_document_chat_0127 import RUN_ID
from test_card_document_edit_composition_0127 import composition  # noqa: F401
from flowgate.card_document_edit import chat_authz, chat_documents, chat_tools, call_ledger, target
from flowgate.card_documents import repository
from modules.flow_gate.db import documents, document_revisions

DOC_URL = "/flowgate/api/v2/chat/documents/"
H = {"Authorization": "Bearer chat"}


def api(chat, tool, inp, ctx=None):
    handlers = {chat_tools.READ_DOC_TOOL: chat_documents.read_document_call_handler,
                chat_tools.REVISE_DOC_TOOL: chat_documents.revise_document_call_handler,
                chat_tools.CREATE_DOC_TOOL: chat_documents.create_document_call_handler}
    ctx = ctx or call_ledger.accept_call(chat.run, {"id": "provider-" + tool, "name": tool, "input": inp})
    return handlers[tool](chat.run, ctx), ctx


def as_result(response):
    return response.status_code, response.json()


def non_card():
    repository.delete(F, "research", "instruction", "N")


def test_descriptors_take_doc_id_only_and_never_card_coordinates():
    tools = {t["name"]: t for t in chat_tools.descriptors()}
    for name in (chat_tools.READ_DOC_TOOL, chat_tools.REVISE_DOC_TOOL, chat_tools.CREATE_DOC_TOOL):
        props = tools[name]["schema"]["properties"]
        assert not {"flow_id", "node_key", "slot"} & set(props)
        assert tools[name]["schema"]["additionalProperties"] is False
    assert tools[chat_tools.REVISE_DOC_TOOL]["schema"]["required"] == ["doc_id", "expected_revision", "content"]
    assert tools[chat_tools.CREATE_DOC_TOOL]["schema"]["required"] == ["title", "content"]
    assert "/documents/{doc_id}/revisions" in chat_tools.cli_document_mention_section("http://h/flowgate/api/v1", "t")


def test_api_card_bound_document_by_doc_id_saves_through_revise_and_replays(chat):
    got, _ = api(chat, chat_tools.READ_DOC_TOOL, {"doc_id": D})
    assert got[0] == 200 and got[1]["edit_path"] == "card" and got[1]["revision_no"] == 0
    assert got[1]["content"] == HEADER + "original\r\n"
    inp = {"doc_id": D, "expected_revision": 0, "content": HEADER + "written from chat\n"}
    saved, ctx = api(chat, chat_tools.REVISE_DOC_TOOL, inp)
    assert saved[0] == 200, saved
    assert saved[1]["doc_id"] == D and saved[1]["revision_no"] == 1 and saved[1]["replayed"] is False
    assert saved[1]["edit_path"] == "card" and saved[1]["line"]["slot"] == "instruction"
    again, _ = api(chat, chat_tools.REVISE_DOC_TOOL, inp, ctx=ctx)
    assert again[0] == 200 and again[1]["replayed"] is True and again[1]["revision_no"] == 1
    reread, _ = api(chat, chat_tools.READ_DOC_TOOL, {"doc_id": D})
    assert reread[1]["revision_no"] == 1 and reread[1]["content"] == HEADER + "written from chat\n"
    history = document_revisions.list_by_doc(D)
    assert len(history) == 1 and history[0]["created_by"] == "user"
    meta = json.loads(chat.env.db._fetch_one("SELECT metadata FROM workflow_events WHERE event_type='doc_edited'")["metadata"])
    assert meta["origin"] == "chat" and meta["node_key"] == "research" and meta["slot"] == "instruction"


def test_cli_non_card_document_edit_conflict_replay_and_reread(chat):
    non_card()
    assert target.classify_card_slot(documents.get_by_id(D)) == target.NON_CARD
    read = chat.env.client.get(DOC_URL + D, headers=H)
    assert read.status_code == 200 and read.json()["edit_path"] == "non_card"
    body = {"expected_revision": 0, "content": "body without front matter\n", "idempotency_key": "save-a"}
    saved = as_result(chat.env.client.post(DOC_URL + D + "/revisions", headers=H, json=body))
    assert saved[0] == 200, saved
    assert saved[1]["revision_no"] == 1 and saved[1]["edit_path"] == "non_card" and saved[1]["replayed"] is False
    # Same attempt retransmitted: the earlier result, no second revision.
    again = as_result(chat.env.client.post(DOC_URL + D + "/revisions", headers=H, json=body))
    assert again[0] == 200 and again[1]["replayed"] is True and again[1]["revision_no"] == 1
    # A stale base is a conflict and writes nothing.
    stale = as_result(chat.env.client.post(DOC_URL + D + "/revisions", headers=H,
                                           json={**body, "content": "other\n", "idempotency_key": "save-b"}))
    assert error(stale, 409, "revision_conflict")["revision_no"] == 1
    reread = chat.env.client.get(DOC_URL + D, headers=H).json()
    # The current front matter is kept when the body is sent without it; doc_id unchanged.
    assert reread["doc_id"] == D and reread["revision_no"] == 1
    assert reread["content"] == HEADER + "\nbody without front matter\n"
    assert chat.env.path.read_bytes().decode() == reread["content"]
    assert len(document_revisions.list_by_doc(D)) == 1
    row = chat.env.db._fetch_one("SELECT actor_user_id, metadata FROM workflow_events WHERE event_type='doc_edited'")
    meta = json.loads(row["metadata"])
    assert row["actor_user_id"] == "user" and meta["origin"] == "chat" and meta["idem_key"].startswith("chat-cli:")


def test_api_non_card_edit_and_identity_change_refused(chat):
    non_card()
    changed = HEADER.replace("doc_number: 0002-N", "doc_number: 0009-N")
    bad, _ = api(chat, chat_tools.REVISE_DOC_TOOL, {"doc_id": D, "expected_revision": 0, "content": changed + "x\n"})
    assert bad[0] == 422 and documents.get_by_id(D)["revision_no"] == 0
    ok, _ = api(chat, chat_tools.REVISE_DOC_TOOL, {"doc_id": D, "expected_revision": 0, "content": HEADER + "api\n"})
    assert ok[0] == 200 and ok[1]["revision_no"] == 1


def test_missing_document_is_not_created_and_read_mode_refuses(chat, monkeypatch):
    missing, _ = api(chat, chat_tools.REVISE_DOC_TOOL, {"doc_id": F + ".0099-R", "expected_revision": 0, "content": "x"})
    assert error(missing, 404, "document_not_found")["doc_id"] == F + ".0099-R"
    assert documents.get_by_id(F + ".0099-R") is None
    monkeypatch.setattr(chat_authz, "_kind_for_token", lambda row: ("read", None))
    denied, _ = api(chat, chat_tools.REVISE_DOC_TOOL, {"doc_id": D, "expected_revision": 0, "content": HEADER + "x\n"})
    assert error(denied, 403, "chat_document_edit_mode_denied")["effective_kind"] == "read"
    created, _ = api(chat, chat_tools.CREATE_DOC_TOOL, {"title": "New", "content": "body"})
    assert error(created, 403, "chat_document_edit_mode_denied")
    assert documents.get_by_id(D)["revision_no"] == 0


def test_unknown_fields_and_card_coordinates_are_rejected(chat):
    bad, _ = api(chat, chat_tools.REVISE_DOC_TOOL, {"doc_id": D, "expected_revision": 0, "content": "x",
                                                    "flow_id": F})
    assert error(bad, 422, "invalid_request")["errors"][0] == {"field": "flow_id", "reason": "unknown_field"}


def test_create_new_document_registers_once_and_is_separate_from_edit(chat, monkeypatch):
    from modules.flow_gate import process_service
    from modules.flow_gate.rbac import permission_service
    monkeypatch.setattr(permission_service, "has_permission", lambda *a: True)
    calls = []

    def create_workflow_root(**kw):
        calls.append(kw)
        from modules.flow_gate.db import groups
        group_id = "flowgate.v02.%04d" % (199 + len(calls))
        groups.create({"group_id": group_id, "project_id": kw["project"], "module": kw["module"],
                       "title": kw["title"], "status": "OPEN"})
        doc_id = group_id + ".0001-" + kw["doc_type"]
        documents.create({"doc_id": doc_id, "project_id": kw["project"], "module": kw["module"],
                          "group_id": group_id, "type_code": kw["doc_type"], "seq": 1,
                          "title": kw["title"], "status": "draft", "owner_id": kw["owner"]})
        return {"status": "success", "doc_id": doc_id, "group_id": group_id}
    monkeypatch.setattr(process_service, "create_workflow_root", create_workflow_root)
    with_header, _ = api(chat, chat_tools.CREATE_DOC_TOOL, {"title": "T", "content": "---\ntype: R\n---\nx"})
    assert error(with_header, 422, "invalid_content")
    inp = {"title": "Chat requirement", "content": "## Goal\nwritten from chat\n"}
    created, ctx = api(chat, chat_tools.CREATE_DOC_TOOL, inp)
    assert created[0] == 200, created
    assert created[1]["doc_id"] == "flowgate.v02.0200.0001-R" and created[1]["created"] is True
    assert calls == [{"project": "flowgate", "module": "v02", "title": "Chat requirement", "slug": "",
                      "priority": "medium", "body": inp["content"], "owner": "user", "doc_type": "R",
                      "locale": "ko"}]
    again, _ = api(chat, chat_tools.CREATE_DOC_TOOL, inp, ctx=ctx)
    assert again[1]["replayed"] is True and again[1]["doc_id"] == created[1]["doc_id"] and len(calls) == 1
    cli = as_result(chat.env.client.post(DOC_URL.rstrip("/"), headers=H,
                                         json={"title": "Bug", "content": "", "doc_type": "B", "idempotency_key": "new-1"}))
    assert cli[0] == 200 and cli[1]["type_code"] == "B" and len(calls) == 2
    monkeypatch.setattr(permission_service, "has_permission", lambda *a: False)
    denied, _ = api(chat, chat_tools.CREATE_DOC_TOOL, {"title": "No", "content": "x"})
    assert error(denied, 403, "permission_denied") and len(calls) == 2
    assert chat.run.get("run_id") == RUN_ID


def test_chat_edit_passes_only_its_own_runs_group_lease(chat, monkeypatch):
    from flowgate.card_document_edit import guards
    from modules.flow_gate.db import group_ai_leases, tokens
    from modules.flow_gate.services import mutation_policy
    from flowgate.card_document_edit.contracts import Origin, Actor
    from types import SimpleNamespace
    token = tokens.get_by_id(chat.token["token_id"])
    lease = {"group_id": F, "run_id": RUN_ID, "token_id": token["token_id"], "action_scope": "chat"}
    monkeypatch.setattr(group_ai_leases, "get_active", lambda g: dict(lease) if g == F else None)
    own = SimpleNamespace(actor=Actor("user", "person_delegated"),
                          origin=Origin("chat", run_id=RUN_ID, token_id=token["token_id"]))
    principal = guards.principal_of(own, F)
    assert principal.kind == "worker" and principal.run_id == RUN_ID and principal.action_scope == "chat"
    # Another run's lease, another group, or another user: the human principal (refused by the lease).
    lease["run_id"] = "aiv_other"
    assert guards.principal_of(own, F).kind == "human"
    lease["run_id"] = RUN_ID
    assert guards.principal_of(own, "other.group").kind == "human"
    other = SimpleNamespace(actor=Actor("other", "person_delegated"), origin=own.origin)
    assert guards.principal_of(other, F).kind == "human"
    # The human principal is what the existing lease check refuses (423 GROUP_AI_RUN_LOCKED).
    assert mutation_policy.human_principal({"user_id": "other"}).kind == "human"
