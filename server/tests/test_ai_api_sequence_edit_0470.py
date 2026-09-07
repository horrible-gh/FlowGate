"""API-provider workflow sequence edit registration regression coverage (0470 T0007)."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services.ai_invoke import worker  # noqa: E402


def _run():
    return {
        "project_id": "flowgate", "run_id": "aiv_sequence", "docs_target": 0,
        "raw_token": "raw-sequence-token", "token_id": "tok_sequence",
        "doc_ref": "flowgate.default.0470.0001-B",
        "action_scope": "workflow_sequence_edit", "mode": "single",
        "cancel_event": threading.Event(), "provider": {"name": "API"},
        "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1",
        "timed_out": False,
    }


def _provider():
    return {"id": "provider", "kind": "openai",
            "api_base_url": "https://api.example", "api_model": "test"}


def test_api_sequence_edit_uses_dedicated_tool_and_never_inbox(monkeypatch):
    seen = {}
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)

    def fake_call(*args):
        seen["tool_name"] = args[-4]
        seen["schema"] = args[-2]
        return "", {"id": "call_1", "input": {
            "items": [{"type": "T", "label": "Implement", "note": None}],
            "expected_workflow_tag": "tag-1",
        }}, {"role": "assistant"}

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    monkeypatch.setattr(worker, "_sequence_edit_register", lambda run, token, payload: (
        seen.update({"register": (run, token, payload)}) or (200, {"ok": True})
    ))
    monkeypatch.setattr(svc, "_inbox_register",
                        lambda *_: pytest.fail("sequence edit must not call /inbox"))

    run = _run()
    assert svc._api_execute(_provider(), "prompt", run) == ("started_ok", None)
    assert seen["tool_name"] == "save_workflow_sequence"
    assert "doc_id" not in seen["schema"]["properties"]
    assert seen["register"][1:] == ("raw-sequence-token", {
        "items": [{"type": "T", "label": "Implement", "note": None}],
        "expected_workflow_tag": "tag-1",
    })
    assert run["last_tool_name"] == "sequence_edit_register"
    assert run["last_tool_status"] == 200
    assert run["last_tool_error"] is None


def test_sequence_edit_request_injects_bound_doc_and_raw_token(monkeypatch):
    captured = {}

    def fake_bound(run, token, path, method="GET", body=None):
        captured.update(run=run, token=token, path=path, method=method, body=body)
        return 200, {"ok": True}

    monkeypatch.setattr(worker, "_api_bound_request", fake_bound)
    run = _run()
    status, payload = worker._sequence_edit_register(
        run, "current-raw-token",
        {"items": [{"type": "T", "label": "Implement"}],
         "force_encoding_reason": "legacy"},
    )
    assert (status, payload) == (200, {"ok": True})
    assert captured["token"] == "current-raw-token"
    assert captured["path"] == "/workflow/sequence"
    assert captured["method"] == "PATCH"
    assert captured["body"] == {
        "doc_id": run["doc_ref"],
        "items": [{"type": "T", "label": "Implement"}],
        "force_encoding_reason": "legacy",
    }


def test_api_sequence_edit_failure_is_recorded_and_returned_for_retry(monkeypatch):
    conversations = []
    attempts = iter([(409, {"error": "stale sequence"}), (200, {"ok": True})])
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)

    def fake_call(*args):
        conversations.append(list(args[3]))
        return "", {"id": f"call_{len(conversations)}", "input": {"items": []}}, {
            "role": "assistant"
        }

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    monkeypatch.setattr(worker, "_sequence_edit_register", lambda *_: next(attempts))

    run = _run()
    assert svc._api_execute(_provider(), "prompt", run) == ("started_ok", None)
    assert run["register_errors"] == [
        {"status": 409, "reason": "stale sequence", "turn": 1}
    ]
    assert "Workflow sequence registration failed (HTTP 409)" in json.dumps(
        conversations[1], ensure_ascii=False
    )
    assert run["last_tool_status"] == 200
    assert run["last_tool_error"] is None


# ── 0007-T 완료기준 (d): the real PATCH route, the real store, the real verdict ──
#
# The three tests above pin the wiring INSIDE the worker (which tool is exposed, what body
# the handler builds, how a failure is recorded) and stub the transport to do it. That is
# deliberately not enough for completion criterion (d): it asks for the same judgment
# test_ai_invoke_parallel_scopes_0268.py::TestPerfectWorkerSucceeds::
# test_sequence_edit_worker_that_replaces_the_tail_is_complete makes for the CLI path —
# `_probe_sequence_max_item` reading the sequence store and settling outcome == 'complete' —
# for the API path. So nothing between the model's tool call and the store is stubbed here:
# `_sequence_edit_register`, `_api_bound_request`, the PATCH /workflow/sequence route with its
# workflow_sequence_edit token guard, and `edit_workflow_pending` all run for real. Only the
# two ends are faked — the model call (`_call_openai`) and the DB rows (the same
# monkeypatched-collaborators style as 0268/0406) — and the DB fake is ONE state list shared
# by the writer (workflow_decision_service) and the judge (`_probe_sequence_max_item`), so a
# route/store miswiring shows up as a wrong verdict instead of staying green.

import io  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from contextlib import contextmanager  # noqa: E402

os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.flow_gate.api.v1 import workflow_decision_routes  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wf_svc  # noqa: E402
from routers.main import app  # noqa: E402

ORACLE_GROUP = "flowgate.default.0470"
ORACLE_ROOT = "flowgate.default.0470.0011-B"
ORACLE_TOKEN_ID = "tok_20260906_000011"
ORACLE_RAW_TOKEN = "raw-sequence-oracle-token"
API_PREFIX = "/flowgate/api/v1"

# A decided sequence: one realized (locked) step and one pending tail, so the probe's
# baseline is max_item_seq == 2 — the same starting shape 0268's FakeWorld uses.
ORACLE_INITIAL = [
    {"id": 1, "item_seq": 1, "type": "T", "label": "Done step", "doc_class": "B",
     "sort_order": 1, "status": "done", "result_doc_id": "flowgate.default.0470.0012-T",
     "note": None, "source_doc_id": None, "source_revision_no": None,
     "provider_id": None, "provider_display_name": None},
    {"id": 2, "item_seq": 2, "type": "N", "label": "Old tail", "doc_class": "B",
     "sort_order": 2, "status": "pending", "result_doc_id": None,
     "note": None, "source_doc_id": None, "source_revision_no": None,
     "provider_id": None, "provider_display_name": None},
]


class _MemoryStore:
    @contextmanager
    def transaction(self):
        yield


class _FakeHTTPResponse:
    """The three attributes `_api_bound_request` uses off urlopen()'s return value."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def oracle_world(monkeypatch, tmp_path):
    """One sequence-item list, written by the real route and read by the real probe."""
    state = [dict(row) for row in ORACLE_INITIAL]
    sequence = {"id": 470, "doc_id": ORACLE_ROOT, "head_advanced_at": None}
    owner = {
        "doc_id": ORACLE_ROOT, "type_code": "B", "seq": 11, "title": "Root",
        "project_id": "flowgate", "group_id": ORACLE_GROUP, "branch": "main",
        "revision_no": 0, "status": "open", "doc_review_status": "wf_in_progress",
    }

    # ── the sequence store: db_wfseq is ONE module object, so the writer
    # (workflow_decision_service) and the judge (oracle._probe_sequence_max_item) see
    # exactly the same rows. That sharing is the point of this fixture.
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: dict(sequence) if doc_id == ORACLE_ROOT else None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items",
                        lambda seq_id: [dict(row) for row in state])
    monkeypatch.setattr(svc.db_wfseq, "get_max_item_seq",
                        lambda seq_id: max((row["item_seq"] for row in state), default=0))
    monkeypatch.setattr(
        svc.db_wfseq, "delete_pending_items",
        lambda seq_id: state.__setitem__(
            slice(None), [r for r in state if r["status"] != "pending"]),
    )

    def insert_sequence_item(**values):
        state.append({
            "id": 100 + len(state), "item_seq": values["item_seq"], "type": values["type_"],
            "label": values["label"], "doc_class": values["doc_class"],
            "sort_order": values["sort_order"], "status": "pending", "result_doc_id": None,
            "note": values["note"], "source_doc_id": values["source_doc_id"],
            "source_revision_no": values["source_revision_no"],
            "provider_id": values["provider_id"],
            "provider_display_name": values["provider_display_name"],
        })

    monkeypatch.setattr(svc.db_wfseq, "insert_sequence_item", insert_sequence_item)
    monkeypatch.setattr(wf_svc, "get_store", lambda: _MemoryStore())

    # ── documents (db_docs and wf_svc.db_documents are the same module object too)
    monkeypatch.setattr(svc.db_docs, "get_by_id",
                        lambda doc_id: dict(owner) if doc_id == ORACLE_ROOT else None)
    monkeypatch.setattr(svc.db_docs, "update", lambda doc_id, values: None)
    monkeypatch.setattr(svc.db_docs, "delete", lambda doc_id: None)
    monkeypatch.setattr(svc.db_docs, "list_documents", lambda **kwargs: [])
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 11)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [dict(owner)])
    monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda token_id: {
        "token_id": token_id, "doc_ref": ORACLE_ROOT,
        "action_scope": "workflow_sequence_edit",
    })

    # ── the run harness (0268's fixture, with an api provider instead of a cli one)
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, "source": "test", "providers": [{
            "id": "p1", "name": "fake-api", "exec_type": "api", "kind": "openai",
            "api_base_url": "https://api.example", "api_model": "test-model",
        }]},
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
    issued: dict = {}

    def _issue(**kw):
        issued.update(kw)
        return {
            "raw_token": ORACLE_RAW_TOKEN, "token_id": ORACLE_TOKEN_ID,
            "expires_at": "2026-09-07T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        }

    monkeypatch.setattr(svc.token_service, "issue", _issue)
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)

    # The group AI lease is REAL here (start_run acquires one, and the mutation-policy
    # middleware every PATCH passes through checks it). It resolves the caller by looking
    # this hop's raw token up, so the stand-in token store must answer with the same four
    # axes the run leased under -- token_id / ai_run_id / action_scope / group_id. Getting
    # them wrong is exactly the GROUP_AI_RUN_OWNER_MISMATCH 403 a worker cannot self-heal,
    # so the ownership check is left running rather than patched away.
    monkeypatch.setattr(svc.token_service, "inspect_for_replay", lambda raw: {
        "token_id": ORACLE_TOKEN_ID, "doc_ref": ORACLE_ROOT,
        "action_scope": "workflow_sequence_edit", "group_id": ORACLE_GROUP,
        "issued_to": "usr_admin", "ai_run_id": issued.get("ai_run_id"),
    } if raw == ORACLE_RAW_TOKEN else {})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative",
                        lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)

    # ── the token gate the worker must pass: only THIS hop's raw token is accepted, and
    # the route's own workflow_sequence_edit/doc_ref guard (routes:905-916) runs unchanged.
    def _verify_bearer(request):
        header = request.headers.get("authorization") or ""
        raw = header[7:] if header.lower().startswith("bearer ") else ""
        if raw != ORACLE_RAW_TOKEN:
            return JSONResponse(status_code=401, content={"error": "invalid_token"})
        return {
            "token_id": ORACLE_TOKEN_ID, "doc_ref": ORACLE_ROOT,
            "action_scope": "workflow_sequence_edit", "issued_to": "usr_admin",
            "group_id": ORACLE_GROUP, "ai_run_id": issued.get("ai_run_id"),
        }

    monkeypatch.setattr(workflow_decision_routes, "verify_bearer", _verify_bearer)
    return state


def _drive_api_sequence_edit(monkeypatch, tool_input: dict) -> tuple[dict, list]:
    """Run one api-provider workflow_sequence_edit hop end to end and return its record."""
    calls: list[tuple[str, str]] = []
    client = TestClient(app, raise_server_exceptions=False)
    emitted = {"n": 0}

    def fake_call(*args):
        emitted["n"] += 1
        if emitted["n"] > 1:  # after the tool call, the model just talks
            return "done", None, {"role": "assistant", "content": "done"}
        return "", {"id": "call_1", "name": args[5], "input": dict(tool_input)}, {
            "role": "assistant",
        }

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    monkeypatch.setattr(svc, "_call_anthropic", fake_call)

    def fake_urlopen(req, timeout=None):
        """Self-HTTP into the very app that serves PATCH /workflow/sequence."""
        path = urllib.parse.urlsplit(req.full_url).path
        assert path.startswith(API_PREFIX), path
        calls.append((req.get_method(), path))
        response = client.request(
            req.get_method(), path, content=req.data,
            headers={key: value for key, value in req.header_items()},
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url, response.status_code, "error", response.headers,
                io.BytesIO(response.content),
            )
        return _FakeHTTPResponse(response.status_code, response.content)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    res = svc.start_run(
        project_id="flowgate", module="default", group_id=ORACLE_GROUP,
        doc_ref=ORACLE_ROOT, action_scope="workflow_sequence_edit", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="usr_admin", api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\nedit the sequence\n",
    )
    for _ in range(500):
        record = svc.get_run_record(res["run_id"])
        if record and record["status"] == "finished":
            return record, calls
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_api_sequence_edit_that_replaces_the_tail_lands_in_the_store_and_is_complete(
    oracle_world, monkeypatch,
):
    run, calls = _drive_api_sequence_edit(monkeypatch, {
        "items": [{"type": "T", "label": "New tail", "note": "실제 저장 확인"}],
    })

    # The write actually went through the HTTP route this scope's token is minted for.
    assert calls == [("PATCH", API_PREFIX + "/workflow/sequence")]
    assert run["last_tool_name"] == "sequence_edit_register"
    assert run["last_tool_status"] == 200

    # The store the probe reads really changed: the old pending tail is gone, the model's
    # row landed, and the server attached the T's report row (expand_steps_with_reports).
    assert [(row["type"], row["status"]) for row in oracle_world] == [
        ("T", "done"), ("T", "pending"), ("TR", "pending"),
    ]
    assert oracle_world[1]["label"] == "New tail"
    assert oracle_world[1]["note"] == "실제 저장 확인"
    assert svc._probe_sequence_max_item(ORACLE_ROOT) == 4 > 2  # baseline was 2

    # 0007-T 완료기준 (d): the same verdict the CLI path gets in 0268.
    assert run["docs_target"] == 0, "a sequence edit registers no document"
    assert run["outcome"] == "complete"
    assert run["oracle_mismatch"] is False


def test_api_sequence_edit_that_inserts_nothing_is_still_judged_none(
    oracle_world, monkeypatch,
):
    """The counterpart 0268 pins for CLI: a 200 that inserted no row is not 'complete'.

    Without it, the test above could pass on a stubbed store — docs_target 0 makes
    `docs_reached >= docs_target` trivially true, which is the false positive
    `_probe_sequence_max_item` exists to prevent (0259 B0001's rule).
    """
    run, calls = _drive_api_sequence_edit(monkeypatch, {"items": []})

    assert calls == [("PATCH", API_PREFIX + "/workflow/sequence")]
    assert run["last_tool_status"] == 200, "the shrink is accepted; only the verdict differs"
    assert [row["type"] for row in oracle_world] == ["T"], "pending tail dropped, nothing inserted"
    assert svc._probe_sequence_max_item(ORACLE_ROOT) == 1
    assert run["outcome"] == "none"
    assert run["oracle_mismatch"] is True
