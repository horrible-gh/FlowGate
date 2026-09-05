"""design_template/WP help contract vs validator drift guard (flowgate.default.0499 T0004).

NR0003 found the help item published only key/provider_id/note while validate() actually
requires 11 canonical step fields plus pair_role/origin/locked_reason enums, the N/T/TS
pairing rules and TSR's server-assembled shape. These tests pin the fix: the help payload
now publishes the full contract (T0004 completion criteria 1-8, 11) and its worked example
passes validate() unchanged (criteria 9-10), without weakening validate() itself (criterion 12).
"""
from __future__ import annotations

import json

import pytest

from tests.test_work_plan_0395 import (  # noqa: F401 — fixtures are used by name
    GROUP,
    PROJECT,
    ROOT_DOC,
    patch_store,
    seed,
    tmp_db,
)


def _wp_help_ctx():
    return {
        "locale": "ko", "base_url": "http://localhost/flowgate/api/v1",
        "doc_type": "WP", "action_scope": "new", "project": PROJECT,
        "group_id": GROUP, "doc_id": ROOT_DOC, "scratch_dir": r"C:\scratch\tok_1",
        "source_mode": "remote", "tool_kind": "read", "registry": {"tools": []},
        "principal_kind": "worker_token",
    }


def test_help_payload_publishes_all_step_fields_and_enums(seed):
    """T0004 §18 — every canonical field/enum/pairing-key literal must reach the worker."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    text = json.dumps(payload, ensure_ascii=False)
    for field in wp.STEP_FIELD_ORDER:
        assert field in text
    for value in wp.PAIR_ROLES:
        assert value in text
    for value in wp.ORIGINS:
        assert value in text
    assert "T#1" in text
    assert "TR#1" in text
    assert "TS#1" in text
    assert "TSR#1" in text
    assert "server_assembled" in text


def test_help_payload_publishes_pairing_and_single_step_rules(seed):
    """T0004 §19 — N/NR, T/TR, TS/TSR pairing plus the single-step pair_role/pair_key rule."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    contract = payload["content"]["contract"]
    assert contract["pair_map"] == {"N": "NR", "T": "TR", "TS": "TSR"}
    assert contract["single_types"] == ["DS", "D", "P", "L", "DB"]
    example = payload["content"]["example"]
    single = next(s for s in example["steps"] if s["pair_role"] == "single")
    assert single["pair_key"] is None


def test_help_example_passes_the_real_validator_unchanged(seed):
    """T0004 §20 — the help item's own worked example must PASS validate() as-is."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    example = payload["content"]["example"]
    validated = wp.validate(example, project_id=PROJECT)
    assert [s["key"] for s in validated["steps"]] == [s["key"] for s in example["steps"]]


def test_help_example_tsr_steps_are_the_server_assembled_shape(seed):
    """T0004 §21 — TSR must never carry a provider/note or a non-system origin."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    tsr_steps = [s for s in payload["content"]["example"]["steps"] if s["type"] == "TSR"]
    assert tsr_steps
    for step in tsr_steps:
        assert step["provider_id"] is None
        assert step["provider_display_name"] is None
        assert step["note"] is None
        assert step["locked"] is True
        assert step["locked_reason"] == "server_assembled"
        assert step["origin"] == "system"


def test_t_two_sets_plus_ts_two_sets_regression():
    """T0004 §22 — the exact failure NR0003 reproduced: T count 2 + TS count 2 must expand
    to T#1/TR#1/T#2/TR#2/TS#1/TSR#1/TS#2/TSR#2, each pair pointing back at the other's key."""
    from modules.flow_gate.services import work_plan_service as wp

    quantities = {
        "T": {"unit": "set", "count": 2},
        "TS": {"unit": "set", "count": 2},
    }
    steps = wp.expand_steps(["T", "TS"], quantities)
    assert [s["key"] for s in steps] == [
        "T#1", "TR#1", "T#2", "TR#2", "TS#1", "TSR#1", "TS#2", "TSR#2",
    ]
    by_key = {s["key"]: s for s in steps}
    assert by_key["T#1"]["pair_key"] == "TR#1"
    assert by_key["TR#1"]["pair_key"] == "T#1"
    assert by_key["T#2"]["pair_key"] == "TR#2"
    assert by_key["TR#2"]["pair_key"] == "T#2"
    assert by_key["TS#1"]["pair_key"] == "TSR#1"
    assert by_key["TSR#1"]["pair_key"] == "TS#1"
    assert by_key["TS#2"]["pair_key"] == "TSR#2"
    assert by_key["TSR#2"]["pair_key"] == "TS#2"


def test_step_contract_is_read_from_the_same_constants_the_validator_uses():
    """T0004 §15/§17 — help and validate() must share one source, never two typed-out copies."""
    from modules.flow_gate.services import work_plan_service as wp

    contract = wp.step_contract()
    assert contract["step_fields"] == list(wp.STEP_FIELD_ORDER)
    assert contract["pair_roles"] == list(wp.PAIR_ROLES)
    assert contract["origins"] == list(wp.ORIGINS)
    assert contract["pair_map"] == dict(wp.WORK_PLAN_PAIR_MAP)
    assert contract["locked_types"] == sorted(wp.WORK_PLAN_LOCKED_TYPES)


def test_note_rule_does_not_contradict_the_null_notes_in_the_worked_example(seed):
    """rev1 fix — the rule text and contract_example() must agree that non-TSR notes
    may be null; a rule demanding every non-TSR note be filled would contradict the
    very example the same payload publishes."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    rules = "\n".join(payload["content"]["rules"])
    assert "null" in rules
    example = payload["content"]["example"]
    non_tsr_notes = [s["note"] for s in example["steps"] if s["type"] != "TSR"]
    assert non_tsr_notes and all(note is None for note in non_tsr_notes)
    # The rule must not still be validated by the example, i.e. validate() must accept
    # exactly this null-note shape unchanged.
    validated = wp.validate(example, project_id=PROJECT)
    assert validated["steps"] == example["steps"]


def test_field_count_rule_permits_x_prefixed_extension_fields(seed):
    """rev1 fix — "exactly N fields" must not contradict _unknown_fields()/canonicalize()
    permitting x_-prefixed extensions (test_work_plan_0395 already locks that behavior)."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    rules = "\n".join(payload["content"]["rules"])
    assert "x_" in rules
    example = payload["content"]["example"]
    step = dict(example["steps"][0])
    step["x_note_source"] = "test"
    body = dict(example)
    body["steps"] = [step] + example["steps"][1:]
    validated = wp.validate(body, project_id=PROJECT)
    assert validated["steps"][0]["x_note_source"] == "test"


def test_provider_unspecified_rule_is_published_and_enforced(seed):
    """rev2 fix — automated review: T0004 §12/§26.8 requires the help to state that
    provider-unspecified is BOTH provider_id=null and provider_display_name=null, and
    validate() must actually enforce that pairing so a worker can derive the rule from
    the validator too, not just from prose."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    rules = "\n".join(payload["content"]["rules"])
    assert "provider_id" in rules and "provider_display_name" in rules
    assert "null" in rules

    example = payload["content"]["example"]
    non_locked = [s for s in example["steps"] if not s["locked"]]
    assert non_locked and all(
        s["provider_id"] is None and s["provider_display_name"] is None for s in non_locked
    )

    body = dict(example)
    body["steps"] = [dict(s) for s in example["steps"]]
    target = next(s for s in body["steps"] if not s["locked"])
    target["provider_display_name"] = "Claude Opus"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body, project_id=PROJECT)
    codes = [e["code"] for e in wp.render_errors(exc.value.errors, "ko")]
    assert "provider_display_name_without_provider_id" in codes
