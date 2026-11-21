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

def test_s_d3c_1_a_g_release_under_m_is_taken_at_the_m_release(env, decided, repo, tap, record, monkeypatch):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-1", sc)
    steps = {name: threading.Event() for name in ("held", "g", "m", "go_g", "go_m")}
    taken = []
    real = result_merge._retry_released
    monkeypatch.setattr(result_merge, "_retry_released",
                        lambda p, g, seen: taken.append((p, g)) or real(p, g, seen))

    def job():
        ctx = lock_manager.new_context("req")
        g = lock_manager.acquire("G", PROJECT, group_id=sc.flow, holder_kind="review_action", ctx=ctx)
        m = lock_manager.acquire("M", PROJECT, holder_kind="publish", ctx=ctx)
        assert g.ok and m.ok
        steps["held"].set()
        steps["go_g"].wait(60)
        lock_manager.release(ctx, g.lock_key)
        steps["g"].set()
        steps["go_m"].wait(60)
        lock_manager.release(ctx, m.lock_key)
        steps["m"].set()

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert steps["held"].wait(30)
    approve(sc)
    rec.step("busy")
    steps["go_g"].set()
    assert steps["g"].wait(30)
    row = rec.step("g_released")
    assert taken == [] and row["integration"] == PENDING, (taken, row)
    steps["go_m"].set()
    assert steps["m"].wait(60)
    t.join(30)
    expect_recovered("a", rec.step("m_released"), sc)
    assert (PROJECT, sc.flow) in taken
