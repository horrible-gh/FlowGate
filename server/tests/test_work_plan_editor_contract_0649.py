"""What the card editor needs from the server — flowgate.default.0649 T#3 (NR0003 §5.3, O7, §11).

The work-plan editor moves whole cards, renumbers keys by order and keeps every card's
``card_id``; a card added in the editor is sent without an id. These tests pin the server
half of that contract and the end-to-end order:

  * save — a new card the editor moved ahead of an existing card of the same type is
    renumbered onto that card's old key. As long as every previous card of the type is still
    sent with its id, the step is a new card and gets a server id (not ``card_id_missing``);
    a body that really lost an id on a reused key is still refused.
  * O7 read view — ``step_execution_status[]`` names each row's ``card_id`` and whether
    the card already started in the workflow (by card identity, not by key).
  * end to end — a mixed-order plan saved the way the editor saves it pours into the
    sequence in card order (item_seq / sort_order / head), and a legacy fixed-order plan
    without stored ids keeps working the same way.
"""
from __future__ import annotations

import copy

import pytest

from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents.routers import work_plan as wp_router
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_service as wp
from tests.test_work_plan_card_order_0649 import (  # noqa: F401 — env is a fixture
    DOC, Env, _body, _candidates, _monotonic, _rows, _save_candidates, env,
)
from tests.test_workflow_protected_rows_0649 import _ROOT, _WP, _items, _seed_rows


def _codes(exc) -> list[str]:
    return [error["code"] for error in exc.value.errors]


def _editor_renumber(steps: list[dict]) -> list[dict]:
    """Renumber keys/ordinals by order the way WorkPlanEditor.renumberSteps() does."""
    out, seen = [], {}
    index = 0
    while index < len(steps):
        step = copy.deepcopy(steps[index])
        if step["pair_role"] == "result":
            raise AssertionError("a result step never leads a card")
        code = step["type"]
        seen[code] = seen.get(code, 0) + 1
        n = seen[code]
        step.update(key=wp.make_key(code, n), ordinal=n)
        if step["pair_role"] == "instruction":
            result = copy.deepcopy(steps[index + 1])
            result.update(key=wp.make_key(result["type"], n), ordinal=n, pair_key=step["key"])
            step["pair_key"] = result["key"]
            out += [step, result]
            index += 2
        else:
            out.append(step)
            index += 1
    return out


def _new_card(code: str) -> list[dict]:
    """Steps of a card the editor just added: no card_id, keys fixed up by the renumber."""
    if code in wp.WORK_PLAN_PAIR_MAP:
        result = wp.WORK_PLAN_PAIR_MAP[code]
        steps = [wp.make_step(code, 99, wp.make_key(result, 99), "instruction"),
                 wp.make_step(result, 99, wp.make_key(code, 99), "result")]
    else:
        steps = [wp.make_step(code, 99, None, "single")]
    for step in steps:
        step.pop("card_id", None)
    return steps


# ── save contract: a renumbered new card ───────────────────────────────────────

def test_a_new_card_moved_ahead_of_an_existing_same_type_card_gets_a_server_id():
    previous = wp.validate(_body([("T", "c_a")], values=False))
    sent = copy.deepcopy(previous)
    sent["quantities"]["T"]["count"] = 2
    # editor: [+T] then drag the new card in front of c_a → new card is T#1, c_a is T#2.
    sent["steps"] = _editor_renumber(_new_card("T") + previous["steps"])
    assert [(s["key"], s.get("card_id")) for s in sent["steps"]] == [
        ("T#1", None), ("TR#1", None), ("T#2", "c_a"), ("TR#2", "c_a"),
    ]

    assigned = wp.assign_card_ids(sent, previous)
    ids = [(s["key"], s["card_id"]) for s in assigned["steps"]]
    new_id = ids[0][1]
    assert new_id.startswith("c_") and new_id != "c_a"
    assert ids == [("T#1", new_id), ("TR#1", new_id), ("T#2", "c_a"), ("TR#2", "c_a")]
    assert wp.validate(assigned)["steps"] == assigned["steps"]


def test_a_reused_key_whose_previous_card_is_not_sent_is_still_a_lost_id():
    previous = wp.validate(_body([("T", "c_a"), ("T", "c_b")], values=False))
    sent = copy.deepcopy(previous)
    # c_b was moved in front and its id dropped — c_b is nowhere else in the body.
    sent["steps"] = _editor_renumber(previous["steps"][2:] + previous["steps"][:2])
    sent["steps"][0].pop("card_id")
    sent["steps"][1].pop("card_id")
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert set(_codes(exc)) == {"card_id_missing"}


def test_another_type_card_does_not_vouch_for_a_lost_id():
    previous = wp.validate(_body([("D", "c_d"), ("T", "c_a")], values=False))
    sent = copy.deepcopy(previous)
    sent["steps"][1].pop("card_id")  # T#1 lost its id; D's id is irrelevant to it
    sent["steps"][2].pop("card_id")
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert set(_codes(exc)) == {"card_id_missing"}


# ── O7: step_execution_status carries card_id / started ───────────────────────

def _read_view(env: Env, monkeypatch, plan: dict) -> dict:
    monkeypatch.setattr(wp_router, "_providers", lambda project_id: [])
    monkeypatch.setattr(wp_router, "_plan_path", lambda doc: env.plan_file)
    monkeypatch.setattr(wp_router.document_service, "is_final_approved", lambda doc: False)
    monkeypatch.setattr(wp_router.document_service, "is_document_editable",
                        lambda doc, final_approved=False: True)
    return wp_router._read_view(env.doc(), plan)


def test_read_view_marks_started_cards_by_identity(env, monkeypatch):
    env.plan(0, _body([("T", "c_a"), ("D", "c_d"), ("T", "c_b")]))
    _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, None),
        ("D", "c_d", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    # rev1: the editor moved D behind B. Keys of c_b change (still T#2), started stays on c_a.
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b"), ("D", "c_d")]))

    assert wpa.started_card_ids(env.doc(), plan) == ["c_a"]
    status = _read_view(env, monkeypatch, plan)["step_execution_status"]
    assert [(s["step_key"], s["card_id"], s["started"]) for s in status] == [
        ("T#1", "c_a", True), ("TR#1", "c_a", True),
        ("T#2", "c_b", False), ("TR#2", "c_b", False),
        ("D#1", "c_d", False),
    ]


def test_read_view_without_a_poured_sequence_has_no_started_card(env, monkeypatch):
    plan = env.plan(0, _body([("T", "c_a"), ("D", "c_d")]))
    assert wpa.started_card_ids(env.doc(), plan) == []
    status = _read_view(env, monkeypatch, plan)["step_execution_status"]
    assert all(entry["started"] is False for entry in status)
    assert [entry["card_id"] for entry in status] == ["c_a", "c_a", "c_d"]


def test_read_view_survives_a_failing_started_lookup(env, monkeypatch):
    plan = env.plan(0, _body([("D", "c_d")]))

    def _boom(doc, body):
        raise RuntimeError("sequence store down")

    monkeypatch.setattr(wpa, "started_card_ids", _boom)
    status = _read_view(env, monkeypatch, plan)["step_execution_status"]
    assert status == [dict(status[0], card_id="c_d", started=False)]


# ── end to end: editor save → pour → execution order ───────────────────────────

def _editor_save(previous: dict, steps: list[dict], quantities: dict) -> dict:
    """What PUT /work-plan does with the editor's body: card-id contract, then validate."""
    sent = copy.deepcopy(previous)
    sent["quantities"] = {**sent["quantities"], **quantities}
    sent["counted_types"] = list(dict.fromkeys(list(sent["counted_types"]) + list(quantities)))
    sent["steps"] = _editor_renumber(steps)
    # provider scope is the PUT route's business (candidates); here only the card contract
    return wp.validate(wp.assign_card_ids(sent, previous), enforce_provider_scope=False)


def test_mixed_order_saved_by_the_editor_pours_and_runs_in_card_order(env):
    rev0 = env.plan(0, _body([("N", "c_n"), ("T", "c_a"), ("T", "c_b")]))
    by_card = {}
    for step in rev0["steps"]:
        by_card.setdefault(step["card_id"], []).append(step)
    # The R0001 example shape: T → N → T2 → T, with a brand-new T2 card in the middle.
    saved = _editor_save(
        rev0,
        by_card["c_a"] + by_card["c_n"] + _new_card("T2") + by_card["c_b"],
        {"T2": {"unit": "set", "count": 1}},
    )
    assert [s["key"] for s in saved["steps"]] == [
        "T#1", "TR#1", "N#1", "NR#1", "T2#1", "TR2#1", "T#2", "TR#2",
    ]
    new_t2 = saved["steps"][4]["card_id"]
    assert new_t2.startswith("c_")
    plan = env.plan(1, saved)

    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    _save_candidates(env, candidate, "replace_after")

    sequence = db_wfseq.get_sequence_by_doc_id(_ROOT)
    rows = _items(sequence["id"])
    assert [(r["type"], r["source_wp_card_id"]) for r in rows] == [
        ("T", "c_a"), ("TR", "c_a"), ("N", "c_n"), ("NR", "c_n"),
        ("T2", new_t2), ("TR2", new_t2), ("T", "c_b"), ("TR", "c_b"),
    ]
    _monotonic(sequence["id"])
    assert db_wfseq.get_effective_head(sequence["id"])["source_wp_card_id"] == "c_a"
    # each card's settings stayed with its card, whatever key it carries now
    assert [r["note"] for r in rows if r["type"] == "T"] == ["note c_a", "note c_b"]


def test_editor_reorder_behind_a_started_card_reflects_in_card_order(env):
    rev0 = env.plan(0, _body([("T", "c_a"), ("T", "c_b"), ("D", "c_d")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"]),
        ("T", "c_b", 0, None), ("TR", "c_b", 0, None), ("D", "c_d", 0, None),
    ]))
    started = [(r["id"], r["item_seq"], r["sort_order"]) for r in _items(seq_id)[:2]]
    by_card = {}
    for step in rev0["steps"]:
        by_card.setdefault(step["card_id"], []).append(step)
    # The editor keeps the started card c_a first and moves D in front of B.
    plan = env.plan(1, _editor_save(rev0, by_card["c_a"] + by_card["c_d"] + by_card["c_b"], {}))

    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    assert candidate["started_card_ids"] == ["c_a"]
    _save_candidates(env, candidate, "replace_after")

    rows = _items(seq_id)
    assert [(r["type"], r["source_wp_card_id"]) for r in rows] == [
        ("T", "c_a"), ("TR", "c_a"), ("D", "c_d"), ("T", "c_b"), ("TR", "c_b"),
    ]
    assert [(r["id"], r["item_seq"], r["sort_order"]) for r in rows[:2]] == started
    _monotonic(seq_id)
    assert db_wfseq.get_effective_head(seq_id)["source_wp_card_id"] == "c_d"


def test_legacy_fixed_order_plan_without_card_ids_still_pours_in_its_order(env):
    legacy = _body([("D", "D#1"), ("T", "T#1"), ("T", "T#2")])
    for step in legacy["steps"]:
        step.pop("card_id")
    plan = env.plan(0, legacy)
    assert [s["card_id"] for s in plan["steps"]] == ["D#1", "T#1", "T#1", "T#2", "T#2"]

    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    _save_candidates(env, candidate, "replace_after")

    sequence = db_wfseq.get_sequence_by_doc_id(_ROOT)
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(sequence["id"])] == [
        ("D", "D#1"), ("T", "T#1"), ("TR", "T#1"), ("T", "T#2"), ("TR", "T#2"),
    ]
    _monotonic(sequence["id"])


# ── over HTTP: the payloads the new client builds ──────────────────────────────

def _http(env: Env, monkeypatch):
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import workflow_decision_routes as routes
    from modules.flow_gate.auth.middleware import get_current_user
    from routers.main import app

    monkeypatch.setattr(routes, "verify_bearer", lambda request: {"_is_user_jwt": True, "issued_to": "usr"})
    monkeypatch.setattr(routes, "_active_ai_run_response_for_user", lambda d, a: None)
    monkeypatch.setattr(wp_router, "_providers", lambda project_id: [])
    monkeypatch.setattr(wp_router, "_plan_path", lambda doc: env.plan_file)
    # the WP row the seed made has no owner link; the editor's GET and the pour read it
    env.store._execute("UPDATE documents SET target_id = ? WHERE doc_id = ?", [_ROOT, _WP])
    monkeypatch.setitem(app.dependency_overrides, get_current_user,
                        lambda: {"user_id": "usr", "is_admin": True})
    return TestClient(app, raise_server_exceptions=False)


def _client_patch_items(rows: list[dict]) -> list[dict]:
    """WorkflowDecisionModal.payloadItems(): protected pending rows echo by id, the editable
    rows carry their values, item_id (stored rows) and source_wp_card_id."""
    fields = ("type", "label", "note", "source_doc_id", "source_revision_no", "provider_id",
              "provider_display_name", "review_count", "reviewer_provider_id",
              "reviewer_provider_display_name", "pre_instruction_text", "pre_instruction_attachment")
    echoes = [{"type": r["type"], "label": r["label"], "item_id": r["item_id"]}
              for r in rows if r.get("protected") and r.get("status") == "pending" and r.get("item_id")]
    editable = []
    for r in rows:
        if r.get("protected") or r.get("locked"):
            continue
        item = {k: r.get(k) for k in fields}
        item["review_count"] = item["review_count"] or 0
        if r.get("item_id") is not None:
            item["item_id"] = r["item_id"]
        if r.get("source_wp_card_id"):
            item["source_wp_card_id"] = r["source_wp_card_id"]
        editable.append(item)
    return echoes + editable


def test_http_editor_to_pour_to_execution_follows_the_card_order(env, monkeypatch):
    rev0 = env.plan(0, _body([("T", "c_a"), ("D", "c_d"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, None),
        ("D", "c_d", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    protected_before = [(r["id"], r["item_seq"], r["sort_order"]) for r in _items(seq_id)[:2]]
    client = _http(env, monkeypatch)

    # 1. the editor reads the plan: c_a is started (O7), the others are free
    view = client.get(f"/flowgate/api/v1/documents/{_WP}/work-plan")
    assert view.status_code == 200, view.text
    assert {(s["card_id"], s["started"]) for s in view.json()["step_execution_status"]} == {
        ("c_a", True), ("c_d", False), ("c_b", False),
    }

    # 2. the editor saves: B moved in front of D and a new T card added in front of D too
    #    (renumbered onto T#3, no id; the server names it), through the PUT card contract.
    by_card = {}
    for step in view.json()["body"]["steps"]:
        by_card.setdefault(step["card_id"], []).append(step)
    saved = _editor_save(rev0, by_card["c_a"] + by_card["c_b"] + _new_card("T") + by_card["c_d"],
                         {"T": {"unit": "set", "count": 3}})
    new_id = saved["steps"][4]["card_id"]
    plan = env.plan(1, saved)
    assert [(s["key"], s["card_id"]) for s in plan["steps"]] == [
        ("T#1", "c_a"), ("TR#1", "c_a"), ("T#2", "c_b"), ("TR#2", "c_b"),
        ("T#3", new_id), ("TR#3", new_id), ("D#1", "c_d"),
    ]

    # 3. [Apply Work Plan] -> replace_after candidates: the started card is not poured again
    cand = client.post(f"/flowgate/api/v1/documents/{_WP}/work-plan/sequence-candidates",
                       json={"mode": "replace_after"})
    assert cand.status_code == 200, cand.text
    data = cand.json()
    assert data["blockers"] == []
    assert "steps_already_done" in [n["code"] for n in data["notifications"]]

    # 4. the modal saves exactly what the new client sends
    resp = client.patch("/flowgate/api/v1/workflow/sequence", json={
        "doc_id": _ROOT,
        "items": _client_patch_items(data["rows"]),
        "expected_workflow_tag": data["workflow_tag"],
        "expected_plan": {"wp_doc_id": _WP, "wp_revision_no": 1, "mode": "replace_after"},
    })
    assert resp.status_code == 200, resp.text

    # 5. execution order = card order; protected rows untouched; the head is the started
    #    card's pending report, then the cards in plan order
    rows = _items(seq_id)
    assert [(r["type"], r["source_wp_card_id"]) for r in rows] == [
        ("T", "c_a"), ("TR", "c_a"), ("T", "c_b"), ("TR", "c_b"),
        ("T", new_id), ("TR", new_id), ("D", "c_d"),
    ]
    assert [(r["id"], r["item_seq"], r["sort_order"]) for r in rows[:2]] == protected_before
    _monotonic(seq_id)
    head = db_wfseq.get_effective_head(seq_id)
    assert (head["type"], head["source_wp_card_id"]) == ("TR", "c_a")
    pending = [r for r in rows if r["result_doc_id"] is None]
    assert [r["source_wp_card_id"] for r in pending] == ["c_a", "c_b", "c_b", new_id, new_id, "c_d"]


def test_http_append_with_this_plans_rows_pending_is_refused_for_the_client(env, monkeypatch):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = [dict(r) for r in _items(seq_id)]
    env.plan(1, _body([("T", "c_b"), ("T", "c_a")]))
    client = _http(env, monkeypatch)

    cand = client.post(f"/flowgate/api/v1/documents/{_WP}/work-plan/sequence-candidates",
                       json={"mode": "append"}).json()
    assert cand["blockers"] == ["plan_rows_pending"]
    note = next(n for n in cand["notifications"] if n["code"] == "plan_rows_pending")
    assert note["severity"] == "blocker" and note["suggested_mode"] == "replace_after"

    # a client that ignored the blocker is refused by the server, nothing written
    resp = client.patch("/flowgate/api/v1/workflow/sequence", json={
        "doc_id": _ROOT, "items": _client_patch_items(cand["rows"]),
        "expected_workflow_tag": cand["workflow_tag"],
        "expected_plan": {"wp_doc_id": _WP, "wp_revision_no": 1, "mode": "append"},
    })
    assert resp.status_code == 409 and resp.json()["error"] == "plan_rows_pending"
    assert [dict(r) for r in _items(seq_id)] == before

    # the replace_after the client points to reorders the pending cards to the plan
    cand = client.post(f"/flowgate/api/v1/documents/{_WP}/work-plan/sequence-candidates",
                       json={"mode": "replace_after"}).json()
    assert cand["blockers"] == []
    resp = client.patch("/flowgate/api/v1/workflow/sequence", json={
        "doc_id": _ROOT, "items": _client_patch_items(cand["rows"]),
        "expected_workflow_tag": cand["workflow_tag"],
        "expected_plan": {"wp_doc_id": _WP, "wp_revision_no": 1, "mode": "replace_after"},
    })
    assert resp.status_code == 200, resp.text
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", "c_b"), ("TR", "c_b"), ("T", "c_a"), ("TR", "c_a"),
    ]
