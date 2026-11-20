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

def test_s_d2_7_a_session_just_opened_is_never_reclaimed(env, decided, repo, tap, record, monkeypatch):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.services.git import conflict
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-7_session", sc)
    real = conflict.open_tr_conflict_session
    gate, entered = threading.Event(), threading.Event()

    def opened(*args, **kwargs):
        out = real(*args, **kwargs)
        entered.set()
        assert gate.wait(120)
        return out

    monkeypatch.setattr(conflict, "open_tr_conflict_session", opened)
    calls = _recover_spy(monkeypatch)
    worker = threading.Thread(target=approve, args=(sc,), daemon=True)
    worker.start()
    assert entered.wait(60)
    result_merge.request(sc.flow)
    result_merge._retry_released(PROJECT, sc.flow, set())
    rec.step("triggered")
    assert calls == [] and rec.at("triggered")["integration"] == INTEGRATING
    gate.set()
    worker.join(120)
    row = rec.step("end")
    merge_id = open_session(sc)
    assert (row["integration"], row["reason"]) == (CONFLICT, f"merge_session:{merge_id}") and len(row["sessions"]) == 1
