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

def test_s_d3c_5_a_stale_lock_reclaimed_by_the_sweeper(env, decided, repo, tap, record):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_sweeper, lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-5", sc)
    blocker = HoldG(sc.flow, "run_prepare")              # a long hold (heartbeated)
    held = blocker.lock[0].find_held(blocker.lock[1])
    lock_manager._heartbeats.remove(held)                  # its holder stopped beating
    approve(sc)
    rec.step("busy")
    get_store()._execute("UPDATE resource_lock SET heartbeat_until = ? WHERE lock_key = ?",
                         ["2000-01-01T00:00:00+00:00", blocker.lock[1]])
    reclaimed = job_sweeper.step_stale_locks(full=True)
    row = rec.step("reclaimed", reclaimed=reclaimed)
    assert reclaimed == 1
    blocker.release()                                      # lock_lost_before_release: no notice
    rec.step("holder_release")
    expect_recovered("a", row, sc)
