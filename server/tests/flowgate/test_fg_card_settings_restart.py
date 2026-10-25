"""flowgate.v02.0053 T#2 — a Graph node's card restarts reach the AI invoke engine.

A Graph node starts its AI as one single 'new' run (``ai_node_starter``), and a single run was always
one-shot: the no-output retry (0359 / 0443) was continuous-only. The card's restart setting rides the start
as ``node_restart_max_attempts``; only that run opens the retry, on the same AI, with the 0443 meaning
(N restarts = N + 1 attempts, -1 until it works, 0 one-shot). Every other single run stays one-shot.

The engine is the real one with the 0359 no-database harness (its ``env`` fixture and scripted CLI worker);
the branch merge claim and the approval freeze claim, which read the database, answer "no claim" here.
"""
from __future__ import annotations

import os
import sys

import pytest
import threading
import time
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from tests.modules.flow_gate.test_ai_invoke_no_output_retry_0359 import (  # noqa: E402,F401 — the same harness, fixture included
    DOC_REF,
    GROUP,
    _scripted_worker,
    _wait_finished,
    env,
)


@pytest.fixture
def engine(env, monkeypatch):
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda group_id: None)
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim", lambda group_id: None)
    return env


def _start_node_run(**kw):
    """A Graph node's AI start exactly as ``ai_node_starter.start_ai_invoke`` makes it (single, new)."""
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=DOC_REF,
        action_scope="new",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\n",
        **kw,
    )


def test_a_node_run_retries_no_output_on_the_same_ai_by_its_card_restarts(engine, monkeypatch):
    launches = _scripted_worker(engine, monkeypatch, [("could not work", False)] * 4)
    run = _wait_finished(_start_node_run(node_restart_max_attempts=2)["run_id"])
    assert launches == ["aip_1"] * 3                                      # 1 attempt + 2 restarts, same AI
    assert (run["attempts_used"], run["attempts_max"], run["outcome"]) == (3, 3, "none")


def test_a_node_run_stops_restarting_once_its_document_lands(engine, monkeypatch):
    launches = _scripted_worker(engine, monkeypatch, [("could not work", False), ("done", True)])
    run = _wait_finished(_start_node_run(node_restart_max_attempts=2)["run_id"])
    assert launches == ["aip_1"] * 2
    assert (run["attempts_used"], run["outcome"]) == (2, "complete")


def test_zero_restarts_keep_a_node_run_one_shot(engine, monkeypatch):
    launches = _scripted_worker(engine, monkeypatch, [("could not work", False)] * 2)
    run = _wait_finished(_start_node_run(node_restart_max_attempts=0)["run_id"])
    assert launches == ["aip_1"]
    assert (run["attempts_used"], run["attempts_max"]) == (1, 1)


def test_every_other_single_run_stays_one_shot(engine, monkeypatch):
    launches = _scripted_worker(engine, monkeypatch, [("could not work", False)] * 2)
    run = _wait_finished(_start_node_run()["run_id"])                     # no card pick: as before
    assert launches == ["aip_1"]
    assert (run["attempts_used"], run["attempts_max"]) == (1, 1)


def test_only_a_run_the_pick_opened_is_eligible():
    run = {"mode": "single", "action_scope": "new", "cancel_event": threading.Event(), "end_reason": "exited",
           "pause_requested": False, "completion_oracle": None, "docs_target": 1, "docs_reached": 0,
           "attempts_used": 1, "attempts_max": 3, "group_id": GROUP, "started_mono": time.monotonic(),
           "timeout_sec": 3600, "outcome": "none"}
    assert svc._retry_eligible(run) is False
    assert svc._retry_eligible({**run, "node_restart_open": True}) is True
    assert svc._retry_eligible({**run, "node_restart_open": True, "attempts_used": 3}) is False
