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

def test_s_d3d_2_a_hold_left_with_no_waiting_integration_is_taken_by_the_next_lock_release(env, decided, repo, tap,
                                                                                         record, monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_service
    sc = scene(env, repo)
    rec = record("S-D3d-2", sc)
    prefail(sc)
    real = run_service.go_on_command
    fails = {"n": 0}

    def go_on(ctx, command_, policies):
        if fails["n"] == 0:
            fails["n"] += 1
            raise RuntimeError("root_binding_lost")
        return real(ctx, command_, policies)

    monkeypatch.setattr(run_service, "go_on_command", go_on)
    Inject(monkeypatch, [("_release_hold.<locals>.body", None)], on_fire=lambda: rec.step("inject"))
    drive(sc, "h2", monkeypatch)
    row = rec.step("F1")                                   # F1: the drain's own G release, at its end
    assert row["integration"] == INTEGRATED and fails["n"] == 1, row
    lock_release(sc.flow)
    row = rec.step("F2")
    assert row["hold"] is None, row
    attempts = _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE run_id = ? AND node_key = ?",
                    (sc.run_id, sc.next))[0]["n"]
    assert attempts <= 1                                   # no second dispatch of the next card
    lock_release(sc.flow)
    assert rec.step("F2_again")["hold"] is None
