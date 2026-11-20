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

@pytest.mark.parametrize('mode', ['persistent'])
@pytest.mark.parametrize('hold', ['h2'])
@pytest.mark.parametrize('point', ['e'])
def test_s_d2_matrix(env, decided, repo, tap, record, monkeypatch, point, hold, mode):
    """S-D2-1 (a, transient), S-D2-2 (a, persistent), S-D2-3 (b), S-D2-4 (c), D2-d, S-D2-9 (e): the failure at the
    point, then F1 (the failure's own dispatch; for c: the blocking G's release), [release], F2 (an unrelated G
    release), F3 (a command), F4 (Stop, Stop again, a G release), F5 (the server start's recovery, the control)."""
    sc = scene(env, repo, conflict=point in ("b", "d"))
    rec = record(f"D2-{point}_{hold}_{mode}", sc)
    rec.step("scene")
    if hold == "h2":
        prefail(sc)
        rec.step("prefail")
    blocker = HoldG(sc.flow) if point == "c" else None
    injection = arm(point, monkeypatch, sc, persistent=mode == "persistent", on_fire=lambda: rec.step("inject"))
    drive(sc, hold, monkeypatch)
    rec.step("drive")
    if blocker is not None:
        blocker.release()                                   # c: the release of the G that made it busy
    rec.step("F1")
    if mode == "persistent":
        injection.release()
        rec.step("release")
    lock_release(sc.flow)
    rec.step("F2")
    rec.step("F3", code=command(sc))
    codes = [stop(sc)]
    rec.step("F4-stop", code=codes[-1])
    codes.append(stop(sc))
    lock_release(sc.flow)
    rec.step("F4", codes=codes)
    from flowgate.graph_run import result_merge, stop as stop_module
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")

    assert rec.at("inject")["integration"] in (INTEGRATING, CONFLICT, INTEGRATED), rec.at("inject")
    expect_recovered(point, rec.at("F1" if mode == "transient" else "F2"), sc)
    if point == "d" and hold == "h3":
        settled = rec.at("F1" if mode == "transient" else "F2")
        assert (settled["run"], settled["idle_reason"]) == ("idle", "merge_conflict"), settled
    runtime = rec.at("F4")                                  # no restart: the runtime triggers alone
    assert runtime["integration"] != INTEGRATING and runtime["run"] != "stopping", runtime
    for row in rec.rows:                                    # S-SAFE-1 / S-SAFE-2 / S-SAFE-3
        assert row["merges"] <= 1 and len([s for s in row["sessions"] if s[1] == "open"]) <= 1, row
        assert not row["merge_head"] or any(s[1] == "open" for s in row["sessions"]), row
    for row in rec.rows[:[r["step"] for r in rec.rows].index("F3")]:
        assert len(row["sessions"]) <= 1, row              # no trigger but a command merges a failed one again
    if point in ("a", "c", "e"):
        assert runtime["node"] == "done" and runtime["result_in_branch"], runtime   # done stays done
