"""flowgate.v02test.0023 (0002-CH turn 43) — chat control tower, stage 1.

A chat in edit mode places work cards on its own Flow (``flowgate.chat_control``), writes their instructions with the
existing card document edit, and starts the Flow's run. Covered here:

- the placement rule (pure): a Flow with only its bug card, this group's real Flow (bug -> confirm -> confirm, all
  done), insertion before an unstarted confirm card, explicit / unknown / ambiguous anchors;
- the service on the real Graph routes and SQLite DB (``test_fg_card_settings`` harness): the draft gets
  bug -> work -> confirm, each card's instruction line exists, a start with an empty instruction is refused, a written
  instruction lets the draft be confirmed and the run start;
- the authority (chat token, edit mode, write permission), the API tool envelope, the CLI mention and the CLI routes.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, FA, _h, _sql, db_path, decided, env, node, put_draft,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")


# ── placement (pure) ─────────────────────────────────────────────────────────

def _n(key, node_type="document", doc_type="T"):
    return {"node_key": key, "node_type": node_type, "doc_type": doc_type if node_type == "document" else None,
            "palette_item_id": None, "label": key, "assignee_kind": None}


def _e(edge_id, source, target, condition=None):
    return {"edge_id": edge_id, "source": source, "target": target, "condition": condition}


def test_bug_card_only_gets_work_then_a_new_confirm_card():
    from flowgate.chat_control import placement

    plan = placement.plan([_n("gn_b", doc_type="B")], [], set())
    assert plan == placement.Plan("gn_b", None, None, True)
    body = placement.draft_body({"nodes": [_n("gn_b", doc_type="B")], "edges": []}, 0, plan,
                                [{"doc_type": "T", "label": "버그 수정", "palette_item_id": "task"}], "확인")
    assert [(n["client_key"] or n["node_key"], n["node_type"], n["doc_type"]) for n in body["nodes"]] == [
        ("gn_b", "document", "B"), ("tmp_cc_n0", "document", "T"), ("tmp_cc_gate", "gate", None)]
    assert [(e["source"], e["target"]) for e in body["edges"]] == [("gn_b", "tmp_cc_n0"), ("tmp_cc_n0", "tmp_cc_gate")]
    assert body["nodes"][2]["palette_item_id"] == "confirm" and body["nodes"][2]["label"] == "확인"


def test_finished_flow_is_extended_after_its_tail_never_before_a_done_confirm_card():
    """This group's Flow: bug -> confirm -> confirm, every card done in the idle run."""
    from flowgate.chat_control import placement

    nodes = [_n("gn_b", doc_type="B"), _n("gn_g1", "gate"), _n("gn_g2", "gate")]
    edges = [_e("ge_1", "gn_b", "gn_g1"), _e("ge_2", "gn_g1", "gn_g2")]
    assert placement.plan(nodes, edges, {"gn_b", "gn_g1", "gn_g2"}) == placement.Plan("gn_g2", None, None, True)
    # explicit anchor on the bug card: its confirm card is done, so a new branch with its own confirm card
    assert placement.plan(nodes, edges, {"gn_b", "gn_g1", "gn_g2"}, after="gn_b") == \
        placement.Plan("gn_b", None, None, True)


def test_cards_are_inserted_before_an_unstarted_confirm_card():
    from flowgate.chat_control import placement

    nodes = [_n("gn_b", doc_type="B"), _n("gn_t1"), _n("gn_g", "gate")]
    edges = [_e("ge_1", "gn_b", "gn_t1"), _e("ge_2", "gn_t1", "gn_g", {"when": "x"})]
    plan = placement.plan(nodes, edges, {"gn_b"})
    assert plan == placement.Plan("gn_t1", "gn_g", "ge_2", False)
    body = placement.draft_body({"nodes": nodes, "edges": edges}, 3, plan,
                                [{"doc_type": "T", "label": "a", "palette_item_id": "task"},
                                 {"doc_type": "TS", "label": "b", "palette_item_id": "test"}], "확인")
    assert body["draft_version"] == 3
    assert [(e["source"], e["target"]) for e in body["edges"]] == [
        ("gn_b", "gn_t1"), ("gn_t1", "tmp_cc_n0"), ("tmp_cc_n0", "tmp_cc_n1"), ("tmp_cc_n1", "gn_g")]
    assert body["edges"][1]["condition"] == {"when": "x"}          # the replaced edge's condition stays on the anchor
    assert not any(n["client_key"] == "tmp_cc_gate" for n in body["nodes"])


def test_gate_false_adds_no_confirm_card():
    from flowgate.chat_control import placement

    assert placement.plan([_n("gn_b", doc_type="B")], [], set(), want_gate=False) == \
        placement.Plan("gn_b", None, None, False)


@pytest.mark.parametrize("nodes, edges, after, code, params", [
    ([], [], None, "chat_cards_flow_empty", {}),
    ([_n("gn_a"), _n("gn_b")], [], None, "chat_cards_anchor_ambiguous", {"candidates": ["gn_a", "gn_b"]}),
    ([_n("gn_a")], [], "gn_x", "chat_cards_anchor_unknown", {"after": "gn_x"}),
    ([_n("gn_a"), _n("gn_b"), _n("gn_g1", "gate"), _n("gn_g2", "gate")],
     [_e("ge_1", "gn_a", "gn_g1"), _e("ge_2", "gn_b", "gn_g2")], None,
     "chat_cards_anchor_ambiguous", {"candidates": ["gn_a", "gn_b"]}),
])
def test_unplaceable_requests_name_why(nodes, edges, after, code, params):
    from flowgate.chat_control import placement

    with pytest.raises(placement.PlacementError) as caught:
        placement.plan(nodes, edges, set(), after=after)
    assert (caught.value.code, caught.value.params) == (code, params)


@pytest.mark.parametrize("body, field", [
    (None, "body"), ({}, "cards"), ({"cards": []}, "cards"), ({"cards": [{}] * 6}, "cards"),
    ({"cards": [{"doc_type": "N"}]}, "cards[0].doc_type"), ({"cards": [{"label": " "}]}, "cards[0].label"),
    ({"cards": [{"label": "a", "instruction": "x"}]}, "cards[0]"), ({"cards": [{}], "gate": "yes"}, "gate"),
    ({"cards": [{}], "after": ""}, "after"), ({"cards": [{}], "mode": 1}, "mode"),
])
def test_place_request_shape_is_checked_before_anything_is_read(body, field):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    with pytest.raises(GraphApiError) as caught:
        service.parse_place(body, "ko")
    assert caught.value.status == 422 and caught.value.params["errors"][0]["field"] == field


def test_default_label_follows_the_locale():
    from flowgate.chat_control import service

    cards, after, gate = service.parse_place({"cards": [{}, {"doc_type": "TS"}]}, "en")
    assert [(c["label"], c["palette_item_id"]) for c in cards] == [("Task", "task"), ("Test", "test")]
    assert (after, gate) == (None, True)


# ── service on the Graph harness ─────────────────────────────────────────────

def _group():
    from modules.flow_gate.graph_workflow.services import canvas_service
    return canvas_service.load_flow(FA)


def _draft():
    from modules.flow_gate.graph_workflow.store import definition_store
    return definition_store.draft(FA)


@pytest.fixture()
def gate_policy(monkeypatch):
    """Confirming a revision with a confirm card needs the gate policy, registered at startup in production
    (as ``test_fg_run_buttons_0053`` / ``test_fg_card_approval_0056`` register it)."""
    from modules.flow_gate.graph_workflow.domain import policies

    monkeypatch.setitem(policies._registered, "gate_node", object())


def _ai_card(key):
    """The card's AI (its legacy role fields, as ``test_fg_card_settings.legacy_setting`` seeds them): this harness
    turns the new card initialization off (``legacy_cards``), so a placed card would wait for AI setup."""
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    for field in ("instruction_ai", "run_ai"):
        with_flow_guard(FA, lambda field=field: repository.put(FA, key, field, "aip_card", "u_kim"))


def _write_instruction(doc_id, body):
    """The chat's card document edit of the instruction line, reduced to its effect: the line's file has a body."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: T\ntitle: {doc_id}\n---\n{body}", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md"})


def test_a_chat_places_work_and_confirm_cards_on_a_flow_with_only_its_bug_card(env, decided):
    from flowgate.chat_control import service

    put_draft(env, FA, 0, [node("tmp_r", "R")])
    root = next(n["node_key"] for n in _draft()["nodes"])
    placed = service.place(_group(), "u_kim", {"cards": [{"label": "버그 수정"}]}, "ko")
    assert placed["after"] == root and placed["draft_version"] == 2
    assert placed["gate"]["created"] is True
    [card] = placed["cards"]
    assert card["label"] == "버그 수정" and card["doc_type"] == "T"
    assert card["instruction"]["slot"] == "instruction" and card["instruction"]["doc_id"]
    draft = _draft()
    kinds = {n["node_key"]: (n["node_type"], n.get("doc_type"), n.get("label")) for n in draft["nodes"]}
    assert kinds[card["node_key"]] == ("document", "T", "버그 수정")
    assert kinds[placed["gate"]["node_key"]] == ("gate", None, "확인")
    assert sorted((e["source"], e["target"]) for e in draft["edges"]) == sorted(
        [(root, card["node_key"]), (card["node_key"], placed["gate"]["node_key"])])

    # a second change before the run goes between the first work card and the unstarted confirm card
    again = service.place(_group(), "u_kim", {"cards": [{"label": "후속"}]}, "ko")
    assert again["after"] == card["node_key"]
    assert again["gate"] == {"node_key": placed["gate"]["node_key"], "created": False}
    second = again["cards"][0]["node_key"]
    assert sorted((e["source"], e["target"]) for e in _draft()["edges"]) == sorted(
        [(root, card["node_key"]), (card["node_key"], second), (second, placed["gate"]["node_key"])])


def test_a_start_refuses_empty_instructions_then_confirms_and_runs(env, decided, gate_policy):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store, run_store

    put_draft(env, FA, 0, [node("tmp_r", "R")])
    placed = service.place(_group(), "u_kim", {"cards": [{"label": "버그 수정"}]}, "ko")
    card = placed["cards"][0]
    with pytest.raises(GraphApiError) as caught:
        service.start(_group(), "u_kim", None)
    assert caught.value.status == 409 and caught.value.code == "chat_run_instruction_missing"
    assert caught.value.params["node_keys"] == [card["node_key"]]
    assert definition_store.latest_revision_no(FA) is None and run_store.current_run(FA) is None

    _ai_card(card["node_key"])
    _write_instruction(card["instruction"]["doc_id"], "## 지시\n\n사용자 원문: 버그 고쳐\n")
    started = service.start(_group(), "u_kim", None)
    assert started["confirmed_revision_no"] == 1 and _draft() is None
    assert started["run"]["run_id"] and started["run"]["revision_no"] == 1
    assert run_store.current_run(FA)["run_id"] == started["run"]["run_id"]
    assert "결과 봐" in started["next"]
    # the chat's written instruction is approved without the instruction AI: the card's first AI job is its result stage
    assert [j.get("stage") for j in env["jobs"] if j["node_key"] == card["node_key"]] == ["result"], env["jobs"]


def test_allow_instruction_ai_starts_with_an_empty_instruction(env, decided, gate_policy):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.store import run_store

    put_draft(env, FA, 0, [node("tmp_r", "R")])
    card = service.place(_group(), "u_kim", {"cards": [{}]}, "ko")["cards"][0]["node_key"]
    _ai_card(card)
    started = service.start(_group(), "u_kim", {"allow_instruction_ai": True})
    assert started["confirmed_revision_no"] == 1 and run_store.current_run(FA) is not None
    # an empty instruction is written by the card's instruction AI as before
    assert [j.get("stage") for j in env["jobs"] if j["node_key"] == card] == ["instruction"], env["jobs"]


def test_placement_after_an_unknown_card_is_404_and_writes_nothing(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    put_draft(env, FA, 0, [node("tmp_r", "R")])
    with pytest.raises(GraphApiError) as caught:
        service.place(_group(), "u_kim", {"cards": [{}], "after": "gn_missing"}, "ko")
    assert caught.value.status == 404 and caught.value.code == "chat_cards_anchor_unknown"
    assert _draft()["draft_version"] == 1


# ── authority ────────────────────────────────────────────────────────────────

class _Scope:
    user_id, project_id = "u_kim", "flowgate"


@pytest.fixture()
def granted(monkeypatch):
    from flowgate.card_document_edit import chat_authz
    from flowgate.chat_control import authz
    from flowgate.graph_read import authz as graph_authz

    state = {"kind": "read_write", "claim": False, "perm": True, "calls": []}
    monkeypatch.setattr(graph_authz, "authorize",
                        lambda token, flow_id, run_hint=None: state["calls"].append(flow_id) or (_Scope(), {"group_id": flow_id}))
    monkeypatch.setattr(chat_authz, "effective_write_kind", lambda token: state["kind"])
    monkeypatch.setattr(chat_authz, "claim_pending", lambda token: state["claim"])
    monkeypatch.setattr(authz, "has_permission", lambda user, project, perm: state["perm"] and perm == "perm_document_update")
    return state


def test_an_edit_mode_chat_with_write_permission_is_allowed(granted):
    from flowgate.chat_control import authz

    scope, group = authz.authorize({"action_scope": "chat"}, FA)
    assert scope.user_id == "u_kim" and group == {"group_id": FA} and granted["calls"] == [FA]


@pytest.mark.parametrize("token, change, status, code", [
    ({"action_scope": "edit"}, {}, 403, "token_kind_not_allowed"),
    ({"action_scope": "chat"}, {"kind": "read"}, 403, "chat_edit_mode_required"),
    ({"action_scope": "chat"}, {"claim": True}, 403, "token_binding_mismatch"),
    ({"action_scope": "chat"}, {"perm": False}, 403, "flow_access_denied"),
])
def test_refusals(granted, token, change, status, code):
    from flowgate.chat_control import authz
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    granted.update(change)
    with pytest.raises(GraphApiError) as caught:
        authz.authorize(token, FA)
    assert (caught.value.status, caught.value.code) == (status, code)


# ── tools, mention, routes ───────────────────────────────────────────────────

def test_tools_are_offered_to_chat_runs_with_a_cli_mention():
    from flowgate.chat_control import tools
    from flowgate.conversation import integration

    names = [t["name"] for t in integration.provide_chat_tools({"action_scope": "chat", "chat_command_capability": True})]
    assert names[:4] == ["read_flow_canvas", "read_flow_current_run", "place_flow_cards", "start_flow_run"]
    text = tools.cli_mention("http://h/flowgate/api/v1", "TOKEN")
    for word in ("POST http://h/flowgate/api/v2/chat/flows/{flow_id}/cards",
                 "POST http://h/flowgate/api/v2/chat/flows/{flow_id}/runs", "Bearer TOKEN",
                 "chat_edit_mode_required", "chat_run_instruction_missing", "slots/instruction/document/revisions"):
        assert word in text
    assert all(callable(t["handler"]) for t in tools.descriptors())


def test_api_tool_answers_refusals_in_the_graph_envelope(monkeypatch):
    from flowgate.chat_control import authz, tools
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    def refuse(run, flow_id):
        raise GraphApiError(403, "chat_edit_mode_required", {})

    monkeypatch.setattr(authz, "from_run", refuse)
    status, body = tools.place_call({"group_id": FA}, {"cards": [{}]})
    assert status == 403 and body["ok"] is False and body["error"]["code"] == "chat_edit_mode_required"
    status, body = tools.start_call({"group_id": FA}, {})
    assert status == 403 and body["error"]["code"] == "chat_edit_mode_required"


def test_api_tool_passes_the_flow_and_body_to_the_service(monkeypatch):
    from flowgate.chat_control import authz, service, tools

    seen = {}
    monkeypatch.setattr(authz, "from_run", lambda run, flow_id: seen.setdefault("flow", flow_id) and (_Scope(), {"group_id": flow_id}))
    monkeypatch.setattr(service, "place", lambda group, user, body, locale: {"placed": body, "group": group["group_id"]})
    monkeypatch.setattr(service, "start", lambda group, user, body: {"started": body})
    status, body = tools.place_call({"group_id": FA}, {"cards": [{"label": "x"}]})
    assert status == 200 and body["placed"] == {"cards": [{"label": "x"}]} and seen["flow"] == FA
    status, body = tools.start_call({"group_id": FA}, {"flow_id": FA, "allow_instruction_ai": True})
    assert status == 200 and body["started"] == {"allow_instruction_ai": True}


def test_cli_routes_authenticate_before_the_body():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flowgate.chat_control import routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    for path in ("cards", "runs"):
        res = client.post(f"/api/v2/chat/flows/{FA}/{path}", content=b"not json")
        assert res.status_code == 401 and res.json()["error"]["code"] == "unauthorized"
