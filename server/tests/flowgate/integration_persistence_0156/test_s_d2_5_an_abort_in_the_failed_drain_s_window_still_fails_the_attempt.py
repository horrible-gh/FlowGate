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

def test_s_d2_5_an_abort_in_the_failed_drain_s_window_still_fails_the_attempt(env, decided, repo, tap, record,
                                                                              monkeypatch):
    """S-D2-5, the order an earlier review found: D2-b's conflict record raises; ``integrate``'s finally has dropped
    ``_opened`` while the drain still owns the Flow (its ``except`` not reached yet) when a person aborts the session
    on another thread. That close cannot own the Flow (``_as_drain_owner`` False) and its ``retry`` only marks
    ``_again``, which the failed drain's cleanup drops. The close is still the attempt's: ``failed``
    (``conflict_aborted``), no second session, no merge — and the integration in progress is never reclaimed while
    the drain owns the Flow (``_recover_one`` never called in the window)."""
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5_window", sc)
    Inject(monkeypatch, TARGETS["b"], on_fire=lambda: rec.step("inject"))    # transient: later records work
    calls = _recover_spy(monkeypatch)
    seen: dict = {}
    real = result_merge.integrate

    def integrate(attempt_id):
        try:
            return real(attempt_id)
        except Exception:
            if seen:
                raise
            seen["owned"] = sc.flow in result_merge._draining
            seen["opened"] = attempt_id in result_merge._opened
            rec.step("window")
            box: dict = {}

            def person():
                try:
                    box["merge_id"] = person_aborts(sc)        # its notice: session_closed + the G release's retry
                except BaseException as exc:  # noqa: BLE001
                    box["error"] = exc

            t = threading.Thread(target=person, daemon=True)
            t.start()
            t.join(60)
            seen["alive"] = t.is_alive()
            seen["error"] = repr(box.get("error"))
            seen["merge_id"] = box.get("merge_id")
            seen["again"] = result_merge._again.get(sc.flow)
            seen["calls_in_window"] = list(calls)
            rec.step("abort_in_window", merge_id=box.get("merge_id"))
            raise

    monkeypatch.setattr(result_merge, "integrate", integrate)
    approve(sc)
    rec.step("F1")
    lock_release(sc.flow)
    rec.step("F2")                                       # (no command: a person's step retries a failed one)
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")
    assert seen.get("owned") is True and seen.get("opened") is False, seen          # the window was reached
    assert seen["alive"] is False and seen["error"] == "None" and seen["merge_id"] is not None, seen
    assert seen["calls_in_window"] == [], seen                                      # the owned drain kept
    assert rec.at("window")["integration"] == INTEGRATING, rec.at("window")
    merge_id = seen["merge_id"]
    for label in ("F1", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")], row                      # never opened again
        assert row["merges"] == 0 and not row["merge_head"], row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")
    assert sc.attempt not in calls, calls                   # judged as the close, never the recovery's re-merge
