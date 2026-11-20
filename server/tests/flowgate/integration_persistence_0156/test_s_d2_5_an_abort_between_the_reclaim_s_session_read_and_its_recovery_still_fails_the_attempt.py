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

def test_s_d2_5_an_abort_between_the_reclaim_s_session_read_and_its_recovery_still_fails_the_attempt(
        env, decided, repo, tap, record, monkeypatch):
    """S-D2-5, the order of TR0008 rev 1's review: D2-b leaves the attempt ``integrating`` with its own session open
    (``_left_open``). The failed drain's own dispatch reclaims it as the drain owner: the reclaim reads that session
    open (so ``_recover_one``, not ``_settle_closed``) and, before ``_recover_one`` reads the Flow's open session again,
    a person aborts it on another thread and that close's notice ends - it cannot own the Flow (only ``_again``) and
    leaves ``_left_open``. ``_recover_one`` then finds no session and records ``pending`` (``recovered``). The close is
    still the attempt's: ``failed`` (``conflict_aborted``) before the pass retries anything - no second session, no
    merge - and ``_recover_one`` runs once, by the drain owner (never by the close: no merge in progress taken)."""
    import os

    # Abort may lazily initialize the token pepper; preserve the caller environment.
    if "FLOWGATE_AGENT_PEPPER_ID" in os.environ:
        monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", os.environ["FLOWGATE_AGENT_PEPPER_ID"])
    else:
        monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", "p1")
        monkeypatch.delenv("FLOWGATE_AGENT_PEPPER_ID")
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5_reclaim_window", sc)
    Inject(monkeypatch, TARGETS["b"], on_fire=lambda: rec.step("inject"))    # transient: later records work
    calls: list = []
    seen: dict = {}
    real = result_merge._recover_one

    def recover_one(row):
        calls.append(row["attempt_id"])
        if row["attempt_id"] == sc.attempt and not seen:
            with result_merge._opened_lock:
                left = result_merge._left_open.get(sc.attempt)
            seen["left"] = left
            seen["open_before"] = left is not None and result_merge._session_open(left[1])
            seen["owned"] = sc.flow in result_merge._draining
            seen["opened"] = sc.attempt in result_merge._opened
            rec.step("reclaim_window")
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
            with result_merge._opened_lock:
                seen["left_after_close"] = result_merge._left_open.get(sc.attempt)
            seen["calls_after_close"] = list(calls)
            rec.step("abort_in_reclaim_window", merge_id=box.get("merge_id"))
        return real(row)

    monkeypatch.setattr(result_merge, "_recover_one", recover_one)
    approve(sc)
    rec.step("F1")
    lock_release(sc.flow)
    rec.step("F2")                                       # (no command: a person's step retries a failed one)
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")
    merge_id = seen.get("merge_id")
    assert seen.get("open_before") is True and seen.get("left") == (sc.flow, merge_id), seen   # the window reached
    assert seen["owned"] is True and seen["opened"] is False, seen
    assert seen["alive"] is False and seen["error"] == "None" and merge_id is not None, seen
    assert seen["again"] is not None and seen["left_after_close"] == (sc.flow, merge_id), seen  # the close left it
    assert seen["calls_after_close"] == [sc.attempt], seen          # the close reclaimed nothing (the owner's only)
    assert rec.at("reclaim_window")["integration"] == INTEGRATING, rec.at("reclaim_window")
    assert rec.at("reclaim_window")["sessions"] == [(merge_id, "open")], rec.at("reclaim_window")
    for label in ("F1", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")], row                      # never opened again
        assert row["merges"] == 0 and not row["merge_head"], row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")
    assert calls == [sc.attempt], calls                     # one recovery, by the drain owner; never again
