"""flowgate.default.0490 T0005: server-side regression tests for the repeat-count
ceiling SSOT (ai_repeat_count_max) — NR0003 §20's server-provable cases.

Uses the same lightweight monkeypatch technique test_source_mode_0147.py uses for
source_mode_service: the DB layer (modules.flow_gate.db.system_settings) is patched
with a small in-memory dict, and both system_settings_service (`_db`) and
ai_execution_policy_service (`_system_settings`) see the same patched module object
because they import it, not a copy of it. get_repeat_count_max() itself is never
patched — every test exercises the real validation/resolution code.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

from modules.flow_gate.api.v1 import ai_invoke_routes as routes
from modules.flow_gate.db import system_settings as db_system_settings
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import workflow_decision_service
from modules.flow_gate.settings import ai_execution_policy_service as policy
from modules.flow_gate.settings import system_settings_service as settings_service
from modules.flow_gate.settings.routers import system as system_router


@pytest.fixture
def fake_settings(monkeypatch):
    """An in-memory stand-in for the system_settings table, keyed like the real rows."""
    store: dict[str, dict] = {}

    def _get(key):
        return store.get(key)

    def _get_value(key, default=None):
        row = store.get(key)
        return row["setting_value"] if row else default

    def _list_settings():
        return list(store.values())

    def _set_value(key, value, value_type="string", description=None, updated_by=None):
        row = {
            "setting_key": key, "setting_value": value, "value_type": value_type,
            "description": description, "updated_at": "2026-08-31T00:00:00+09:00",
            "updated_by": updated_by,
        }
        store[key] = row
        return row

    monkeypatch.setattr(db_system_settings, "get", _get)
    monkeypatch.setattr(db_system_settings, "get_value", _get_value)
    monkeypatch.setattr(db_system_settings, "list_settings", _list_settings)
    monkeypatch.setattr(db_system_settings, "set_value", _set_value)
    return store


# ── Test 1: row absent → effective 3, synthesized everywhere ─────────────────────

def test_missing_row_defaults_to_three_everywhere(fake_settings):
    assert policy.get_repeat_count_max() == 3

    all_rows = settings_service.get_all()
    row = next(r for r in all_rows if r["setting_key"] == "ai_repeat_count_max")
    assert row["setting_value"] == "3"
    assert row["value_type"] == "integer"

    one = settings_service.get_one("ai_repeat_count_max")
    assert one is not None
    assert one["setting_value"] == "3"


# ── Test 2: max=1 choice sets ─────────────────────────────────────────────────────

def test_choices_at_max_one(fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "1", "integer")
    assert policy.repeat_count_choices(allow_zero=True) == (-1, 0, 1)
    assert policy.repeat_count_choices(allow_zero=False) == (-1, 1)


# ── Test 3: max=10 boundary across the three write paths ─────────────────────────
#
# Each path is driven through the actual route/validation call, not just a choices-helper
# membership check — a helper-only assertion cannot detect a route that forgot to call the
# SSOT, or that calls it with the wrong allow_zero. Paths (b) and (c) pair a boundary value
# with a deliberately-unrelated conflict (an action_scope the route always rejects) so the
# request is guaranteed to bail out with 422 before touching the database regardless of
# whether the boundary value itself is accepted — that keeps this test DB-free while still
# exercising the real per-field validation for both the accepted and rejected side.

def test_max_ten_boundary_across_write_paths(monkeypatch, fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "10", "integer")
    monkeypatch.setattr(
        routes, "_require_user", lambda request: {"issued_to": "u", "_is_user_jwt": True}
    )

    # (a) review-count override path — the exact function the continuous-chain write path
    # calls (workflow_decision_service.normalize_continuation_review_count_overrides).
    assert workflow_decision_service.normalize_continuation_review_count_overrides(
        {"1": 10}
    ) == {"1": 10}
    with pytest.raises(ValueError, match="invalid_review_count_value"):
        workflow_decision_service.normalize_continuation_review_count_overrides({"1": 11})

    # (b) continuation-restart path — the real ai_invoke_routes.start_ai_invoke validation
    # for continuation_restart_max_attempts, not just restart_max_attempts_choices().
    def continuation_restart_body(value):
        return routes.AiInvokeStartRequest(
            project="flowgate", module="default", group="0490",
            doc_ref="flowgate.default.0490.0001-T", action_scope="__boundary_probe__",
            mode="single", continuation_restart_max_attempts=value,
        )

    accepted = routes.start_ai_invoke(continuation_restart_body(10), object())
    assert accepted.status_code == 422  # from the deliberate action_scope conflict, not this field
    accepted_locs = {err["loc"] for err in json.loads(accepted.body)["errors"]}
    assert "continuation_restart_max_attempts" not in accepted_locs

    rejected = routes.start_ai_invoke(continuation_restart_body(11), object())
    assert rejected.status_code == 422
    rejected_locs = {err["loc"] for err in json.loads(rejected.body)["errors"]}
    assert "continuation_restart_max_attempts" in rejected_locs

    # (c) document review loop path — the real `allowed` dict in start_ai_invoke, built
    # from ai_execution_policy_service.repeat_count_choices, for review_count and
    # failure_restart_max_attempts.
    loop_fields = {
        "reviewer_provider_id": "reviewer", "review_criteria": "document_type_default",
        "rework_provider_id": "reworker", "rework_timeout_sec": 1800,
        "rework_message": "fix every finding", "total_timeout_sec": 3600,
    }

    def review_loop_body(review_count, failure_restart_max_attempts):
        return routes.AiInvokeStartRequest(
            project="flowgate", module="default", group="0490",
            doc_ref="flowgate.default.0490.0001-T", action_scope="edit", mode="single",
            document_review_loop={
                **loop_fields,
                "review_count": review_count,
                "failure_restart_max_attempts": failure_restart_max_attempts,
            },
        )

    loop_accepted = routes.start_ai_invoke(review_loop_body(10, 10), object())
    assert loop_accepted.status_code == 422  # from action_scope != review, not these fields
    loop_accepted_locs = {err["loc"] for err in json.loads(loop_accepted.body)["errors"]}
    assert "document_review_loop.review_count" not in loop_accepted_locs
    assert "document_review_loop.failure_restart_max_attempts" not in loop_accepted_locs

    loop_rejected = routes.start_ai_invoke(review_loop_body(11, 11), object())
    assert loop_rejected.status_code == 422
    loop_rejected_locs = {err["loc"] for err in json.loads(loop_rejected.body)["errors"]}
    assert "document_review_loop.review_count" in loop_rejected_locs
    assert "document_review_loop.failure_restart_max_attempts" in loop_rejected_locs


# ── Test 4: hard max=30 boundary ──────────────────────────────────────────────────

def test_hard_max_boundary(fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "30", "integer")
    choices = policy.repeat_count_choices(allow_zero=True)
    assert choices[-1] == 30
    assert 31 not in choices
    assert policy.valid_setting_value("30") is True
    assert policy.valid_setting_value("31") is False


# ── Test 5: saving 0 is rejected end to end ───────────────────────────────────────

def test_set_values_rejects_zero(fake_settings):
    with pytest.raises(ValueError, match="ai_repeat_count_max must be an integer between 1 and 30"):
        settings_service.set_values({"ai_repeat_count_max": "0"})


def test_router_rejects_zero_with_422(fake_settings):
    with pytest.raises(HTTPException) as exc_info:
        system_router.update_settings(
            system_router.SettingsPatch(updates={"ai_repeat_count_max": "0"}),
            {"user_id": "usr_admin"},
        )
    assert exc_info.value.status_code == 422


# ── Test 6: saving 31 and other malformed values are rejected ────────────────────

@pytest.mark.parametrize("value", ["31", "abc", "", "3.5", True, None])
def test_set_values_rejects_invalid_values(fake_settings, value):
    with pytest.raises(ValueError, match="ai_repeat_count_max must be an integer between 1 and 30"):
        settings_service.set_values({"ai_repeat_count_max": value})


# ── Test 7: per-feature defaults are unaffected by the ceiling ───────────────────

def test_feature_defaults_unaffected_by_max_change(fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "10", "integer")
    assert ai_invoke_service.RESTART_MAX_ATTEMPTS_DEFAULT == 1
    assert ai_invoke_service.REVIEW_COUNT_DEFAULT == 0
    assert workflow_decision_service.REVIEW_COUNT_DEFAULT == 0


# ── Test 8: sentinel/zero semantics stay per-feature, independent of max ─────────

def test_sentinel_and_zero_semantics_are_per_feature(fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "1", "integer")
    # -1 ("될 때까지") is always accepted regardless of allow_zero or the max.
    assert policy.valid_repeat_count(-1, allow_zero=True) is True
    assert policy.valid_repeat_count(-1, allow_zero=False) is True
    # 0 is accepted only for the features that opt in (restart-style), never for the
    # document review loop's review_count (allow_zero=False there).
    assert policy.valid_repeat_count(0, allow_zero=True) is True
    assert policy.valid_repeat_count(0, allow_zero=False) is False


# ── Test 9: a fresh request beyond today's max is refused (UI-bypass direct call) ─

def test_new_request_beyond_max_is_422(monkeypatch, fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "5", "integer")
    monkeypatch.setattr(
        routes, "_require_user", lambda request: {"issued_to": "u", "_is_user_jwt": True}
    )
    body = routes.AiInvokeStartRequest(
        project="flowgate", module="default", group="0490",
        doc_ref="flowgate.default.0490.0001-T", action_scope="new", mode="single",
        continuation_restart_max_attempts=6,
    )
    response = routes.start_ai_invoke(body, object())
    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["code"] == "validation_failed"
    assert any(err["loc"] == "continuation_restart_max_attempts" for err in payload["errors"])


# ── Test 10: read and write share one ceiling — no frozen literal survives on either
# side ─────────────────────────────────────────────────────────────────────────────
#
# T0005 §3.3 asked for the read path to skip range-checking entirely (accept any
# shape-valid int) so a shrunk ceiling could never demote an already-stored pick.
# flowgate.default.0490.0006-TR rev2/rev3 review rejected every revision that carried
# that rule, because resolve_review_count/_resolve_restart_max_attempts already had
# pre-T0005 unit coverage in test_ai_invoke_review_gate_0414.py and
# test_ai_invoke_restart_count_0443.py (test_a_hand_edited_out_of_range_count_reads_
# as_no_review, test_unrecognized_value_falls_back_to_default) asserting the opposite
# — that an out-of-range read (4, at the unconfigured default max of 3) degrades to
# the feature default, not to itself. T0005 §5 forbids touching those two files, and
# there is no bound that satisfies both "4 degrades at max=3" and "15 survives at
# max=5" for the same function — one is strictly a subset check of the other. The
# review's repeated, explicit instruction ("시험이 아니라 구현을 고쳐라") was resolved by
# keeping resolve_review_count/_resolve_restart_max_attempts bounded, exactly as they
# were pre-T0005, except the bound is now read live from
# ai_execution_policy_service.repeat_count_choices() instead of a frozen
# frozenset/tuple literal. This satisfies R0001's actual ask (no hardcoded ceiling
# literal survives; the two pre-existing files stay green with zero edits) at the
# cost of NR0003 §19 R9's specific non-demotion nuance: lowering the ceiling now
# demotes an already-stored pick on read, exactly as it always did pre-T0005 — this
# is a deliberate, documented trade-off, not an oversight.
def test_read_path_tracks_the_live_ceiling_on_both_sides(fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "5", "integer")
    # WRITE: a value beyond today's ceiling is refused for a new request.
    with pytest.raises(ValueError, match="invalid_review_count_value"):
        workflow_decision_service.normalize_continuation_review_count_overrides({"5": 15})
    # READ: the same out-of-range value degrades to the feature default rather than
    # crashing an unmanned chain — symmetric with the write-path refusal, and with
    # the pre-existing hand-edited-row tests' behavior at the (lower) default ceiling.
    assert ai_invoke_service.resolve_review_count({"5": 15}, 5) == 0
    assert ai_invoke_service._resolve_restart_max_attempts(15) == 2
    # Raising the ceiling widens both sides together — nothing is hardcoded at 5.
    db_system_settings.set_value("ai_repeat_count_max", "15", "integer")
    assert ai_invoke_service.resolve_review_count({"5": 15}, 5) == 15
    assert ai_invoke_service._resolve_restart_max_attempts(15) == 16
    assert workflow_decision_service.normalize_continuation_review_count_overrides(
        {"5": 15}
    ) == {"5": 15}


# ── fail-safe: a DB failure never propagates out of get_repeat_count_max ─────────

def test_get_repeat_count_max_fail_safe_on_db_exception(monkeypatch):
    def _boom(key, default=None):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(db_system_settings, "get_value", _boom)
    assert policy.get_repeat_count_max() == 3


# ── API contract: execution_policy rides list_runtime_providers, keys unchanged ──

def test_list_runtime_providers_carries_execution_policy(monkeypatch, fake_settings):
    db_system_settings.set_value("ai_repeat_count_max", "7", "integer")
    monkeypatch.setattr(
        ai_invoke_service.ai_settings_service,
        "resolve_effective",
        lambda project_id: {"providers": [], "default_provider_id": None},
    )
    result = ai_invoke_service.list_runtime_providers("flowgate")
    assert result["ok"] is True
    assert result["project"] == "flowgate"
    assert result["providers"] == []
    assert result["default_provider_id"] is None
    assert result["execution_policy"] == {
        "repeat_count_max": 7,
        "repeat_count_min": 1,
        "repeat_count_hard_max": 30,
    }


# ── judgment/message agreement: a rejected value is never listed as a choice ─────

@pytest.mark.parametrize("max_value", [1, 5, 15, 30])
def test_choice_membership_matches_validity(fake_settings, max_value):
    db_system_settings.set_value("ai_repeat_count_max", str(max_value), "integer")
    choices = policy.repeat_count_choices(allow_zero=True)
    for candidate in (-2, -1, 0, 1, max_value, max_value + 1, 999):
        assert policy.valid_repeat_count(candidate, allow_zero=True) == (candidate in choices)
