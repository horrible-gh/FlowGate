"""Independently selectable scenario; shared harness stays in test_fg_integration_persistence_0156."""
from __future__ import annotations

from test_fg_integration_persistence_0156 import (
    CAUSES,
    CONFLICT,
    CleanupFails,
    FA,
    FAILED,
    HoldG,
    INTEGRATED,
    INTEGRATING,
    Inject,
    LEE,
    LOG_KEYS,
    LogTap,
    PENDING,
    PROJECT,
    Path,
    Record,
    Repo,
    SimpleNamespace,
    TARGETS,
    _SERVER,
    _admin_and_reviewer,
    _claim,
    _doc,
    _gate_end,
    _git,
    _group,
    _h,
    _held_merge,
    _lock_file,
    _recover_spy,
    _result_commit_of,
    _source,
    _sql,
    _status_is,
    abort_before_conflict,
    approve,
    approve_unwoken,
    arm,
    attempt_of,
    card_outputs_port,
    chain,
    command,
    conflict_kept_as_session_0042,
    db_path,
    decided,
    drive,
    edge,
    env,
    expect_recovered,
    fresh_drain_state,
    json,
    legacy_cards,
    lock_release,
    logging,
    no_document_gates,
    node,
    node_of,
    open_session,
    passive_person_results,
    person_aborts,
    pre_feature_attempts,
    prefail,
    put_draft,
    pytest,
    pytestmark,
    record,
    registers,
    repo,
    reserve,
    result_doc,
    run_of,
    scene,
    shutil,
    state,
    stop,
    subprocess,
    sys,
    tap,
    threading,
    time,
)

def test_s_il_l2_the_attempt_worktree_lock_is_a_failed_commit_never_pending(tmp_path, monkeypatch):
    """L2: a result commit already made (the integration reuses it) never touches the attempt worktree; the result
    commit step (``create_attempt_commit``) meeting an attempt worktree ``index.lock`` answers ``commit_failed``,
    which ``integrate`` records as ``failed`` (only ``git_busy`` — an app lock with a release notice — is pending)."""
    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import commit as git_commit
    r = Repo(tmp_path / "git")
    linked = tmp_path / "git" / "attempt"
    _git(r.common, "worktree", "add", "-b", "graph_attempt_x", str(linked), r.base)
    (linked / "a.txt").write_text("changed\n", encoding="utf-8")
    r.git_path(linked, "index.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(gs.db_git, "get_config", lambda project_id: {"enabled": True})
    monkeypatch.setattr(gs, "_worktree_link_ok", lambda path: True)
    from modules.flow_gate.services.git import lock_manager
    monkeypatch.setattr(lock_manager, "acquire", lambda *a, **k: SimpleNamespace(ok=True, lock_key="W:x"))
    monkeypatch.setattr(lock_manager, "release", lambda ctx, key: None)
    outcome = git_commit.create_attempt_commit(PROJECT, linked, "graph_attempt_x", r.base, "ga_x", "doc_x", "feat: x")
    assert outcome["committed"] is False and outcome["skipped_reason"] == "commit_failed", outcome
