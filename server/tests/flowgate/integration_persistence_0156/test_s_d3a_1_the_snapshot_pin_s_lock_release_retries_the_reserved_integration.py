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

def test_s_d3a_1_the_snapshot_pin_s_lock_release_retries_the_reserved_integration(env, decided, repo, tap, record):
    from flowgate.gate_decisions import repository as gate_repo
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3a-1", sc)
    decision = reserve(sc.flow)
    approve(sc)
    row = rec.step("reserved")
    assert (row["integration"], row["reason"]) == (PENDING, "gate_snapshot_reserved"), row
    pinned = threading.Event()

    def snapshot():                                        # the publish job's _snapshot: G and M, frozen_sha, release
        ctx = lock_manager.new_context("job", "job_gate_0156")
        held = []
        for domain, extra in (("G", {"group_id": sc.flow}), ("M", {})):
            outcome = lock_manager.acquire(domain, PROJECT, holder_kind="publish", ctx=ctx, **extra)
            assert outcome.ok, outcome
            held.append(outcome.lock_key)
        assert gate_repo.move(decision["decision_id"], decision["decision_version"],
                              frozen_sha=_git(repo.wt(sc.flow), "rev-parse", "HEAD"))
        for key in reversed(held):
            lock_manager.release(ctx, key)
        pinned.set()

    t = threading.Thread(target=snapshot, daemon=True)
    t.start()
    assert pinned.wait(60)
    t.join(30)
    row = rec.step("pinned")
    expect_recovered("a", row, sc)
