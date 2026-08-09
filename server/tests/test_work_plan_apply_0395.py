"""Focused acceptance tests for flowgate.default.0395 T0015."""
from __future__ import annotations

import json

import pytest

from modules.flow_gate.services import work_plan_apply_service as svc


def step(key, provider="p1", note="note", *, locked=False):
    code, ordinal = key.split("#")
    return {
        "key": key, "type": code, "ordinal": int(ordinal),
        "provider_id": provider, "provider_display_name": provider,
        "note": note, "locked": locked,
    }


def item(seq, code, status="pending", *, order=None, result=None, label=None):
    return {
        "item_seq": seq, "type": code, "status": status,
        "sort_order": seq if order is None else order,
        "result_doc_id": result, "label": label or code,
    }


REGISTRY = [{"id": "p1", "name": "p1", "enabled": True}]


def test_preview_calculation_does_not_mutate_inputs(monkeypatch):
    items = [item(1, "T"), item(2, "TR")]
    before = json.dumps(items, sort_keys=True)
    monkeypatch.setattr(svc, "_sequence", lambda _owner: ({"id": 1}, items))
    plan = {"steps": [step("T#1"), step("TR#1")], "defaults": {"note": ""}}
    svc.preview(doc={"doc_id": "g.1-WP", "target_id": "g.1-R", "revision_no": 0,
                     "doc_review_status": "approved"}, plan=plan, providers=REGISTRY,
                instruction_mode="auto_approved")
    assert json.dumps(items, sort_keys=True) == before


def test_preview_separates_keep_and_change_workflow_availability(monkeypatch):
    monkeypatch.setattr(svc, "_sequence", lambda _owner: (None, []))
    plan = {"steps": [step("D#1")], "defaults": {"note": ""}}
    result = svc.preview(
        doc={"doc_id": "g.1-WP", "target_id": "g.1-R", "revision_no": 0,
             "doc_review_status": "approved"},
        plan=plan, providers=REGISTRY, instruction_mode="ai_direct",
    )
    assert result["can_apply_without_workflow"] is False
    assert result["apply_blockers"]["keep_workflow"] == "workflow_not_decided"
    assert result["can_apply_with_workflow"] is True
    assert result["apply_blockers"]["change_workflow"] is None


def test_preview_disables_both_paths_when_nothing_can_be_filled(monkeypatch):
    monkeypatch.setattr(svc, "_sequence", lambda _owner: ({"id": 1}, [item(1, "D")]))
    plan = {"steps": [step("D#1", None, "")], "defaults": {"note": ""}}
    result = svc.preview(
        doc={"doc_id": "g.1-WP", "target_id": "g.1-R", "revision_no": 0,
             "doc_review_status": "approved"},
        plan=plan, providers=REGISTRY, instruction_mode="ai_direct",
    )
    assert result["can_apply_without_workflow"] is False
    assert result["can_apply_with_workflow"] is False
    assert result["apply_blockers"] == {
        "keep_workflow": "nothing_to_fill",
        "change_workflow": "nothing_to_fill",
    }


def test_logical_key_mapping_counts_occurrences():
    rows = svc.build_step_map([step("T#1"), step("T#2")], [item(4, "T"), item(9, "T")])
    assert [x["item_seq"] for x in rows] == [4, 9]


def test_completed_slot_still_counts_for_ordinal():
    rows = svc.build_step_map([step("T#1"), step("T#2")],
                              [item(4, "T", "done"), item(9, "T")])
    assert rows[0]["item_seq"] == 4 and rows[1]["item_seq"] == 9


def test_mapping_uses_item_seq_not_sort_order():
    rows = svc.build_step_map([step("T#1"), step("T#2")],
                              [item(8, "T", order=99), item(2, "T", order=1)])
    assert [x["item_seq"] for x in rows] == [2, 8]


def test_auto_approved_projection_folds_n_and_t():
    steps = [step("T#1")]
    items = [item(1, "T"), item(2, "TR")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "auto_approved", REGISTRY)
    assert out["provider_overrides"] == {"2": "p1"}
    assert out["filled_item_seqs"] == [2]


def test_ai_direct_projection_does_not_fold():
    steps = [step("T#1")]
    items = [item(1, "T"), item(2, "TR")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "ai_direct", REGISTRY)
    assert out["provider_overrides"] == {"1": "p1"} and out["folded"] == []


def test_ts_never_folds_in_auto_mode():
    steps = [step("TS#1")]
    items = [item(1, "TS"), item(2, "TSR")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "auto_approved", REGISTRY)
    assert out["provider_overrides"] == {"1": "p1"}


def test_own_provider_wins_over_folded_provider():
    steps = [step("T#1", "p2"), step("TR#1", "p1")]
    items = [item(1, "T"), item(2, "TR")]
    registry = REGISTRY + [{"id": "p2", "name": "p2"}]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "auto_approved", registry)
    assert out["provider_overrides"]["2"] == "p1"


def test_own_note_and_folded_provider_are_independent():
    steps = [step("T#1", "p1", "from T"), step("TR#1", None, "from TR")]
    items = [item(1, "T"), item(2, "TR")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "auto_approved", REGISTRY)
    assert out["provider_overrides"]["2"] == "p1"
    assert out["note_overrides"]["2"] == "from TR"


def test_nearer_of_two_folded_instructions_wins():
    steps = [step("T#1", "p1", "one"), step("T#2", "p2", "two")]
    items = [item(1, "T"), item(2, "T"), item(3, "TR")]
    registry = REGISTRY + [{"id": "p2", "name": "p2"}]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "auto_approved", registry)
    assert out["provider_overrides"]["3"] == "p2"
    assert len(out["folded"]) == 2


def test_tsr_is_locked_and_unfilled():
    steps, items = [step("TSR#1", locked=True)], [item(1, "TSR")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "ai_direct", REGISTRY)
    assert {"key": "TSR#1", "reason": "locked", "item_seq": 1} in out["unfilled"]


def test_unregistered_provider_is_not_substituted():
    steps, items = [step("D#1", "gone")], [item(1, "D")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "ai_direct", REGISTRY)
    assert out["provider_overrides"] == {}


def test_unset_provider_does_not_emit_empty_override():
    steps, items = [step("D#1", None, "note")], [item(1, "D")]
    out = svc.project(steps, svc.build_step_map(steps, items), items, "ai_direct", REGISTRY)
    assert "1" not in out["provider_overrides"] and out["note_overrides"]["1"] == "note"


def test_target_suggestion_skips_locked_folded_and_unavailable():
    steps = [step("T#1"), step("TR#1"), step("TS#1", "gone"), step("TSR#1", locked=True)]
    items = [item(1, "T"), item(2, "TR"), item(3, "TS"), item(4, "TSR")]
    mapping = svc.build_step_map(steps, items)
    folded = svc.project(steps, mapping, items, "auto_approved", REGISTRY)["folded"]
    assert svc.suggest_target_seq(steps, mapping, folded, REGISTRY) == 2


def test_workflow_tag_is_stable_without_updated_at():
    sequence = {"id": 7, "head_advanced_at": None, "updated_at": "one"}
    items = [item(1, "D")]
    first = svc.build_workflow_tag(sequence, items)
    sequence["updated_at"] = "two"
    assert svc.build_workflow_tag(sequence, items) == first


def test_workflow_tag_changes_for_status_and_count():
    sequence, items = {"id": 7, "head_advanced_at": None}, [item(1, "D")]
    first = svc.build_workflow_tag(sequence, items)
    items[0]["status"] = "done"
    second = svc.build_workflow_tag(sequence, items)
    assert first != second
    assert svc.build_workflow_tag(sequence, items + [item(2, "P")]).endswith("-i2")


def test_conflict_shape_carries_both_workflow_tags():
    exc = svc.ApplyConflict("workflow_changed", {
        "sent_workflow_tag": "old", "current_workflow_tag": "new",
    })
    assert exc.payload == {"sent_workflow_tag": "old", "current_workflow_tag": "new"}


def test_missing_item_decision_is_idempotent_by_type_count(monkeypatch):
    monkeypatch.setattr(svc, "expand_steps_with_reports", lambda rows, _locale: rows)
    steps = [step("D#1")]
    added, _ = svc._missing_items(steps, [], "ko")
    added_again, _ = svc._missing_items(steps, added, "ko")
    assert len(added) == 1 and added_again == []


def test_keep_workflow_reports_unmatched_plan_steps():
    rows = svc.build_step_map([step("T#2")], [item(1, "T")])
    assert rows[0]["status"] == "unmatched"


def test_no_workflow_tag_is_none():
    assert svc.build_workflow_tag(None, []) == "none"


def test_all_warning_codes_fire_and_have_distinct_three_locale_copy():
    """P0009 §7.3: every warning is reachable and fully translated.

    This intentionally drives all warning branches at once. Adding a new code to
    ``WARNING_CODES`` without adding its trigger or any locale must fail here.
    """
    plan_steps = [
        step("D#1", None, ""),
        step("P#1", "gone", ""),
        {**step("L#1", "p1", "note"), "provider_display_name": "old name"},
        step("TSR#1", "p1", "locked value", locked=True),
    ]
    step_map = [
        {"key": "D#1", "matched": True, "item_seq": 1, "status": "done"},
    ]
    projection = {
        "provider_overrides": {}, "note_overrides": {},
        "folded": [{"from_key": "T#1", "to_key": "TR#1", "to_item_seq": 2}],
    }
    messages = {}
    for locale in ("ko", "en", "ja"):
        warnings = svc.build_warnings(
            plan_steps=plan_steps,
            step_map=step_map,
            provider_registry=REGISTRY,
            projection=projection,
            sequence_decided=False,
            added=[{"plan_key": "D#2"}],
            extra_item_seqs=[9],
            unplaceable_keys=["X#1"],
            order_differs_keys=["P#1"],
            wp_review_status="pending_review",
            unmatched_keys=["T#2"],
            locale=locale,
        )
        assert {row["code"] for row in warnings} == set(svc.WARNING_CODES)
        messages[locale] = {row["code"]: row["message"] for row in warnings}
        assert all(text.strip() for text in messages[locale].values())

    for code in svc.WARNING_CODES:
        assert len({messages[locale][code] for locale in ("ko", "en", "ja")}) == 3


def test_application_journal_is_latest_first_and_tolerates_broken_line(tmp_path):
    plan_path = tmp_path / "document.json"
    svc.append_application(plan_path, "g.0002-WP", {"applied_at": "one"})
    path = svc._applications_path(plan_path, "g.0002-WP")
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    svc.append_application(plan_path, "g.0002-WP", {"applied_at": "two"})
    result = svc.read_applications(plan_path, "g.0002-WP")
    assert [x["applied_at"] for x in result["items"]] == ["two", "one"]
    assert result["broken_lines"] == 1
