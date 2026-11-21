"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0085 import (
    FA,
    PROJECT,
    Path,
    _SERVER_DIR,
    _act,
    _alive,
    _attempt,
    _cleanup_jobs,
    _cleanups,
    _completed_merge_flow,
    _confirm,
    _confirmed,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _expire_and_reconcile,
    _finish,
    _flow,
    _gate,
    _gate_events,
    _git,
    _h,
    _job_row,
    _jobs,
    _linear,
    _merges,
    _nodes,
    _parked_after_merge,
    _parked_without_effect,
    _push_rejected_once,
    _requests,
    _rev,
    _run_cleanup,
    _run_job,
    _run_status,
    _second_publish,
    _side_commit,
    _sql,
    _start,
    _start_linear,
    _states,
    _version,
    _work,
    allowed,
    base64,
    db_path,
    edge,
    env,
    git_env,
    json,
    node,
    os,
    product,
    put_draft,
    pytest,
    pytestmark,
    setting,
    shutil,
    subprocess,
    sys,
)

def test_hold_with_archive_only_preserves_and_ignores_the_stored_git_options(git_env):
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    main_before = _rev(r["base"], "refs/heads/main")
    _gate(env, g, merge_mode="hold", merge_target="origin", push=True, keep_branch=True, archive=True)
    body = _confirm(env, run_id, g, "h1")
    did = body["decision"]["decision_id"]
    eff = json.loads(_decision(env, did)["settings_json"])["effective"]
    assert (eff["git_apply"], eff["push"], eff["keep_branch"], eff["archive"]) == (False, False, False, True)
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}-applied") is None
    assert _rev(r["base"], "refs/heads/main") == main_before == _rev(r["bare"], "refs/heads/main")
    assert _rev(r["bare"], f"refs/heads/{r['branch']}") is None             # no push
    assert set(_effects(env, did)) == {"snapshot", "archive"}
    assert _nodes(_current(env))[g]["gate"]["archived"] is True
def test_a_local_merge_without_push_stays_local(git_env):
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    origin_before = _rev(r["bare"], "refs/heads/main")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "l1")
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    merged = _rev(r["base"], "refs/heads/main")
    assert merged == sha and _rev(r["bare"], "refs/heads/main") == origin_before
    assert _nodes(_current(env))[g]["gate"]["archived"] is False            # archive OFF: no chip
def test_a_db_failure_after_the_git_work_retries_only_the_completion(git_env, monkeypatch):
    from flowgate.gate_decisions import intake
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "db1")
    real, calls = intake._apply, []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        return real(*args, **kwargs)

    monkeypatch.setattr(intake, "_apply", failing)
    job, _ = _run_job(body["job"]["job_id"])
    assert (job["status"], job["phase"]) == ("retry_wait", "db_finalize")
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"
    assert _decision(env, body["decision"]["decision_id"])["status"] == "accepted"
    merged = _rev(r["base"], "refs/heads/main")
    assert _rev(r["bare"], "refs/heads/main") == merged
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "succeeded"
    assert _rev(r["base"], "refs/heads/main") == merged and _merges(r["base"]) == 0       # not merged again
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
    assert _states(env)[t] == "open"
