import json

from modules.flow_gate.db import project_ai_leases
from modules.flow_gate.services import remote_tool_service, tool_registry


def test_project_ai_lease_is_project_scoped_and_released(monkeypatch):
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    assert project_ai_leases.acquire("p1", "owner-1")
    assert project_ai_leases.acquire("p1", "owner-2") is None
    assert project_ai_leases.acquire("p2", "owner-3")
    assert project_ai_leases.activate("p1", "owner-1", "run-1")["state"] == "active"
    assert project_ai_leases.release("p1", "run-1") is True
    assert project_ai_leases.acquire("p1", "owner-4")


def test_resolve_base_dirty_permission_and_catalog_converge():
    assert tool_registry.kind_for_step("resolve_base_dirty") == ("read_write", None)
    assert tool_registry.kind_for_token({"action_scope": "resolve_base_dirty"}) == ("read_write", None)
    # Convergence is per SCOPE, not per kind: the op 403s for anything but its own
    # action_scope (remote_tool_service._exec_resolve_base_dirty), so a plain read_write
    # worker must not be advertised it (0492 D0004 D-2 "advertised == granted").
    assert "resolve_base_dirty" in tool_registry.tool_names("read_write", "resolve_base_dirty")
    assert "resolve_base_dirty" not in tool_registry.tool_names("read_write")
    assert "resolve_base_dirty" not in tool_registry.tool_names("read_write", "edit")
    assert "resolve_base_dirty" not in tool_registry.tool_names("read", "resolve_base_dirty")
    assert remote_tool_service.OP_SCOPE["resolve_base_dirty"] == "write"


def _stub_resolve_lock(monkeypatch, git_service, acquire_calls=None, release_calls=None):
    """Stub the project git lock so unit tests stay hermetic (no real DB row).

    Records calls when the caller passes a list, so tests can assert the lock
    is acquired exactly once for the whole batch (0482 T0011 automated review:
    baseline capture, discard, and commit must share one held lock)."""
    from modules.flow_gate.db import git_integration as db_git

    def _acquire(project_id, holder, **_kw):
        if acquire_calls is not None:
            acquire_calls.append((project_id, holder))
        return True

    def _release(project_id, holder):
        if release_calls is not None:
            release_calls.append((project_id, holder))

    monkeypatch.setattr(git_service, "_acquire_lock", _acquire)
    monkeypatch.setattr(db_git, "release_lock", _release)


def test_resolve_base_dirty_prevalidates_and_returns_partial(monkeypatch):
    token = {"action_scope": "resolve_base_dirty"}
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _grant: token)
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py", "b.py"]}}})
    reverted = []
    monkeypatch.setattr(git_service, "base_revert", lambda _p, files, **_kw: reverted.extend(files) or {})
    result, _ = remote_tool_service._exec_resolve_base_dirty(
        {"decisions": [{"path": "a.py", "action": "discard"}], "complete": False},
        {"project": "p1"},
    )
    assert result == {"status": "partial", "remaining": ["b.py"], "commit": None}
    assert reverted == ["a.py"]


def test_resolve_base_dirty_rejects_unknown_path_before_mutation(monkeypatch):
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    called = []
    monkeypatch.setattr(git_service, "base_revert", lambda *_a, **_kw: called.append(True))
    try:
        remote_tool_service._exec_resolve_base_dirty(
            {"decisions": [{"path": "x.py", "action": "discard"}], "complete": True}, {"project": "p1"})
    except remote_tool_service._OpError as exc:
        assert exc.status == 422
    else:
        raise AssertionError("expected validation failure")
    assert called == []


def test_resolve_base_dirty_complete_with_outstanding_returns_dirty(monkeypatch):
    # complete=true but the baseline has an undecided file → status=dirty, nothing committed.
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py", "b.py"]}}})
    monkeypatch.setattr(git_service, "base_revert", lambda *_a, **_kw: {})
    committed = []
    monkeypatch.setattr(git_service, "base_commit", lambda *a, **kw: committed.append(a) or {"result": {"commit": "deadbeef"}})
    result, _ = remote_tool_service._exec_resolve_base_dirty(
        {"decisions": [{"path": "a.py", "action": "discard"}], "complete": True},
        {"project": "p1"},
    )
    assert result == {"status": "dirty", "remaining": ["b.py"], "commit": None}
    assert committed == []   # nothing is committed while outstanding files remain


def test_resolve_base_dirty_complete_all_commit_returns_resolved(monkeypatch):
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    calls = []

    def _base_commit(project_id, message, paths, **_kw):
        calls.append((project_id, message, paths))
        return {"result": {"commit": "cafebabe"}}

    monkeypatch.setattr(git_service, "base_commit", _base_commit)
    result, _ = remote_tool_service._exec_resolve_base_dirty(
        {"decisions": [{"path": "a.py", "action": "commit"}], "complete": True, "commit_message": "fix: base"},
        {"project": "p1"},
    )
    assert result == {"status": "resolved", "remaining": [], "commit": "cafebabe"}
    # Regression guard (0482 T0011 automated review): message and paths must
    # land in the right positional slots, not swapped.
    assert calls == [("p1", "fix: base", ["a.py"])]


def test_resolve_base_dirty_complete_all_discard_returns_null_commit(monkeypatch):
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})
    monkeypatch.setattr(git_service, "base_revert", lambda *_a, **_kw: {})
    result, _ = remote_tool_service._exec_resolve_base_dirty(
        {"decisions": [{"path": "a.py", "action": "discard"}], "complete": True},
        {"project": "p1"},
    )
    assert result == {"status": "resolved", "remaining": [], "commit": None}


def test_resolve_base_dirty_git_lock_failure_is_409_git_busy(monkeypatch):
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    _stub_resolve_lock(monkeypatch, git_service)

    class _Busy(Exception):
        status_code = 409

    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}})

    def _raise(*_a, **_kw):
        raise _Busy("git busy")

    monkeypatch.setattr(git_service, "base_revert", _raise)
    try:
        remote_tool_service._exec_resolve_base_dirty(
            {"decisions": [{"path": "a.py", "action": "discard"}], "complete": True}, {"project": "p1"})
    except remote_tool_service._OpError as exc:
        assert exc.status == 409
        assert exc.details == {"reason": "git_busy"}
    else:
        raise AssertionError("expected git_busy")


def test_resolve_base_dirty_outer_lock_busy_is_409(monkeypatch):
    # Acquiring the whole-batch project lock itself can fail (another git op
    # holds it) — must surface as 409 git_busy before touching anything.
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_a, **_kw: False)
    try:
        remote_tool_service._exec_resolve_base_dirty(
            {"decisions": [{"path": "a.py", "action": "discard"}], "complete": True}, {"project": "p1"})
    except remote_tool_service._OpError as exc:
        assert exc.status == 409
        assert exc.details == {"reason": "git_busy"}
    else:
        raise AssertionError("expected git_busy")


def test_resolve_base_dirty_holds_one_lock_across_discard_and_commit(monkeypatch):
    # 0482 T0011 automated review: baseline capture, discard, and commit
    # previously ran under three independently-acquired locks (or none, for
    # the baseline read), leaving a race window between them. Now they must
    # share exactly one held lock for the whole call.
    monkeypatch.setattr(remote_tool_service, "_worker_token_for_grant", lambda _g: {"action_scope": "resolve_base_dirty"})
    from modules.flow_gate.services import git_service
    acquire_calls = []
    release_calls = []
    _stub_resolve_lock(monkeypatch, git_service, acquire_calls, release_calls)
    monkeypatch.setattr(git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": ["a.py", "b.py"]}}})
    revert_holders = []
    monkeypatch.setattr(git_service, "base_revert", lambda _p, files, _holder=None: revert_holders.append(_holder) or {})
    commit_holders = []
    monkeypatch.setattr(
        git_service, "base_commit",
        lambda _p, msg, paths, _holder=None: commit_holders.append(_holder) or {"result": {"commit": "abc123"}},
    )
    result, _ = remote_tool_service._exec_resolve_base_dirty(
        {
            "decisions": [{"path": "a.py", "action": "discard"}, {"path": "b.py", "action": "commit"}],
            "complete": True, "commit_message": "fix: base",
        },
        {"project": "p1"},
    )
    assert result == {"status": "resolved", "remaining": [], "commit": "abc123"}
    assert len(acquire_calls) == 1   # one lock for the whole batch, not per sub-call
    holder = acquire_calls[0][1]
    assert revert_holders == [holder]
    assert commit_holders == [holder]
    assert release_calls == [("p1", holder)]


def test_base_commit_with_holder_reuses_the_callers_lock(monkeypatch, tmp_path):
    # git_service side of the same contract: passing `_holder` must skip
    # acquiring a fresh project lock and run the locked body directly.
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "_require_base_checkout", lambda _p: ({}, tmp_path))
    monkeypatch.setattr(git_service, "guard_base_free", lambda _p: None)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    acquire_calls = []
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_a, **_kw: acquire_calls.append(1) or True)
    monkeypatch.setattr(git_service, "_base_commit_locked", lambda *_a, **_kw: {"result": {"commit": "x"}})
    result = git_service.base_commit("p1", "msg", ["a.py"], _holder="held-1")
    assert result == {"result": {"commit": "x"}}
    assert acquire_calls == []


def test_base_revert_with_holder_reuses_the_callers_lock(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "_require_base_checkout", lambda _p: ({}, tmp_path))
    monkeypatch.setattr(git_service, "guard_base_free", lambda _p: None)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    acquire_calls = []
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_a, **_kw: acquire_calls.append(1) or True)
    monkeypatch.setattr(git_service, "_base_revert_locked", lambda *_a, **_kw: {"result": {"results": []}})
    result = git_service.base_revert("p1", ["a.py"], _holder="held-1")
    assert result == {"result": {"results": []}}
    assert acquire_calls == []


def _stub_finalize_dependencies(monkeypatch, svc, tmp_path):
    """Neutralize every _finalize_run collaborator this test does not care about.

    _finalize_run does a great deal beyond the lease release (scratch cleanup, stop-code
    classification, persistence, broadcast) — none of it is this test's concern, so each
    is stubbed to a no-op rather than reconstructing a fully realistic run.
    """
    monkeypatch.setattr(svc, "peek_auto_resume", lambda _g: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "begin_handoff", lambda *a: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a: None)
    monkeypatch.setattr(svc, "_resolve_stop_code", lambda run, respawn_pending: None)
    monkeypatch.setattr(svc, "is_resumable", lambda code: False)
    monkeypatch.setattr(svc, "_stop_reason_text", lambda code, run: None)
    monkeypatch.setattr(svc, "_mark_scratch_completed", lambda *a: True)
    monkeypatch.setattr(svc, "_delete_owned_scratch", lambda *a: (True, "complete"))
    monkeypatch.setattr(svc, "_git_status_paths", lambda *_a: None)
    monkeypatch.setattr(svc, "_resolve_timeout_diagnostics", lambda run: (None, None))
    monkeypatch.setattr(svc, "_apply_stop_row", lambda *a: None)
    monkeypatch.setattr(svc, "_persist_run_record", lambda run: None)
    monkeypatch.setattr(svc, "_notify_chain_failure_if_needed", lambda run: None)
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    monkeypatch.setattr(svc, "finished_payload", lambda run: {})
    (tmp_path / "scratch").mkdir()
    return tmp_path / "scratch"


def test_finalize_run_releases_the_project_lease_on_completion(monkeypatch, tmp_path):
    # Regression: a botched edit once turned this release call into a dead trailing
    # comment (the whole block silently never ran), so a finished resolve_base_dirty
    # run left its project lease held until TTL expiry and every later attempt for the
    # same project 409'd with base_dirty_run_in_progress forever.
    from modules.flow_gate.services import ai_invoke_service as svc

    scratch = _stub_finalize_dependencies(monkeypatch, svc, tmp_path)
    released = []
    monkeypatch.setattr(project_ai_leases, "release", lambda project_id, run_id: released.append((project_id, run_id)))
    run = {
        "action_scope": "resolve_base_dirty", "project_id": "p1", "run_id": "run-1",
        "group_id": None, "scratch_dir": str(scratch), "outcome": "complete",
        "started_mono": 0.0, "last_message": None, "last_message_seen": None,
        "chain_docs_accounted": True,
    }
    svc._finalize_run(run)
    assert released == [("p1", "run-1")]


def test_finalize_run_skips_release_for_other_scopes(monkeypatch, tmp_path):
    from modules.flow_gate.services import ai_invoke_service as svc

    scratch = _stub_finalize_dependencies(monkeypatch, svc, tmp_path)
    released = []
    monkeypatch.setattr(project_ai_leases, "release", lambda *a: released.append(a))
    run = {
        "action_scope": "edit", "project_id": "p1", "run_id": "run-1",
        "group_id": "flowgate.default.0001", "scratch_dir": str(scratch), "outcome": "complete",
        "started_mono": 0.0, "last_message": None, "last_message_seen": None,
        "chain_docs_accounted": True,
    }
    svc._finalize_run(run)
    assert released == []


def test_admission_order_prefers_empty_over_run_in_progress(monkeypatch):
    # L0008 §4: base_dirty_empty must win even when a project lease is already active —
    # nothing to delegate outweighs an in-progress run.
    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(ai_invoke_routes.git_service, "project_git_status", lambda _p: {"status": {"base_dirty": {"files": []}}})
    monkeypatch.setattr(project_ai_leases, "get_active", lambda _p: {"run_id": "existing-run"})
    body = ai_invoke_routes.AiInvokeStartRequest(project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single")

    class _Request:
        headers = {"x-locale": "ko"}
        url = type("URL", (), {"scheme": "http", "hostname": "localhost", "port": 80})()

    response = ai_invoke_routes.start_ai_invoke(body, _Request())
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload["code"] == "base_dirty_empty"   # not base_dirty_run_in_progress