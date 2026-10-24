"""Regression matrix for flowgate.default.0554 WorkPlan v2 execution settings."""
from __future__ import annotations

import copy

import pytest


def _body(type_code: str = "D"):
    from modules.flow_gate.services import work_plan_service as wp

    return wp.initial_body(
        [type_code],
        [],
        quantities={type_code: 1},
    )


def _codes(exc) -> list[str]:
    return [item["code"] for item in exc.value.errors]


def test_v1_promotes_in_memory_and_preserves_extensions():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body()
    body["wp_version"] = 1
    body["x_top"] = {"keep": True}
    body["steps"][0]["x_step"] = "keep"
    for field in (
        "review_count",
        "reviewer_provider_id",
        "reviewer_provider_display_name",
        "pre_instruction_text",
        "pre_instruction_attachment",
    ):
        body["steps"][0].pop(field)

    promoted = wp.validate(body)
    assert promoted["wp_version"] == 2
    assert promoted["x_top"] == {"keep": True}
    assert promoted["steps"][0]["x_step"] == "keep"
    assert promoted["steps"][0]["review_count"] == 0
    assert promoted["steps"][0]["reviewer_provider_id"] is None
    assert promoted["steps"][0]["pre_instruction_attachment"] is None
    assert body["wp_version"] == 1
    assert "review_count" not in body["steps"][0]


def test_v2_missing_execution_field_is_not_implicitly_filled():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body()
    body["steps"][0].pop("review_count")
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "missing_field" in _codes(exc)


@pytest.mark.parametrize("value", [0, -1])
def test_review_count_zero_and_unlimited(value):
    from modules.flow_gate.services import work_plan_service as wp

    body = _body()
    body["steps"][0]["review_count"] = value
    assert wp.validate(body)["steps"][0]["review_count"] == value


def test_review_count_finite_max_bool_and_out_of_range():
    from modules.flow_gate.services import work_plan_service as wp

    finite_max = max(value for value in wp.review_count_choices() if value > 0)
    body = _body()
    body["steps"][0]["review_count"] = finite_max
    wp.validate(body)

    for invalid in (True, finite_max + 1):
        rejected = copy.deepcopy(body)
        rejected["steps"][0]["review_count"] = invalid
        with pytest.raises(wp.WorkPlanValidationError) as exc:
            wp.validate(rejected)
        assert "review_count_invalid" in _codes(exc)


def test_reviewer_must_be_enabled_and_name_is_server_snapshot(monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp

    body = _body()
    step = body["steps"][0]
    step["review_count"] = 1
    step["reviewer_provider_id"] = "aip_review"
    step["reviewer_provider_display_name"] = "stale"

    monkeypatch.setattr(
        wp,
        "_registered_providers",
        lambda project_id: [{"id": "aip_review", "name": "Reviewer Current"}],
    )
    saved = wp.validate(body, project_id="p")
    assert saved["steps"][0]["reviewer_provider_display_name"] == "Reviewer Current"

    monkeypatch.setattr(wp, "_registered_providers", lambda project_id: [])
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body, project_id="p")
    assert "reviewer_provider_unavailable" in _codes(exc)


def test_reviewer_display_without_id_and_reviewer_when_disabled_are_rejected():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body()
    body["steps"][0]["reviewer_provider_display_name"] = "orphan"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "reviewer_display_name_without_provider_id" in _codes(exc)

    body = _body()
    body["steps"][0]["reviewer_provider_id"] = "aip_review"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "reviewer_not_allowed" in _codes(exc)


def test_result_review_is_allowed_but_tsr_review_is_locked():
    from modules.flow_gate.services import work_plan_service as wp

    t_body = _body("T")
    tr = next(step for step in t_body["steps"] if step["type"] == "TR")
    tr["review_count"] = 1
    wp.validate(t_body)

    ts_body = _body("TS")
    tsr = next(step for step in ts_body["steps"] if step["type"] == "TSR")
    tsr["review_count"] = 1
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(ts_body)
    assert "reviewer_not_allowed" in _codes(exc)


def test_pre_instruction_text_boundary_and_control_matrix():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body("T")
    instruction = next(step for step in body["steps"] if step["pair_role"] == "instruction")
    instruction["pre_instruction_text"] = (
        "a" * (wp.PRE_INSTRUCTION_TEXT_MAX_CHARS - 4) + "\r\n\t끝"
    )
    wp.validate(body)

    too_long = copy.deepcopy(body)
    too_long["steps"][0]["pre_instruction_text"] = (
        "a" * (wp.PRE_INSTRUCTION_TEXT_MAX_CHARS + 1)
    )
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(too_long)
    assert "pre_instruction_too_long" in _codes(exc)

    controlled = copy.deepcopy(body)
    controlled["steps"][0]["pre_instruction_text"] = "bad\x00value"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(controlled)
    assert "pre_instruction_control_char" in _codes(exc)


def test_pre_instruction_result_forbidden_and_empty_text_canonicalizes_null():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body("T")
    instruction, result = body["steps"]
    instruction["pre_instruction_text"] = ""
    assert wp.validate(body)["steps"][0]["pre_instruction_text"] is None

    body = _body("T")
    result = next(step for step in body["steps"] if step["pair_role"] == "result")
    result["pre_instruction_text"] = "must not reach a result step"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "pre_instruction_not_allowed" in _codes(exc)


def test_attachment_reference_save_and_ai_matrix(monkeypatch):
    from modules.flow_gate.services import work_plan_attachment_service as wp_attach
    from modules.flow_gate.services import work_plan_service as wp

    reference = {
        "doc_id": "p.m.g.0001-WP",
        "filename": "__wp_pre_instruction__T-1__0123456789abcdef.txt",
        "original_filename": "brief.txt",
        "content_sha256": "a" * 64,
    }
    body = _body("T")
    instruction = next(step for step in body["steps"] if step["pair_role"] == "instruction")
    instruction["pre_instruction_attachment"] = reference
    monkeypatch.setattr(wp_attach, "validate_reference", lambda doc_id, ref: None)
    saved = wp.validate(body, doc_id=reference["doc_id"])
    assert saved["steps"][0]["pre_instruction_attachment"] == reference

    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(
            body,
            doc_id=reference["doc_id"],
            allow_pre_instruction_attachments=False,
        )
    assert "pre_instruction_attachment_ai_forbidden" in _codes(exc)


def test_read_mode_keeps_stale_provider_and_attachment_visible():
    from modules.flow_gate.services import work_plan_service as wp

    body = _body("T")
    step = body["steps"][0]
    step["review_count"] = 30
    step["reviewer_provider_id"] = "aip_deleted"
    step["reviewer_provider_display_name"] = "Deleted reviewer"
    step["pre_instruction_attachment"] = {
        "doc_id": "old",
        "filename": "__wp_pre_instruction__T-1__old.txt",
        "original_filename": "old.txt",
        "content_sha256": "b" * 64,
    }
    loaded = wp.validate(
        body,
        project_id="p",
        doc_id="current",
        enforce_provider_scope=False,
    )
    assert loaded["steps"][0]["reviewer_provider_id"] == "aip_deleted"
    assert loaded["steps"][0]["pre_instruction_attachment"]["doc_id"] == "old"


def test_help_contract_and_ai_examples_share_v2_fields():
    from modules.flow_gate.services import work_plan_service as wp

    payload = wp.template_payload("en")
    assert payload["contract"]["step_fields"] == list(wp.STEP_FIELD_ORDER)
    assert payload["contract"]["pre_instruction_text_max_chars"] == 20_000
    assert payload["contract"]["review_count_choices"] == list(
        wp.review_count_choices()
    )
    assert all(
        example["pre_instruction_attachment"] is None
        for example in payload["examples"].values()
    )
    assert all(
        step["pre_instruction_attachment"] is None
        for step in payload["example"]["steps"]
    )
