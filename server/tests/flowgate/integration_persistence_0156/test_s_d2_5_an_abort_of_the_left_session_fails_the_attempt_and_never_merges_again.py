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

def test_s_d2_5_an_abort_of_the_left_session_fails_the_attempt_and_never_merges_again(env, decided, repo, tap,
                                                                                         record, monkeypatch):
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5", sc)
    injection = Inject(monkeypatch, TARGETS["b"], persistent=True, on_fire=lambda: rec.step("inject"))
    approve(sc)
    rec.step("F1")
    merge_id = person_aborts(sc)                           # the person's abort (its session closes)
    rec.step("abort", merge_id=merge_id)
    injection.release()
    lock_release(sc.flow)
    rec.step("F2")
    from flowgate.graph_run import result_merge, stop as stop_module
    result_merge.recover()                                 # (a failed one waits for its command, by design)
    stop_module.recover()
    rec.step("F5")
    for label in ("abort", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")] and not row["merge_head"] and row["merges"] == 0, row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")
