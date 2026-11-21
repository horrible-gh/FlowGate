"""flowgate.v02.0085 T#3 rework (review rej_01M5NT4F9CPN06M6): one focused regression test per defect of the
review, on the ``gate_publish`` harness and a real Git repository.

flowgate.v02.0098 T#2 (TR0009): moved unchanged from ``test_fg_gate_publish_0085``, whose harness, fixtures
and shared helpers it imports. ``--dist loadfile`` gives one file to one worker, and the single file's serial
time alone exceeded the 1800 s Self-check limit.
From the end cleanup under its locks on, the cases are in ``test_fg_gate_publish_rework_recovery_0085``
for the same reason.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _h, _sql, db_path, env  # noqa: E402,F401
from test_fg_gate_intake_0085 import _finish, _gate_events, _start, _states, product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    PROJECT, _git, _rev, _work, _gate, _run_job, _expire_and_reconcile, _decision, _effects, _job_row, _merges,
    _confirm, _act, _version, _requests, _start_linear, _evidence, _run_cleanup, _flow, _cleanups, _run_status,
    _parked_after_merge, _parked_without_effect, _side_commit, _second_publish, _completed_merge_flow, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


# ── T#3 rework: the review's defects, each with its focused regression test ──

def test_a_groups_gate_jobs_run_in_acceptance_order_and_hold_the_integration_until_their_pin(git_env):
    """D0007 §7.3: one group's publishes run in their acceptance order, and a result integrated after an acceptance
    never enters that decision's snapshot (the integration waits for the pin, then becomes the next one's)."""
    from flowgate.graph_run import result_merge
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "o1")
    did1, job1 = body["decision"]["decision_id"], body["job"]["job_id"]
    did2, job2 = _second_publish(env, run_id)
    seqs = {j["job_id"]: json.loads(j["payload"])["group_seq"] for j in _sql(
        env["db"], "SELECT job_id, payload FROM operation_job WHERE kind = 'gate_publish'")}
    assert seqs == {job1: 1, job2: 2}
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job2])
    assert job_runner.try_claim(job2) is None                                 # the earlier job is unfinished
    assert job_store.gate_publish_predecessor(job_store.get_job(job2))["job_id"] == job1
    side = _side_commit(r, sha, "late.txt")
    row = {"group_id": FA, "run_id": run_id, "node_key": t, "attempt_id": "att_side_1", "result_commit": side}
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    assert _rev(r["wt"], "HEAD") == sha                                       # nothing integrated
    job, _ = _run_job(job1)
    assert job["status"] == "succeeded" and _decision(env, did1)["frozen_sha"] == sha
    # the second decision still holds the group branch until its own pin
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    job, _ = _run_job(job2)                                                   # now its turn: it pins HEAD
    assert _decision(env, did2)["frozen_sha"] == sha
    assert job["status"] == "recovery_required" and job["last_error_code"] == "gate_not_open"   # a stand-in gate
    status, merged = result_merge._merge(row)                                 # the pins are taken: it goes on
    assert status == result_merge.INTEGRATED and _rev(r["wt"], "HEAD") == merged != sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did1}") == sha       # the snapshots stay as they were
    assert _decision(env, did1)["frozen_sha"] == _decision(env, did2)["frozen_sha"] == sha
