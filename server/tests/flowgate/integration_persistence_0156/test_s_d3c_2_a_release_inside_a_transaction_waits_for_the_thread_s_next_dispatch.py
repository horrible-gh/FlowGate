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

def test_s_d3c_2_a_release_inside_a_transaction_waits_for_the_thread_s_next_dispatch(env, decided, repo, tap, record):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-2", sc)
    steps = {name: threading.Event() for name in ("held", "go", "released", "go2", "done")}

    def job():
        ctx = lock_manager.new_context("req")
        g = lock_manager.acquire("G", PROJECT, group_id=sc.flow, holder_kind="review_action", ctx=ctx)
        assert g.ok
        steps["held"].set()
        steps["go"].wait(60)
        with get_store().transaction():
            lock_manager.release(ctx, g.lock_key)
        steps["released"].set()
        steps["go2"].wait(60)
        other = lock_manager.acquire("W", PROJECT, target_key="unrelated", holder_kind="review_action", ctx=ctx)
        lock_manager.release(ctx, other.lock_key)        # the thread's next release dispatches what waited
        steps["done"].set()

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert steps["held"].wait(30)
    approve(sc)
    rec.step("busy")
    steps["go"].set()
    assert steps["released"].wait(30)
    row = rec.step("released_in_transaction")
    assert row["integration"] == PENDING, row
    steps["go2"].set()
    assert steps["done"].wait(60)
    t.join(30)
    expect_recovered("a", rec.step("next_dispatch"), sc)
