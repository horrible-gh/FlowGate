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

@pytest.mark.parametrize('then', ['server_start'])
def test_s_d2_6_a_stop_reclaims_the_integrating_attempt_done_stays_done(env, decided, repo, tap, record, monkeypatch,
                                                                          then):
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo)
    rec = record(f"S-D2-6_{then}", sc)
    injection = Inject(monkeypatch, TARGETS["a"], persistent=True, on_fire=lambda: rec.step("inject"))
    approve(sc)
    rec.step("F1")
    rec.step("stop_while_failing", code=stop(sc))
    assert rec.at("stop_while_failing")["run"] == "stopping"          # the merge's end is not recorded yet
    injection.release()
    if then == "stop_again":
        rec.step("stop", code=stop(sc))
    elif then == "lock_release":
        lock_release(sc.flow)
        rec.step("lock_release")
    else:
        stop_module.recover()
        rec.step("server_start")
    row = rec.rows[-1]
    assert (row["run"], row["idle_reason"]) == ("idle", "stopped"), row
    assert (row["attempt"], row["integration"], row["node"]) == ("ended", INTEGRATED, "done"), row
    assert row["merges"] == 1 and row["result_in_branch"], row
    assert stop(sc) == 409                                             # idempotent: nothing left to stop
    lock_release(sc.flow)
    result_merge.recover()
    stop_module.recover()
    again = rec.step("again")
    assert (again["run"], again["integration"], again["node"], again["merges"]) == ("idle", INTEGRATED, "done", 1)
