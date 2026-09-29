"""flowgate.v02.0014 HOLD 2.2 - the reworker chosen at Start is fail-closed.

A concrete reworker Provider captured at Start (`reworker_provider_id`) used to be dropped
silently when it was deleted/disabled by the time a review rejection needed the rework hop: the
hop fell back to the step executor. The user chose that Provider on purpose, so the hop is now
refused with `reworker_unavailable` (resumable, notifying) and no other Provider is substituted.
`null`/empty still means "unspecified" and keeps the fallback chain.

Pause/resume of the snapshot itself (paused row column -> resume bundle) is already pinned by
test_ai_invoke_pause_resume_0252.py and test_ai_groups_0014.py; this file pins the refusal, the
stop-code registry and the auto-resume park path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services.ai_invoke import chain, finalize, review, runtime  # noqa: E402

GROUP = "flowgate.v02.0014"
ROOT = "flowgate.v02.0014.0001-R"
GATE = {"slot": {"doc_id": ROOT, "item_seq": 2}}


def _bundle(**over):
    bundle = {"doc_ref": ROOT, "locale": "ko", "issued_to": "usr_admin",
              "api_base_url": "http://127.0.0.1:1/flowgate/api/v1",
              "base_provider_id": "aip_header", "work_executor_provider_id": "aip_nr",
              "reworker_provider_id": "aip_opus"}
    bundle.update(over)
    return bundle


@pytest.fixture
def spawned(monkeypatch):
    calls = {"start_run": [], "resolve": []}
    monkeypatch.setattr(svc, "start_run", lambda **kw: calls["start_run"].append(kw) or {"run_id": "rework"})

    def _resolve(bundle, item_seq, project_id, doc_ref):
        calls["resolve"].append((item_seq, project_id, doc_ref))
        return "aip_step_executor"
    monkeypatch.setattr(review, "resolve_step_executor", _resolve)
    return calls


def _stored(monkeypatch, row=None, error=None):
    from modules.flow_gate.db import ai_providers

    def _get_row(_project, _provider_id):
        if error:
            raise error
        return row
    monkeypatch.setattr(ai_providers, "get_row", _get_row)


@pytest.mark.parametrize("row,reason", [
    (None, "provider_not_found"),
    ({"id": "aip_opus", "enabled": False}, "provider_disabled"),
    ({"id": "aip_opus", "enabled": True}, "provider_not_effective"),
])
def test_unavailable_start_time_reworker_is_refused_not_substituted(monkeypatch, spawned, row, reason):
    monkeypatch.setattr(review, "_provider_enabled", lambda _project, _pid: False)
    _stored(monkeypatch, row)
    with pytest.raises(HTTPException) as caught:
        review._spawn_rework_hop(GROUP, _bundle(), GATE)
    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail["code"] == runtime.REWORKER_UNAVAILABLE_STOP_CODE == "reworker_unavailable"
    assert detail["reworker_provider_id"] == "aip_opus"
    assert detail["reason"] == reason
    assert spawned["start_run"] == [], "a rework hop was launched on another Provider"
    assert spawned["resolve"] == [], "the step-executor fallback was consulted"


def test_reason_lookup_failure_still_fails_closed(monkeypatch, spawned):
    monkeypatch.setattr(review, "_provider_enabled", lambda _project, _pid: False)
    _stored(monkeypatch, error=RuntimeError("db down"))
    with pytest.raises(HTTPException) as caught:
        review._spawn_rework_hop(GROUP, _bundle(), GATE)
    assert caught.value.detail["reason"] == "provider_not_effective"
    assert spawned["start_run"] == []


@pytest.mark.parametrize("unspecified", [None, ""])
def test_unspecified_reworker_keeps_the_fallback_chain(monkeypatch, spawned, unspecified):
    monkeypatch.setattr(review, "_provider_enabled", lambda _project, _pid: False)
    review._spawn_rework_hop(GROUP, _bundle(reworker_provider_id=unspecified), GATE)
    assert spawned["resolve"] == [(2, "flowgate", ROOT)]
    assert spawned["start_run"][0]["provider_id"] == "aip_step_executor"


def test_effective_reworker_is_the_one_that_runs(monkeypatch, spawned):
    monkeypatch.setattr(review, "_provider_enabled", lambda _project, pid: pid == "aip_opus")
    review._spawn_rework_hop(GROUP, _bundle(), GATE)
    assert spawned["start_run"][0]["provider_id"] == "aip_opus"
    assert spawned["start_run"][0]["continuation_reworker_provider_id"] == "aip_opus"
    assert spawned["resolve"] == []


def test_reworker_unavailable_is_resumable_and_announced_and_explained():
    code = runtime.REWORKER_UNAVAILABLE_STOP_CODE
    assert code in runtime.RESUMABLE_STOP_CODES
    assert finalize.is_resumable(code)
    assert code in runtime.ENGINE_NOTIFY_STOP_CODES
    assert code in runtime.PARK_NOTIFY_STOP_CODES
    assert code not in runtime.INBOX_NOTIFY_STOP_CODES
    text = finalize._stop_reason_text(code, {"continuation_reworker_provider_id": "aip_opus"})
    assert "aip_opus" in text and "resume" in text.lower()
    assert "unknown" in finalize._stop_reason_text(code, {})


def _drive_auto_resume(monkeypatch, gate_error):
    parked = []
    queued = {"doc_ref": ROOT, "reworker_provider_id": "aip_opus"}
    run = {"group_id": GROUP, "run_id": "aiv_test", "end_reason": "exited", "stop_code": None,
           "cancel_event": None, "continuation_reworker_provider_id": "aip_opus"}
    svc._auto_resume[GROUP] = queued
    monkeypatch.setattr(svc, "_write_handoff_row", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_park_handoff", lambda _run, pending, code: parked.append((code, pending)))

    def _gate(*_args, **_kwargs):
        raise gate_error
    monkeypatch.setattr(chain.review, "run_review_gate", _gate)
    try:
        chain._maybe_auto_resume_hop(run)
    finally:
        svc._auto_resume.pop(GROUP, None)
    return parked


def test_auto_resume_parks_on_reworker_unavailable_and_keeps_the_snapshot(monkeypatch):
    error = HTTPException(409, {"code": "reworker_unavailable", "message": "gone",
                                "reworker_provider_id": "aip_opus", "reason": "provider_disabled"})
    parked = _drive_auto_resume(monkeypatch, error)
    assert [code for code, _ in parked] == ["reworker_unavailable"]
    assert parked[0][1]["reworker_provider_id"] == "aip_opus"


@pytest.mark.parametrize("error", [
    HTTPException(409, {"code": "something_else"}),
    HTTPException(500, "plain string detail"),
])
def test_other_handoff_rejections_still_park_as_hop_handoff_failed(monkeypatch, error):
    parked = _drive_auto_resume(monkeypatch, error)
    assert [code for code, _ in parked] == [runtime.HOP_HANDOFF_FAILED_STOP_CODE]
