"""Help tree contracts: reachability, visibility, budgets and legacy assembly."""
import json
import pytest
from modules.flow_gate.services import help_catalog
from modules.flow_gate.services.help import render, tree, visibility
from flowgate.chat_control import tools


@pytest.fixture(autouse=True)
def suppliers(monkeypatch):
    from modules.flow_gate import template_provision
    from modules.flow_gate.db import documents, templates
    from modules.flow_gate.services import question_policy
    monkeypatch.setattr(template_provision, "has_body_template", lambda code: False)
    monkeypatch.setattr(templates, "list_document_types", lambda **kwargs: [])
    monkeypatch.setattr(documents, "get_documents_by_group_id", lambda *args: [])
    monkeypatch.setattr(documents, "get_by_id", lambda *args: None)
    monkeypatch.setattr(question_policy, "text", lambda *args, **kwargs: "")


def context(locale="ko", scope="chat", kind="read_write", doc_type="TR"):
    return {"principal_kind": "worker", "project": "flowgate", "group_id": "flowgate.v02test.0061",
            "doc_id": "flowgate.v02test.0061.0008-TR", "doc_type": doc_type,
            "action_scope": scope, "tool_kind": kind, "source_mode": "remote", "reason": None,
            "locale": locale, "base_url": "/flowgate/api/v1", "registry": {"tools": [], "kind": kind, "notes": []},
            "token_rec": {}, "continuous": False}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("scope", ["chat", "edit", "review"])
def test_index_and_area_budgets(locale, scope):
    ctx = context(locale, scope)
    index = render.build_index(ctx)
    assert size(index) <= 1200
    summaries = [row["summary"] for row in index["items"]]
    assert len(summaries) == len(set(summaries))
    for row in index["items"]:
        assert set(row) == {"name", "summary", "children_count"}
        children = render.enumerate_children(row["name"], ctx)
        assert size(children) <= 1000
        assert len({child["summary"] for child in children}) == len(children)
        for child in children:
            if row["name"] in tree.AREA_LEAVES:
                assert visibility.decide_visibility(row["name"] + "/" + child["name"], ctx).visible


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("scope", ["chat", "edit"])
def test_leaf_budgets(locale, scope):
    ctx = context(locale, scope)
    failures = []
    for area, leaves in tree.AREA_LEAVES.items():
        for leaf in leaves:
            if not visibility.decide_visibility(area + "/" + leaf, ctx).visible:
                continue
            payload = render.build_child(area, leaf, ctx)
            if size(payload) > 2200:
                failures.append((area + "/" + leaf, size(payload)))
    assert not failures, failures


def test_old_flat_names_are_gone_and_bulk_visibility():
    ctx = context()
    removed = set(tree.LEGACY_ORDER) - set(tree.CATALOG_ORDER)
    assert {"chat_control", "chat_turn", "document_access", "document_attachments", "chat_documents",
            "chat_recovery", "chat_questions", "group_documents", "doc_type", "changed_files_format",
            "test_commands", "tr_self_check"} <= removed
    for name in sorted(removed):
        assert visibility.decide_visibility(name, ctx).reason == "unknown_item", name
        with pytest.raises(tree.HelpSupplierError):
            render.build_item(name, ctx)
    # the console user session keeps the flat index it always had
    session = dict(ctx, principal_kind="user_session")
    assert visibility.decide_visibility("document_access", session).visible
    assert visibility.decide_visibility("doc_type", session).visible
    assert tuple(tree.CATALOG_ORDER) == tuple(tree.AREA_ORDER)
    items, unavailable = render.build_bulk(["documents/partial", "cards/roles", "documents/missing"], ctx)
    assert len(items) == 2
    assert unavailable[0]["http_status"] == 404
    ctx["action_scope"] = "review"
    assert not visibility.decide_visibility("cards", ctx).visible
    assert not visibility.decide_visibility("cards/roles", ctx).visible
    assert render.build_bulk(["cards/roles"], ctx)[1][0]["http_status"] == 403


def test_card_contract_assembly():
    assert tools.cli_mention("/flowgate/api/v1", "test") == "\n".join(
        getattr(tools, "contract_" + topic)("/flowgate/api/v1", "test")
        for topic in ("planning", "ai_options", "place", "run", "roles"))
    assert "POST /flowgate/api/v2/chat/flows/{flow_id}/cards" in tools.contract_place("/flowgate/api/v1", "test")


def _original_card_contract(api_base, raw_token):
    base = str(api_base or "").rstrip("/")
    base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
    return "\n".join([
        "## Card run (control tower, stage 1)",
        "---",
        tools.GUIDE,
        "",
        f"GET {base}/api/v2/chat/flows/{{flow_id}}/ai-options",
        f"Authorization: Bearer {raw_token}",
        "The AIs you may choose for your cards (`options`: id, name, kind) and the AI groups with role profiles",
        "(`role_groups`, for from_group only when the user names one). Read it before placing cards.",
        "",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/cards",
        f"Authorization: Bearer {raw_token}",
        '{"cards": [{"label": "<card title>", "doc_type": "T", "roles": {"instruction": {"kind": "not_used"},',
        '  "execution": {"kind": "assigned", "provider_id": "<options id>"}, "review": {"kind": "assigned",',
        '  "provider_id": "<another options id>"}}, "settings": {"review_count": 1}}],',
        ' "after": "<node_key, optional>", "gate": true}',
        "`roles` per card is yours to choose (omitted roles take the defaults; `defaults_used` in the answer lists",
        "them). Another user's or a disabled AI: 422 ai_not_selectable and nothing is placed.",
        "`settings` per card: review_count (0 none, -1 until it passes, 1..max), restart and time_limit (minutes;",
        "T only), verify_max_runs (verifications, 1 or more) and verify_run_minutes (1..30; both T only, null = the",
        "default 3 / 2 minutes). Decide the review count yourself; a value the card cannot take is 422 with its `choices`.",
        tools.TOPOLOGY_GUIDE,
        "Without links: places up to 5 work cards (T, or TS) in one chain and ends it in a confirm card",
        "(the anchor's unstarted confirm card, else a new one). Without `after` the server picks the only",
        "work card -> unstarted confirm card edge, else the only tail; otherwise 409 chat_cards_anchor_ambiguous",
        "lists candidates. A Flow with no card yet starts the chain with a start card of its bug/requirement",
        "document (answer `root`). The answer gives each card's node_key and its instruction line: write the",
        "instruction with the card document edit (POST .../nodes/{node_key}/slots/instruction/document/revisions;",
        "content without front matter keeps the document's current front matter).",
        "",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/runs",
        f"Authorization: Bearer {raw_token}",
        '{"allow_instruction_ai": false}',
        "Confirms the draft and starts the run in full mode (an idle/paused run goes on). Empty instruction lines",
        "are refused (409 chat_run_instruction_missing) unless allow_instruction_ai is true. `waiting` lists cards",
        "that already wait and why (manual_write_wait = no AI on that card role); runs/current node_runs carry the",
        "same wait_reason / intervention later.",
        "",
        f"GET {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/roles",
        f"Authorization: Bearer {raw_token}",
        "The instruction / execution / review AI of a work card this chat placed (any mode): each role's state and",
        "selection, settings_version and context (send them back with a change), locked, and `options` (the AIs",
        "you may pick: id, name, kind). Other cards: 403 chat_card_not_owned (lists the node_keys this chat placed).",
        "`settings`: the card's review_count / restart / time_limit / verify_max_runs / verify_run_minutes, each with value, default and choices.",
        "",
        f"PATCH {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/roles",
        f"Authorization: Bearer {raw_token}",
        '{"roles": {"execution": {"kind": "assigned", "provider_id": "<options id>"}, "review": {"kind": "not_used"}},',
        ' "expected_settings_version": <from GET>, "expected_context": <context from GET, optional>}',
        "Edit mode only, and only cards this chat placed. Changes only those roles, all or nothing (never the card's",
        "other settings or your account defaults). 422 role_not_applicable / ai_not_selectable, 409",
        "node_settings_locked (the card runs) / gate_context_changed (stale: GET again). Only a GET after it that",
        "shows the new selection counts as confirmed; on a refusal report its code and message to the user.",
        '{"from_group": "<role_groups ai_group_id>", "expected_settings_version": <from GET>} applies that AI',
        "group's role profile to the card: only when the user names the group (roles sent with it win).",
        '{"settings": {"review_count": 2}, "expected_settings_version": <from GET>} changes the run settings',
        "(review_count, restart, time_limit, verify_max_runs, verify_run_minutes; a value from its `choices`, null = default; with or without roles, one",
        "save). 422 setting_not_applicable (a TS card has only review_count) / not_a_choice.",
        "",
        "flow_id is this chat's group id. Edit mode only: reading mode answers 403 chat_edit_mode_required.",
        "You are not woken when the run stops: report what you started and ask the user to message you when it",
        "is done, then read runs/current and the canvas. Neither call consumes this token.",
    ])

def test_card_contract_is_unchanged():
    assert tools.cli_mention("/flowgate/api/v1", "test") == _original_card_contract("/flowgate/api/v1", "test")


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("scope", ["chat", "edit"])
def test_http_api_and_bulk_leaf_paths(monkeypatch, locale, scope):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import help_routes
    from modules.flow_gate.services import api_server_tools
    ctx = context(locale, scope)
    monkeypatch.setattr(help_routes, "verify_bearer", lambda request: {})
    monkeypatch.setattr(help_catalog, "resolve_context", lambda *args: ctx)
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda token: {})
    app = FastAPI()
    app.include_router(help_routes.router)
    client = TestClient(app, headers={"Authorization": "Bearer test"})
    response = client.get("/api/v1/help")
    assert size(response.json()) <= 1200
    print("HELP_SIZE", locale, scope, "index", size(response.json()))
    for area in tree.AREA_LEAVES:
        if not visibility.decide_visibility(area, ctx).visible:
            continue
        area_size = size(client.get("/api/v1/help/items/" + area).json())
        assert area_size <= 1100
        print("HELP_SIZE", locale, scope, area, area_size)
        for leaf in render.enumerate_children(area, ctx):
            path = area + "/" + leaf["name"]
            http = client.get("/api/v1/help/items/" + path)
            status, api = api_server_tools.read_help({"api_base_url": ctx["base_url"]}, "test", {"item": path})
            assert http.status_code == status == 200
            assert http.json() == api
            bulk = client.get("/api/v1/help", params={"items": path}).json()
            assert bulk["items"][0]["content"] == api["content"]
