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

def test_s_d3c_4_a_release_retried_by_its_timer_dispatches_on_the_timer_thread(env, decided, repo, tap, record,
                                                                              monkeypatch):
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-4", sc)
    blocker = HoldG(sc.flow)
    approve(sc)
    rec.step("busy")
    real = lock_manager.db.delete_lock
    failed = {"n": 0}

    def delete_lock(*args, **kwargs):
        if failed["n"] == 0:
            failed["n"] += 1
            raise RuntimeError("store unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(lock_manager.db, "delete_lock", delete_lock)
    blocker.release()
    row = rec.step("store_error")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and attempt_of(env, sc.attempt)["integration_status"] != INTEGRATED:
        time.sleep(0.2)
    assert failed["n"] == 1 and row["integration"] == PENDING
    expect_recovered("a", rec.step("timer"), sc)
