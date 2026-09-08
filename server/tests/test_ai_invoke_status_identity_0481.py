"""flowgate.default.0481 T0010 rev3 — the run identity fields the browser polls on.

`ai_invoke_started` (the SSE frame) has shipped `project_id` + `action_scope` since
T0010 #1, and the browser reads both: `payloadGroupKey` files a `resolve_base_dirty`
card under `project:<id>`, and MainPanel's `activeGitOwnRun` keeps a
`resolve_conflict` run's own screen mounted instead of covering it.

`GET /ai-invoke/{run_id}` is the SAME registry read on the reload/poll path, and the
store rebuilds every live entry from it every 5 seconds (`refresh` -> `trackStarted`).
While these two keys were missing from it, both facts survived the start frame and
then vanished on the first poll — the card moved to a group nobody watches, and the
merge dialog was torn down moments after it was saved. Ship them from both places or
neither.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services.ai_invoke import diagnostics  # noqa: E402


class _FakeService:
    def __init__(self, run):
        self._run = run

    def get_run_record(self, run_id):
        return self._run if run_id == self._run["run_id"] else None

    def _oracle_new_docs(self, _run):
        return []

    def _open_q_doc_ids(self, _group_id):
        return []

    def document_review_loop_payload(self, _run):
        return None

    def finished_payload(self, run):
        return {"outcome": run.get("outcome")}


def _run_record(**overrides):
    run = {
        "run_id": "aiv_20260908_000002",
        "group_id": "test2.default.0009",
        "project_id": "test2",
        "action_scope": "resolve_conflict",
        "doc_ref": "",
        "mode": "single",
        "status": "running",
        "docs_target": 1,
        "provider": {"id": "p1", "name": "Claude Haiku 4.5"},
        "provider_id": "p1",
        "attempt_no": 1,
        "started_at": "2026-09-08T10:32:13+09:00",
        "started_mono": 0.0,
        "completion_oracle": "scoped",
        "chain_id": None,
        "chain_docs_target": 0,
        "chain_docs_reached": 0,
        "chain_docs_accounted": True,
    }
    run.update(overrides)
    return run


def _status(monkeypatch, run):
    monkeypatch.setattr(diagnostics, "_svc", lambda: _FakeService(run))
    return diagnostics.get_status(run["run_id"])


def test_live_status_carries_the_scope_the_browser_branches_on(monkeypatch):
    payload = _status(monkeypatch, _run_record())

    assert payload["action_scope"] == "resolve_conflict"
    assert payload["project_id"] == "test2"
    # The group identity it always had must not move.
    assert payload["group_id"] == "test2.default.0009"


def test_live_status_carries_both_identities_of_a_project_scoped_run(monkeypatch):
    # resolve_base_dirty runs under a synthetic `<project>.none.0000` group; the card
    # lives under `project:<id>`, which is only derivable from these two fields.
    payload = _status(monkeypatch, _run_record(
        group_id="test2.none.0000", action_scope="resolve_base_dirty",
    ))

    assert (payload["project_id"], payload["action_scope"]) == ("test2", "resolve_base_dirty")


def test_a_run_without_a_scope_reports_none_rather_than_raising(monkeypatch):
    run = _run_record()
    run.pop("action_scope")
    run.pop("project_id")

    payload = _status(monkeypatch, run)

    assert payload["action_scope"] is None
    assert payload["project_id"] is None
