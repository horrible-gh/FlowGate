"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_0066 import (
    GitServiceError,
    SECRET,
    SECRET_KO,
    _Crash,
    _ctx,
    _dirty,
    _git,
    _hidden_conflict,
    _job_of,
    _local_unrecorded_merge,
    _mm,
    _recovery_row,
    _session,
    _svc,
    branch,
    drive_jobs,
    files_at,
    git_quoted,
    group,
    held_locks,
    manual,
    merge_sessions,
    needs_git,
    no_hidden,
    patch_store,
    proj,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    try_rev,
    uuid,
    work,
)

@needs_git
@pytest.mark.parametrize("run,hook,expected", [
    (None, "unused", None),
    ({"run_id": "r1"}, None, None),
    ({"run_id": "r1"}, "graph_execution_active", "graph_execution_active"),
    ({"run_id": "r1"}, "graph_execution_handoff", "graph_execution_handoff"),
    ({"run_id": "r1"}, "graph_integration_active", "graph_integration_active"),
    ({"run_id": "r1"}, "unregistered", "graph_state_unavailable"),
])
def test_graph_blocker_table(proj, monkeypatch, run, hook, expected):
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import graph_execution_hooks
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    monkeypatch.setattr(run_store, "active_run", lambda _: run)
    monkeypatch.setattr(run_store, "has_active_attempts", lambda _: True)
    if hook == "unregistered":
        monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    else:
        monkeypatch.setattr(graph_execution_hooks, "_handler",
                            lambda action, *a, **k: hook if action == "manual_git_blocker" else None)
    if expected is None:
        out = manual(gid)
        assert out["status"] == "merged"
        return
    error = refused(manual, gid)
    assert error.code == "graph_execution_active" and error.details == {"reason": expected}
    assert "a.txt" not in files_at(proj.base, "main")
def test_manual_git_blocker_reads_only_execution_handoff_and_integration(monkeypatch):
    from flowgate.graph_run import execution, execution_store, result_merge
    from modules.flow_gate.graph_workflow.store import run_store
    facts = {"working": False, "handoff": False, "integrating": False, "status": "idle"}
    monkeypatch.setattr(execution_store, "working", lambda _: facts["working"])
    monkeypatch.setattr(execution_store, "handing_off", lambda _: facts["handoff"])
    monkeypatch.setattr(result_merge, "in_progress", lambda _: facts["integrating"])
    monkeypatch.setattr(run_store, "run_by_id", lambda _: {"status": facts["status"]})
    assert execution.manual_git_blocker("r") is None                       # idle run
    facts.update(status="running")
    assert execution.manual_git_blocker("r") is None                       # open manual/gate node only
    facts.update(handoff=True)
    assert execution.manual_git_blocker("r") == "graph_execution_handoff"
    facts.update(status="idle")
    assert execution.manual_git_blocker("r") is None                       # hand-off of an idle run
    facts.update(integrating=True)
    assert execution.manual_git_blocker("r") == "graph_integration_active"
    facts.update(working=True)
    assert execution.manual_git_blocker("r") == "graph_execution_active"
    assert execution._ACTIONS["manual_git_blocker"] is execution.manual_git_blocker
def test_unregistered_hook_raises_for_manual_git_blocker(monkeypatch):
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    with pytest.raises(RuntimeError):
        graph_execution_hooks.call("manual_git_blocker", "r1")
