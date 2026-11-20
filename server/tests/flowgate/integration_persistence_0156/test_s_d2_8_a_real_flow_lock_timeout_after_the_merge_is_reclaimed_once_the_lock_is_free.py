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

def test_s_d2_8_a_real_flow_lock_timeout_after_the_merge_is_reclaimed_once_the_lock_is_free(env, decided, repo, tap,
                                                                                            record, monkeypatch):
    from modules.flow_gate.graph_workflow.services import flow_guard
    sc = scene(env, repo)
    rec = record("S-D2-8", sc)
    grab, grabbed, let_go = threading.Event(), threading.Event(), threading.Event()

    def holder():
        assert grab.wait(120)
        lock = flow_guard._flow_lock(sc.flow)
        lock.acquire()
        grabbed.set()
        let_go.wait(120)
        lock.release()

    contender = threading.Thread(target=holder, daemon=True)
    contender.start()
    gate, _entered = _held_merge(monkeypatch, after_merge=lambda: (grab.set(), grabbed.wait(60)))
    gate.set()
    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 1)
    try:                                                   # the merge commits, then (e) times out on the Flow lock
        approved = approve(sc, check=False)
    except Exception as exc:  # noqa: BLE001 — recorded: the approval's own guards may time out too
        approved = type(exc).__name__
    rec.step("F1", approved=str(approved))
    let_go.set()
    contender.join(30)
    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 30)
    lock_release(sc.flow)
    row = rec.step("F2")
    assert rec.at("F1")["merges"] == 1 and "Graph attempt integration failed" in rec.at("F1")["logs"]
    expect_recovered("a", row, sc)
