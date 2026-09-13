import json

import pytest

from modules.flow_gate.services.ai_invoke import worker


DOC = "flowgate.default.0545.0009-TR"
PROJECT = "flowgate"


def _run():
    return {
        "run_id": "aiv_0545_contract",
        "doc_ref": DOC,
        "project_id": PROJECT,
        "group_id": "flowgate.default.0545",
        "module": "default",
        "locale": "ko",
        "provider_id": "provider-test",
        "transport_api_base": "http://flowgate.test/api/v1",
    }


@pytest.fixture
def bound(monkeypatch):
    context = {
        "action": "review", "project": PROJECT,
        "group": "flowgate.default.0545", "doc": DOC,
    }
    monkeypatch.setattr(worker, "_bind_register_context", lambda *_a: (context, {"token_id": "tok"}))
    return context


def test_review_preflight_and_submit_preserve_semantic_payload_and_unicode(monkeypatch, bound):
    calls = []
    review = {
        "verdict": "issues",
        "findings": [{"locus": "본문", "note": "한글 日本語 English 😀"}],
        "comment": "검수 완료 日本語 😀",
        "body_sha256": "a" * 64,
        "body_chars": 12,
        "force_encoding_reason": "의도된 물음표 문장입니다",
    }

    def post(_run_arg, _token, body):
        calls.append(body)
        if body.get("dry_run"):
            return 200, {
                "ok": True, "receipt": "receipt-1", "payload_identity": "identity-1",
                "validated_at": "2026-09-11T00:00:00Z",
                "validation": {"fingerprint_supplied": True, "fingerprint_matched": True},
            }
        return 201, {"ok": True, "revision_no": 3}

    monkeypatch.setattr(worker, "_post_inbox_json", post)
    run = _run()
    status, result = worker._inbox_register(run, "raw", review)

    assert status == 201 and result["ok"] is True
    assert len(calls) == 2
    dry, real = calls
    for key in ("action", "project", "module", "group_name", "doc_id",
                "verdict", "findings", "comment", "body_sha256", "body_chars",
                "force_encoding_reason"):
        assert dry[key] == real[key]
    assert dry["dry_run"] is True and "receipt" not in dry
    assert real["receipt"] == "receipt-1" and "dry_run" not in real
    assert "한글 日本語 English 😀" in json.dumps(real, ensure_ascii=False)
    assert run["review_submission"]["submitted"] is True
    assert run["review_submission"]["receipt"] == "receipt-1"


@pytest.mark.parametrize("status,response", [
    (422, {"code": "encoding_corruption", "validation": {"corruption_detected": True}}),
    (200, {"ok": True}),
    (0, {"error": "transport failed"}),
])
def test_review_preflight_failure_never_attempts_real_submit(monkeypatch, bound, status, response):
    calls = []

    def post(_run_arg, _token, body):
        calls.append(body)
        return status, response

    monkeypatch.setattr(worker, "_post_inbox_json", post)
    run = _run()
    got_status, got_response = worker._inbox_register(
        run, "raw", {"verdict": "pass", "comment": "깨진 입력"},
    )

    assert (got_status, got_response) == (status, response)
    assert len(calls) == 1 and calls[0]["dry_run"] is True
    assert run["review_submission"]["submitted"] is False


def test_review_payload_is_not_reassembled_between_requests(monkeypatch, bound):
    calls = []
    envelope_calls = []

    def envelope(_context, _run_arg, _tool_input):
        # Each call is distinguishable (call-counted comment) so a regression that
        # reassembles the envelope between the dry-run and real requests is caught
        # even though `_inbox_register` otherwise only reuses `dict(body)`.
        envelope_calls.append(1)
        return {
            "action": "review", "project": PROJECT, "module": "default",
            "group_name": "flowgate.default.0545", "doc_id": DOC,
            "verdict": "pass", "findings": [], "comment": f"원문 😀 #{len(envelope_calls)}",
        }

    # `_inbox_register` calls `_svc()._register_envelope(...)`, and `_svc()` resolves
    # to the live `ai_invoke_service` module object (worker.runtime._svc(), 0545
    # TR0011 review finding). `ai_invoke_service._register_envelope` is a name copied
    # in from `facade` via `from .facade import *` at that module's first import, so
    # patching `worker._register_envelope` directly never reaches the call site once
    # any other test in the process has already imported `ai_invoke_service`. Patch
    # the attribute on the actual module `_svc()` returns instead.
    monkeypatch.setattr(worker._svc(), "_register_envelope", envelope)
    monkeypatch.setattr(
        worker, "_post_inbox_json",
        lambda _r, _t, body: calls.append(body) or (
            (200, {"receipt": "r"}) if body.get("dry_run") else (201, {"ok": True})
        ),
    )
    worker._inbox_register(_run(), "raw", {"comment": "ignored after assembly"})
    assert len(envelope_calls) == 1
    semantic = lambda body: {k: v for k, v in body.items() if k not in {"dry_run", "receipt"}}
    assert semantic(calls[0]) == semantic(calls[1])
