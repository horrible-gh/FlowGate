"""flowgate.v02test.0107 F107-14: GET /ai-invoke/{run_id} says whether the run went to a remote Agent.

The chat's dying-message box is for the remote CLI path only (0028-T decision 6). `api_turns_used`
only separates API runs from CLI runs, so the detail now carries a read-only `remote_cli` marker:
true when the run carried a `remote_job` (live record or persisted row), false for a server-local
CLI run and an API run. Nothing is stored for it.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.v02test.0107"
PROJECT = "flowgate"
DOC_REF = "flowgate.v02test.0107.0001-C"
REMOTE_JOB = {"provider_id": "aip_remote", "target_agent_id": "agt_a", "claimed_agent_id": "agt_a",
              "job_id": "job_1", "attempt_id": "1", "status": "SUCCEEDED", "terminal_cause": "agent_report",
              "failure_code": None, "cancel_reason": None}


def _stored_row(run_id, **extra):
    row = {
        "run_id": run_id, "group_id": GROUP, "project_id": PROJECT, "doc_ref": DOC_REF,
        "mode": "single", "status": "finished", "outcome": "complete", "docs_reached": 0,
        "docs_target": 1, "reached_doc_ids": [], "end_reason": "exited", "stop_code": None,
        "stop_reason": None, "resumable": False, "exit_code": 0,
        "last_message": "the last words", "last_message_excerpt": "the last words",
        "provider_id": "aip_1", "provider_name": "cli-1", "attempt_no": 1, "attempts_used": 1,
        "attempts_max": 1, "fallback_history": [], "register_errors": [], "tool_call_misses": 0,
        "turn_limit_exhausted": False, "oracle_mismatch": False, "source_dirty": False,
        "scratch_retained": None, "hop_item_seq": 1, "token_id": None, "issued_to": "usr_1",
        "started_at": "2026-11-28T09:00:00+09:00", "finished_at": "2026-11-28T09:05:00+09:00",
        "duration_ms": 300000, "timeout_sec": 3600, "deadline_at": None,
    }
    row.update(extra)
    return row


def _finished_live_run(run_id, **extra):
    run = defaultdict(lambda: None)
    run.update({
        "run_id": run_id, "status": "finished", "mode": "single", "group_id": GROUP,
        "project_id": PROJECT, "doc_ref": DOC_REF, "outcome": "complete", "end_reason": "exited",
        "docs_reached": 0, "docs_target": 1, "reached_doc_ids": [], "last_message_received": True,
        "last_message": "the last words", "provider_id": "aip_1", "provider": {"name": "cli-1"},
        "started_at": "2026-11-28T09:00:00+09:00", "finished_at": "2026-11-28T09:05:00+09:00",
        "attempt_no": 1, "fallback_history": [], "source_dirty": False, "scratch_retained": None,
        "duration_ms": 300000,
    })
    run.update(extra)
    return run


@pytest.fixture
def runs(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    rows: dict[str, dict] = {}
    monkeypatch.setattr(db_runs, "get", lambda run_id: dict(rows[run_id]) if run_id in rows else None)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: None)
    return rows


def test_persisted_remote_run_is_marked(runs):
    runs["aiv_remote"] = _stored_row("aiv_remote", remote_job=dict(REMOTE_JOB))
    assert svc.get_run_detail("aiv_remote")["remote_cli"] is True


def test_persisted_local_cli_run_is_not_marked(runs):
    # `_remote_job_record` stores NULL for a run that never used a remote Agent.
    runs["aiv_local"] = _stored_row("aiv_local", remote_job=None)
    runs["aiv_old"] = _stored_row("aiv_old")  # a row from before the remote_job column
    assert svc.get_run_detail("aiv_local")["remote_cli"] is False
    assert svc.get_run_detail("aiv_old")["remote_cli"] is False


def test_persisted_api_run_is_not_marked(runs):
    runs["aiv_api"] = _stored_row("aiv_api", api_turns_used=3, remote_job=None)
    payload = svc.get_run_detail("aiv_api")
    assert payload["remote_cli"] is False and payload["api_turns_used"] == 3


def test_live_runs_are_marked_from_memory(runs, monkeypatch):
    remote = _finished_live_run("aiv_live_remote", remote_job=dict(REMOTE_JOB, workdir="/w"))
    local = _finished_live_run("aiv_live_local")
    monkeypatch.setattr(svc, "_runs", {remote["run_id"]: remote, local["run_id"]: local})
    assert svc.get_run_detail("aiv_live_remote")["remote_cli"] is True
    assert svc.get_run_detail("aiv_live_local")["remote_cli"] is False
