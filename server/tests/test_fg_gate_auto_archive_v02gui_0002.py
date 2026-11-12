"""flowgate.v02gui.0002 C (v02test.0024.0006-B C-1~C-5): a confirm's archive ON archives the Flow once its run completes.

On the real SQLite DB, routes, Runner and a real bare origin of ``test_fg_gate_publish_0085``: the confirm's publish job
runs, the run completes and the Flow is archived through the manual archive's own path (``flow_archive.change``) -
with no toolbar click. Archive OFF archives nothing (the manual button still works); a run not completed yet (a card
after the gate) archives nothing until it completes; a withdrawn write permission leaves the Flow as it is; a repeat
changes nothing. No Git ref, decision or document is touched by the archive itself.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node, put_draft, setting  # noqa: E402,F401
from test_fg_gate_intake_0085 import _current, _nodes, _start, allowed, product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    _cleanup_jobs, _confirm, _decision, _flow, _gate, _rev, _run_job, _run_status, _start_linear, _work, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def _archive_view(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/archive", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _archived_at(env):
    return _sql(env["db"], "SELECT archived_at, archived_by FROM groups WHERE group_id = ?", (FA,))[0]


def _single_gate_run(r, **gate):
    """R -> G: the confirm's publish job ends the run (completed)."""
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, **gate)
    body = _confirm(env, run_id, g, "aa1")
    return env, g, run_id, sha, body


@pytest.mark.parametrize("gate", [
    {"merge_mode": "hold", "archive": True},                 # Git apply OFF, archive ON
    {"merge_mode": "merge", "archive": True},                # Git apply ON, archive ON
], ids=["git_off", "git_on"])
def test_archive_on_archives_the_flow_once_its_run_completes(git_env, gate):
    r = git_env
    env, g, run_id, sha, body = _single_gate_run(r, **gate)
    did = body["decision"]["decision_id"]
    assert body["job"] is not None                                         # archive ON always goes through the job
    assert _archived_at(env)["archived_at"] is None                        # accepted only: nothing archived yet
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    stored = _archived_at(env)
    assert stored["archived_at"] is not None and stored["archived_by"] == "u_kim"   # the confirming person
    view = _archive_view(env)
    assert (view["archived"], view["archived_by"], view["reason"]) == (True, "u_kim", "already_archived")
    # The Git checkpoint and the decision are the publish job's, untouched by the Flow archive.
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _decision(env, did)["status"] == "succeeded"
    assert _nodes(_current(env))[g]["gate"]["archived"] is True
    # The end cleanup job of the completion is still made beside it.
    assert len(_cleanup_jobs(env)) == 1
    events = _sql(env["db"], "SELECT payload_json FROM canvas_events WHERE group_id = ? AND payload_json LIKE ?",
                  (FA, "%flow_archived%")) if _has_table(env, "canvas_events") else None
    if events is not None:
        assert len(events) == 1                                            # the manual button's own event


def _has_table(env, name):
    return bool(_sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)))


def test_archive_off_never_archives_and_the_manual_button_still_does(git_env):
    r = git_env
    env, g, run_id, sha, body = _single_gate_run(r, merge_mode="merge", archive=False)
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _archived_at(env)["archived_at"] is None
    view = _archive_view(env)
    assert (view["archived"], view["eligible"]) == (False, True)
    res = env["client"].post(f"/api/v1/flows/{FA}/archive", headers=_h())
    assert res.status_code == 200, res.text
    assert res.json()["archived"] is True


def test_archive_on_waits_for_the_run_to_complete(git_env):
    """R -> G -> T: the publish job ends while T is still open - the run is not completed, the Flow stays."""
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "aw1")
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _run_status(env, run_id)["idle_reason"] != "completed"
    assert _nodes(_current(env))[t]["status"] == "open"
    assert _archived_at(env)["archived_at"] is None
    assert _archive_view(env)["archived"] is False


def test_a_withdrawn_permission_archives_nothing_and_the_completion_stands(git_env, allowed):
    r = git_env
    env, g, run_id, sha, body = _single_gate_run(r, merge_mode="hold", archive=True)
    allowed["allow"] = False
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _archived_at(env)["archived_at"] is None


def test_a_repeat_changes_nothing(git_env):
    from flowgate.gate_decisions import auto_archive
    r = git_env
    env, g, run_id, sha, body = _single_gate_run(r, merge_mode="hold", archive=True)
    _run_job(body["job"]["job_id"])
    first = _archived_at(env)
    assert first["archived_at"] is not None
    again = auto_archive.archive(FA, run_id, "u_kim")
    assert again["archived"] is True and again["archived_at"] == first["archived_at"]
    assert _archived_at(env) == first
    assert auto_archive.archiving_actor(FA, run_id) == "u_kim"
