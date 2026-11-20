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

def test_s_d3b_2_a_claim_ended_with_its_session_open_is_retried_by_the_lock_release_that_follows(env, decided, repo,
                                                                                                    tap, record):
    """``complete_reviewed`` / ``_complete_merge_review`` write ``review_state=completed`` and close the attempt in the
    same call under the caller's Git lock; the claim ends with the session still open only in between."""
    from modules.flow_gate.db import git_integration as db_git
    sc = scene(env, repo)
    rec = record("S-D3b-2", sc)
    merge_id = _claim(sc.flow, repo.flows[sc.flow].branch)
    approve(sc)
    row = rec.step("claimed")
    assert (row["integration"], row["reason"]) == (PENDING, "git_busy"), row
    holder = HoldG(sc.flow, "branch_merge")             # the review's caller holds the target's lock
    context = db_git.session_context(db_git.get_session(merge_id))
    db_git.set_session_context(merge_id, {**context, "review_state": "completed"})
    row = rec.step("claim_ended")
    assert (row["integration"], row["reason"]) == (PENDING, "git_busy"), row
    holder.release()
    expect_recovered("a", rec.step("released"), sc)
