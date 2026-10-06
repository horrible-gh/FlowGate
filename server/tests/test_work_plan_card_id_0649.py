"""Work-plan card identity and mixed order — flowgate.default.0649 T#1 (NR0003 §5.1).

The plan body is now "cards in array order": a set card is its instruction immediately
followed by its result, ordinals inside one type run 1..n in order, and every step carries a
``card_id`` that survives renumbering. These tests pin

  * the structural validator (mixed order accepted; split sets, result-first and ordinal
    inversion refused) and the backward-compatible legacy read (card_id = key);
  * the card-id save contract (assign_card_ids): unknown / missing / type-changed ids are
    refused, new cards get a server id, a legacy fixed-order body inherits ids;
  * the real PUT route: reorder + save keeps ids, delete-then-re-add gets a NEW id, the old
    id is refused, revision restore brings the snapshot's ids back;
  * the AI-facing contract (template rules, contract example).
"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from tests.test_work_plan_0395 import (  # noqa: F401 — fixtures are used by name
    GROUP,
    PROJECT,
    ROOT_DOC,
    _client,
    patch_store,
    seed,
    storage_root,
    tmp_db,
)


def _wp():
    from modules.flow_gate.services import work_plan_service as wp

    return wp


def _body(counts: dict[str, int], order: list[str] | None = None, *, card_ids: bool = True) -> dict:
    """A canonical body for ``counts``; ``order`` lists card keys (instruction/single)."""
    wp = _wp()
    counted = list(counts)
    quantities = {
        code: {"unit": wp.WORK_PLAN_TYPE_UNITS[code], "count": count}
        for code, count in counts.items()
    }
    steps = wp.expand_steps(counted, quantities)
    if order is not None:
        by_key = {step["key"]: step for step in steps}
        ordered = []
        for key in order:
            ordered.append(by_key[key])
            if by_key[key]["pair_role"] == "instruction":
                ordered.append(by_key[by_key[key]["pair_key"]])
        assert len(ordered) == len(steps)
        steps = ordered
    if not card_ids:
        for step in steps:
            step.pop("card_id", None)
    return {
        "wp_version": 2,
        "binding": "advisory",
        "counted_types": counted,
        "quantities": quantities,
        "provider_candidates": [],
        "defaults": {"provider_id": None, "note": ""},
        "steps": steps,
    }


def _codes(exc) -> list[str]:
    return [error["code"] for error in exc.value.errors]


# ── Structure: mixed order is a valid plan, broken cards are not ─────────────

def test_r0001_mixed_order_example_validates_and_keeps_its_order():
    wp = _wp()
    body = _body(
        {"N": 1, "T": 2, "T2": 1},
        order=["T#1", "N#1", "T2#1", "T#2"],
    )
    validated = wp.validate(body)
    assert [step["key"] for step in validated["steps"]] == [
        "T#1", "TR#1", "N#1", "NR#1", "T2#1", "TR2#1", "T#2", "TR#2",
    ]


def test_legacy_fixed_order_body_still_validates_and_reads_card_id_as_key():
    wp = _wp()
    legacy = _body({"D": 1, "T": 2, "TS": 1}, card_ids=False)
    validated = wp.validate(legacy)
    assert [(s["key"], s["card_id"]) for s in validated["steps"]] == [
        ("D#1", "D#1"), ("T#1", "T#1"), ("TR#1", "T#1"), ("T#2", "T#2"), ("TR#2", "T#2"),
        ("TS#1", "TS#1"), ("TSR#1", "TS#1"),
    ]
    # Canonical output always carries the field now, right after key.
    assert list(validated["steps"][0])[:2] == ["key", "card_id"]


def test_a_set_split_by_another_card_is_refused():
    wp = _wp()
    body = _body({"N": 1, "T": 1})
    steps = {s["key"]: s for s in body["steps"]}
    body["steps"] = [steps["T#1"], steps["N#1"], steps["NR#1"], steps["TR#1"]]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "pair_not_adjacent" in _codes(exc)


def test_a_result_before_its_instruction_is_refused():
    wp = _wp()
    body = _body({"T": 1})
    body["steps"] = list(reversed(body["steps"]))
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "pair_not_adjacent" in _codes(exc)


def test_ordinal_inversion_inside_one_type_is_refused():
    wp = _wp()
    body = _body({"T": 2}, order=["T#2", "T#1"])
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "ordinal_order_invalid" in _codes(exc)


def test_count_mismatch_keeps_its_old_code():
    wp = _wp()
    body = _body({"D": 2})
    body["steps"] = body["steps"][:1]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc) == ["steps_quantity_mismatch"]


def test_overtaking_cards_of_one_type_are_renumbered_but_keep_their_card_ids():
    """B moves in front of A: keys follow position (T#1/T#2), card ids follow the card."""
    wp = _wp()
    body = _body({"T": 2})
    a_instr, a_result, b_instr, b_result = copy.deepcopy(body["steps"])
    assert (a_instr["card_id"], b_instr["card_id"]) == ("T#1", "T#2")
    for step, key, pair, ordinal in (
        (b_instr, "T#1", "TR#1", 1), (b_result, "TR#1", "T#1", 1),
        (a_instr, "T#2", "TR#2", 2), (a_result, "TR#2", "T#2", 2),
    ):
        step.update(key=key, pair_key=pair, ordinal=ordinal)
    body["steps"] = [b_instr, b_result, a_instr, a_result]
    validated = wp.validate(body)
    assert [(s["key"], s["card_id"]) for s in validated["steps"]] == [
        ("T#1", "T#2"), ("TR#1", "T#2"), ("T#2", "T#1"), ("TR#2", "T#1"),
    ]


# ── card_id format rules ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "retired:r0:T#1", "x" * 65, "has space", 7])
def test_card_id_format_is_enforced(bad):
    wp = _wp()
    body = _body({"D": 1})
    body["steps"][0]["card_id"] = bad
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "card_id_invalid" in _codes(exc)


def test_two_cards_cannot_share_a_card_id():
    wp = _wp()
    body = _body({"D": 2})
    body["steps"][1]["card_id"] = body["steps"][0]["card_id"]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "card_id_duplicate" in _codes(exc)


def test_a_set_instruction_and_result_share_one_card_id():
    wp = _wp()
    body = _body({"T": 1})
    body["steps"][1]["card_id"] = "c_other"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "card_id_pair_mismatch" in _codes(exc)


def test_a_body_where_only_some_steps_have_card_ids_is_not_legacy():
    wp = _wp()
    body = _body({"D": 2})
    body["steps"][1].pop("card_id")
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc) == ["card_id_missing"]


def test_reading_a_partially_identified_body_is_schema_invalid(tmp_path):
    wp = _wp()
    body = _body({"D": 2})
    body["steps"][1].pop("card_id")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(wp.WorkPlanUnreadable) as exc:
        wp.load_body(path)
    assert exc.value.reason == "schema_invalid"


def test_reading_a_legacy_file_fills_card_ids_in_memory_only(tmp_path):
    wp = _wp()
    legacy = _body({"T": 1}, card_ids=False)
    path = tmp_path / "plan.json"
    raw = json.dumps(legacy)
    path.write_text(raw, encoding="utf-8")
    loaded = wp.load_body(path)
    assert [s["card_id"] for s in loaded["steps"]] == ["T#1", "T#1"]
    assert path.read_text(encoding="utf-8") == raw  # reads never rewrite the file
    assert wp.body_stores_card_ids(json.loads(raw)) is False
    assert wp.body_stores_card_ids(loaded) is True


# ── Save contract (assign_card_ids) ───────────────────────────────────────────

def test_save_refuses_an_existing_card_that_lost_its_id():
    wp = _wp()
    previous = wp.validate(_body({"D": 2}))
    sent = copy.deepcopy(previous)
    sent["steps"][1].pop("card_id")
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert _codes(exc) == ["card_id_missing"]


def test_save_refuses_a_card_id_the_previous_body_never_had():
    wp = _wp()
    previous = wp.validate(_body({"D": 1}))
    sent = _body({"D": 2})  # D#2 arrives carrying the made-up id "D#2"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert _codes(exc) == ["card_id_unknown"]
    assert exc.value.errors[0]["params"]["value"] == "D#2"


def test_save_refuses_a_card_whose_type_changed():
    wp = _wp()
    previous = wp.validate(_body({"D": 1, "P": 1}))
    sent = copy.deepcopy(previous)
    p_step = next(s for s in sent["steps"] if s["type"] == "P")
    d_step = next(s for s in sent["steps"] if s["type"] == "D")
    p_step["card_id"], d_step["card_id"] = d_step["card_id"], p_step["card_id"]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert set(_codes(exc)) == {"card_id_type_changed"}


def test_new_cards_get_a_server_id_and_a_set_shares_it():
    wp = _wp()
    previous = wp.validate(_body({"T": 1}))
    sent = copy.deepcopy(previous)
    sent["quantities"]["T"]["count"] = 2
    new_steps = [s for s in wp.expand_steps(["T"], sent["quantities"]) if s["ordinal"] == 2]
    for step in new_steps:
        step.pop("card_id")
    sent["steps"] = sent["steps"] + new_steps
    assigned = wp.assign_card_ids(sent, previous)
    ids = {s["key"]: s["card_id"] for s in assigned["steps"]}
    assert ids["T#1"] == ids["TR#1"] == "T#1"
    assert ids["T#2"].startswith("c_") and ids["T#2"] == ids["TR#2"]
    assert wp.validate(assigned)["steps"][2]["card_id"] == ids["T#2"]


def test_legacy_fixed_order_body_without_ids_inherits_the_previous_ids():
    wp = _wp()
    previous = wp.validate(_body({"D": 1, "T": 1}))
    previous["steps"][1]["card_id"] = previous["steps"][2]["card_id"] = "c_knowncard01"
    sent = _body({"D": 2, "T": 1}, card_ids=False)
    assigned = wp.assign_card_ids(sent, previous)
    ids = {s["key"]: s["card_id"] for s in assigned["steps"]}
    assert ids["D#1"] == "D#1"
    assert ids["T#1"] == ids["TR#1"] == "c_knowncard01"
    assert ids["D#2"].startswith("c_") and ids["D#2"] != "c_knowncard01"


def test_reordered_body_without_ids_is_refused_instead_of_guessed():
    wp = _wp()
    previous = wp.validate(_body({"D": 1, "T": 1}))
    sent = _body({"D": 1, "T": 1}, order=["T#1", "D#1"], card_ids=False)
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.assign_card_ids(sent, previous)
    assert set(_codes(exc)) == {"card_id_missing"}


def test_creation_keeps_sent_ids_and_fills_missing_ones():
    wp = _wp()
    sent = _body({"D": 2})
    sent["steps"][0]["card_id"] = "c_fromclient1"
    sent["steps"][1].pop("card_id")
    assigned = wp.assign_card_ids(sent, None)
    assert assigned["steps"][0]["card_id"] == "c_fromclient1"
    assert assigned["steps"][1]["card_id"].startswith("c_")


# ── The real PUT route ───────────────────────────────────────────────────────

def _create(client, quantities: dict[str, int], seq: str) -> dict:
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value=seq,
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "counted_types": list(quantities),
            "provider_candidates": [],
            "quantities": quantities,
        })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _put(client, doc_id: str, body: dict, revision: int):
    return client.put(
        f"/api/v1/documents/{doc_id}/work-plan",
        json={"base_revision_no": revision, "body": body, "capability_warning_acks": []},
    )


def _renumbered(body: dict, card_order: list[str]) -> dict:
    """Move cards (by card_id) into ``card_order`` and renumber keys like the editor does."""
    wp = _wp()
    cards: dict[str, list[dict]] = {}
    for step in body["steps"]:
        cards.setdefault(step["card_id"], []).append(copy.deepcopy(step))
    out, seen = [], {}
    for card_id in card_order:
        steps = cards[card_id]
        code = steps[0]["type"]
        seen[code] = seen.get(code, 0) + 1
        ordinal = seen[code]
        if len(steps) == 2:
            result_code = wp.WORK_PLAN_PAIR_MAP[code]
            steps[0].update(key=f"{code}#{ordinal}", ordinal=ordinal, pair_key=f"{result_code}#{ordinal}")
            steps[1].update(key=f"{result_code}#{ordinal}", ordinal=ordinal, pair_key=f"{code}#{ordinal}")
        else:
            steps[0].update(key=f"{code}#{ordinal}", ordinal=ordinal)
        out.extend(steps)
    return {**copy.deepcopy(body), "steps": out}


def test_put_reorder_keeps_ids_and_delete_then_readd_gets_a_new_id(seed, storage_root):
    client = _client()
    created = _create(client, {"D": 1, "T": 2}, "0601-WP")
    doc_id = created["doc_id"]
    body = created["body"]
    assert [s["card_id"] for s in body["steps"]] == ["D#1", "T#1", "T#1", "T#2", "T#2"]

    # rev1: mixed order — T#2's card goes first, D in the middle.
    moved = _renumbered(body, ["T#2", "D#1", "T#1"])
    saved = _put(client, doc_id, moved, 0)
    assert saved.status_code == 200, saved.text
    rev1 = saved.json()["body"]
    assert [(s["key"], s["card_id"]) for s in rev1["steps"]] == [
        ("T#1", "T#2"), ("TR#1", "T#2"), ("D#1", "D#1"), ("T#2", "T#1"), ("TR#2", "T#1"),
    ]
    reread = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()["body"]
    assert reread == rev1

    # rev2: drop the last T card (card "T#1", now keyed T#2).
    shrunk = copy.deepcopy(rev1)
    shrunk["quantities"]["T"]["count"] = 1
    shrunk["steps"] = shrunk["steps"][:3]
    saved = _put(client, doc_id, shrunk, 1)
    assert saved.status_code == 200, saved.text

    # rev3 attempt: re-add it with the deleted card's old id -> card_id_unknown (422).
    revived = copy.deepcopy(saved.json()["body"])
    revived["quantities"]["T"]["count"] = 2
    revived["steps"] += [
        {**copy.deepcopy(rev1["steps"][3])}, {**copy.deepcopy(rev1["steps"][4])},
    ]
    refused = _put(client, doc_id, revived, 2)
    assert refused.status_code == 422, refused.text
    assert {e["code"] for e in refused.json()["errors"]} == {"card_id_unknown"}

    # rev3: the same card added as new (no id) gets a fresh server id.
    for step in revived["steps"][3:]:
        step.pop("card_id")
    saved = _put(client, doc_id, revived, 2)
    assert saved.status_code == 200, saved.text
    rev3 = saved.json()["body"]
    new_id = rev3["steps"][3]["card_id"]
    assert new_id.startswith("c_") and rev3["steps"][4]["card_id"] == new_id
    assert new_id not in {"T#1", "T#2", "D#1"}

    # Restoring rev1 brings rev1's own ids back — the one exemption from card_id_unknown.
    restored = client.post(
        f"/api/v1/documents/{doc_id}/work-plan/revisions/1/restore",
        json={"base_revision_no": 3},
    )
    assert restored.status_code == 200, restored.text
    after = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()["body"]
    assert [s["card_id"] for s in after["steps"]] == [s["card_id"] for s in rev1["steps"]]


def test_put_refuses_ordinal_inversion_and_missing_id(seed, storage_root):
    client = _client()
    created = _create(client, {"T": 2}, "0602-WP")
    doc_id = created["doc_id"]
    body = created["body"]

    inverted = copy.deepcopy(body)
    inverted["steps"] = body["steps"][2:] + body["steps"][:2]  # T#2 before T#1, not renumbered
    resp = _put(client, doc_id, inverted, 0)
    assert resp.status_code == 422
    assert "ordinal_order_invalid" in {e["code"] for e in resp.json()["errors"]}

    # Swapping two T cards and dropping every id renumbers back to the fixed order, which a
    # pre-card-id client also sends for "nothing moved" — that body inherits by key (NR0003
    # §5.1 exception) and cannot be told apart, so it is accepted.
    same_shape = _renumbered(body, ["T#2", "T#1"])
    for step in same_shape["steps"]:
        step.pop("card_id")
    assert [s["key"] for s in same_shape["steps"]] == ["T#1", "TR#1", "T#2", "TR#2"]

    mixed = _create(client, {"D": 1, "T": 1}, "0603-WP")
    lost = _renumbered(mixed["body"], ["T#1", "D#1"])
    for step in lost["steps"]:
        step.pop("card_id")
    resp = _put(client, mixed["doc_id"], lost, 0)
    assert resp.status_code == 422
    assert {e["code"] for e in resp.json()["errors"]} == {"card_id_missing"}


# ── AI-facing contract ────────────────────────────────────────────────────────

def test_contract_example_is_mixed_and_carries_card_ids():
    wp = _wp()
    example = wp.contract_example()
    keys = [s["key"] for s in example["steps"]]
    assert keys == ["T#1", "TR#1", "D#1", "TS#1", "TSR#1", "T#2", "TR#2", "TS#2", "TSR#2"]
    assert all(s.get("card_id") for s in example["steps"])
    assert wp.validate(copy.deepcopy(example)) == example


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_template_rules_describe_mixed_order_and_card_id(locale):
    wp = _wp()
    payload = wp.template_payload(locale)
    text = "\n".join(payload["rules"])
    assert "card_id" in text
    assert "순서까지 같아야" not in text
    assert "in the same order" not in text
    assert payload["contract"]["optional_step_fields"] == ["card_id"]


def test_ai_inbox_edit_follows_the_same_card_id_contract(seed, storage_root, tmp_path):
    """The AI path goes through the same save checks as the human PUT (NR0003 §5.1)."""
    from tests.test_work_plan_0395 import _inbox_edit, _inbox_post

    wp = _wp()
    initial = wp.validate(_body({"D": 1, "T": 1}))
    created = _inbox_post(tmp_path, wp.dumps(initial), doc_code="WP0604")
    assert created.status_code == 201, created.text
    doc_id = created.json()["doc_id"]

    # A grown plan that invents ids for its new cards is refused …
    invented = wp.validate(_body({"D": 1, "T": 2}, order=["T#1", "D#1", "T#2"]))
    refused = _inbox_edit(tmp_path, doc_id, wp.dumps(invented))
    assert refused.status_code == 400, refused.text
    assert "card_id" in refused.json()["error_message"]

    # … the same mixed-order plan with the new card left without an id is stored, and the
    # moved cards keep their ids.
    for step in invented["steps"]:
        if step["key"] in ("T#2", "TR#2"):
            step.pop("card_id")
    accepted = _inbox_edit(tmp_path, doc_id, json.dumps(invented))
    assert accepted.status_code == 200, accepted.text
    stored = _client().get(f"/api/v1/documents/{doc_id}/work-plan").json()["body"]
    assert [(s["key"], s["card_id"][:2] if s["key"] in ("T#2", "TR#2") else s["card_id"])
            for s in stored["steps"]] == [
        ("T#1", "T#1"), ("TR#1", "T#1"), ("D#1", "D#1"), ("T#2", "c_"), ("TR#2", "c_"),
    ]
