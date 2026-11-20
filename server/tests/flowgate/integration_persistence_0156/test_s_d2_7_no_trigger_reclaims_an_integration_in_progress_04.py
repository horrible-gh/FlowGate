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

@pytest.mark.parametrize('ending', ['finish_fails'])
@pytest.mark.parametrize('with_stop', [True])
def test_s_d2_7_no_trigger_reclaims_an_integration_in_progress(env, decided, repo, tap, record, monkeypatch,
                                                                with_stop, ending):
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo)
    rec = record(f"S-D2-7_{'stop' if with_stop else 'run'}_{ending}", sc)
    gate, entered = _held_merge(monkeypatch)
    calls = _recover_spy(monkeypatch)
    if ending == "finish_fails":
        Inject(monkeypatch, TARGETS["a"], on_fire=lambda: rec.step("inject"))
    worker = threading.Thread(target=approve, args=(sc,), daemon=True)
    worker.start()
    assert entered.wait(60)                                # thread A: inside _merge, under G, before the merge
    rec.step("held")
    result_merge.request(sc.flow)                          # thread B: every trigger
    result_merge._retry_released(PROJECT, sc.flow, set())
    result_merge.on_lock_released(SimpleNamespace(domain="W", project_id=PROJECT, group_id=None))
    if with_stop:
        rec.step("stop", code=stop(sc))
        assert (rec.at("stop")["run"], rec.at("stop")["integration"]) == ("stopping", INTEGRATING), rec.at("stop")
        stop_module.advance(sc.flow, sc.run_id)
    rec.step("triggered")
    assert calls == [] and rec.at("triggered")["integration"] == INTEGRATING
    gate.set()
    worker.join(120)
    assert not worker.is_alive()
    row = rec.step("end")
    assert (row["integration"], row["node"], row["merges"]) == (INTEGRATED, "done", 1), row
    assert row["run"] != "stopping" and len(row["sessions"]) == 0, row
    if with_stop:
        assert (row["run"], row["idle_reason"]) == ("idle", "stopped"), row
    if ending == "finish_fails":
        assert calls == [sc.attempt], calls                # reclaimed once A left the drain (its dispatch)
    else:
        assert calls == [], calls
