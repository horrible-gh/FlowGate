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

def test_s_d3c_3_a_session_closed_inside_a_transaction(env, decided, repo, tap, record):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db.connection import get_store
    sc = scene(env, repo, conflict=True)
    rec = record("S-D3c-3", sc)
    approve(sc)
    merge_id = open_session(sc)
    assert merge_id is not None and rec.step("conflict")["integration"] == CONFLICT
    _git(repo.wt(sc.flow), "merge", "--abort")
    done = threading.Event()

    def closer():                                          # its own thread: nothing it holds is released after
        with get_store().transaction():
            db_git.close_session(merge_id, "aborted")
        done.set()

    t = threading.Thread(target=closer, daemon=True)
    t.start()
    assert done.wait(30)
    t.join(10)
    row = rec.step("closed_in_transaction")
    assert row["integration"] == CONFLICT and row["sessions"] == [(merge_id, "aborted")], row
    lock_release(sc.flow)
    row = rec.step("lock_release")
    # 0156 T#3 Q3: the first G release settles the lost close; no restart or new merge.
    assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
    assert row["merges"] == 0 and not row["merge_head"], row
    assert not [session for session in row["sessions"] if session[1] == "open"], row
