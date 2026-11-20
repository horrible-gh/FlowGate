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

@pytest.mark.parametrize('case', ['not_merged'])
def test_s_rec_1_the_server_start_recovery(env, decided, repo, tap, record, monkeypatch, case):
    from flowgate.graph_run import result_merge
    sc = scene(env, repo)
    rec = record(f"S-REC-1_{case}", sc)
    blocker = None
    if case == "merged":
        injection = Inject(monkeypatch, TARGETS["a"], persistent=True)
        approve(sc)
        injection.release()
    elif case == "not_merged":
        blocker = HoldG(sc.flow)
        injection = Inject(monkeypatch, TARGETS["c"], persistent=True)
        approve(sc)
        injection.release()
    else:
        prefail(sc)
        fails = Inject(monkeypatch, [("_end_hold.<locals>.body", None), ("_release_hold.<locals>.body", None)],
                       persistent=True)
        drive(sc, "h2", monkeypatch)
        fails.release()
    before = rec.step("before")
    result_merge.recover()
    row = rec.step("server_start")
    if case == "hold":
        assert before["hold"] is not None and row["hold"] is None, (before, row)
    if case == "not_merged":                               # pending again (recovered), its retry finds G busy
        assert before["integration"] == INTEGRATING and (row["integration"], row["reason"]) == (PENDING, "git_busy")
        blocker.release()
        row = rec.step("blocker_released")
    else:
        assert before["integration"] == (INTEGRATING if case == "merged" else INTEGRATED), before
    expect_recovered("a", row, sc)
