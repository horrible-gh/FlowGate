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
@pytest.mark.parametrize('where', ['L4'])
def test_s_il_an_outside_git_lock_file(env, decided, repo, tap, record, monkeypatch, where, hold):
    sc = scene(env, repo)
    rec = record(f"S-IL_{where}_{hold}", sc)
    if hold == "h2":
        prefail(sc)
    path = _lock_file(repo, sc, where)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    drive(sc, hold, monkeypatch)
    row = rec.step("locked", lock=str(path), dirty=repo.dirty(sc.flow))
    lock_release(sc.flow)
    rec.step("lock_release")
    path.unlink()
    rec.step("command", code=command(sc), dirty=repo.dirty(sc.flow))
    final = rec.step("final", dirty=repo.dirty(sc.flow))
    if where == "L4":                                      # the common index: a linked group worktree is not touched
        assert row["integration"] == INTEGRATED, row
    else:
        assert row["integration"] in (FAILED, INTEGRATED), row          # never pending with no trigger
        assert row["hold"] is None, row
    assert final["integration"] == INTEGRATED and final["merges"] == 1 and final["result_in_branch"], final
    assert not final["merge_head"], final
