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

@pytest.mark.parametrize('hold', ['h1'])
def test_s_il_l2_an_attempt_worktree_lock_fails_the_integration_and_a_command_recovers_it(env, decided, repo, tap,
                                                                                           record, monkeypatch, hold):
    """S-IL-2 (L2) on the real stack: t1's result is not committed yet, so the integration's T#8 step
    (``commit_result`` -> ``create_attempt_commit``, under the real W lock) meets an ``index.lock`` left in the attempt
    worktree. Under the lock: ``failed`` (``result_commit:commit_failed``), no hold, idle (``merge_conflict``), no
    result commit recorded, no merge, no MERGE_HEAD, the card's change kept in its tree. An unrelated G release does
    not retry it (a failed one waits for its command, by design). The lock removed, a command (step) commits it once
    and merges it once: integrated, done, one merge commit, the result in the group branch. (h1) the idle hold of an
    approval no command wakes; (h2) the step command's hold after the first failure. Only path resolution is
    redirected (``workspace.source_root`` -> the attempt's linked worktree; the project's Git config read as enabled)."""
    from flowgate.graph_run import workspace
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import commit as git_commit
    sc = scene(env, repo, uncommitted=True)
    rec = record(f"S-IL_L2_{hold}", sc)
    real_root = workspace.source_root
    monkeypatch.setattr(workspace, "source_root", lambda attempt_id, **kw:
                        sc.linked.resolve() if attempt_id == sc.attempt else real_root(attempt_id, **kw))
    real_cfg = gs.db_git.get_config
    monkeypatch.setattr(gs.db_git, "get_config", lambda project_id:
                        {**(real_cfg(project_id) or {}), "enabled": True} if project_id == PROJECT
                        else real_cfg(project_id))
    commits: list = []
    real_commit = git_commit.create_attempt_commit

    def create_attempt_commit(*args, **kwargs):
        out = real_commit(*args, **kwargs)
        commits.append("committed" if out.get("committed") else out.get("skipped_reason"))
        return out

    monkeypatch.setattr(git_commit, "create_attempt_commit", create_attempt_commit)
    lock = repo.git_path(sc.linked, "index.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    assert _result_commit_of(env, sc.attempt) is None                 # nothing made in advance to reuse
    if hold == "h1":
        approve_unwoken(sc, monkeypatch)
    else:
        approve(sc)                                                   # the first integration meets the lock
        first = rec.step("first", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt))
        assert (first["integration"], first["reason"]) == (FAILED, "result_commit:commit_failed"), first
        assert command(sc, "step") == 200                             # (h2) the command's hold, the lock still there
    locked = rec.step("locked", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt),
                      dirty=repo.dirty(sc.flow), kept=(sc.linked / "result_l2.txt").exists())
    lock_release(sc.flow)                                             # F2: no retry of a failed one
    released = rec.step("lock_release", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt))
    lock.unlink()
    code = command(sc, "step")
    sc.result = _result_commit_of(env, sc.attempt)
    final = rec.step("command", code=code, commits=list(commits), result_commit=sc.result, dirty=repo.dirty(sc.flow))
    final_after = rec.step("final", dirty=repo.dirty(sc.flow))

    for row in (locked, released):
        assert (row["integration"], row["reason"]) == (FAILED, "result_commit:commit_failed"), row
        assert row["hold"] is None and not row["working"], row
        assert (row["run"], row["idle_reason"]) == ("idle", "merge_conflict"), row
        assert row["result_commit"] is None and row["merges"] == 0 and not row["merge_head"], row
        assert row["sessions"] == [], row
    assert locked["commits"] and set(locked["commits"]) == {"commit_failed"}, locked
    assert len(locked["commits"]) == (1 if hold == "h1" else 2), locked             # each integration met the lock
    assert released["commits"] == locked["commits"], released                      # the G release asked nothing
    assert not locked["dirty"] and locked["kept"], locked                          # no partial merge, change kept
    assert code == 200, final
    assert final["commits"][len(locked["commits"]):] == ["committed"], final       # committed once, after the lock
    assert sc.result and sc.result != repo.base, final
    expect_recovered("a", final, sc)                       # integrated, ended, done, one merge, result in the branch
    assert not final["merge_head"] and not final["dirty"], final
    assert (repo.wt(sc.flow) / "result_l2.txt").read_text(encoding="utf-8") == "the card's result\n"
    assert final_after["merges"] == 1, final_after
