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

def test_s_safe_4_no_new_thread_timer_sweep_and_the_recovery_rule_is_called_not_copied():
    merge = _source("flowgate/graph_run/result_merge.py")
    stop_src = _source("flowgate/graph_run/stop.py")
    for src in (merge, stop_src):
        assert "Thread(" not in src and "Timer(" not in src and "time.sleep" not in merge
    assert merge.count("def _recover_one(") == 1
    reclaim = merge[merge.index("def reclaim_integrating("):]
    reclaim = reclaim[:reclaim.index("\ndef ", 1)]
    assert "_recover_one(row)" in reclaim and "merge --abort" not in reclaim and "_merge_commit_of" not in reclaim
    retry = merge[merge.index("def retry("):]
    retry = retry[:retry.index("\ndef ", 1)]
    assert retry.index("reclaim_integrating(") < retry.index("_closed_conflicts(")
    advance = stop_src[stop_src.index("def advance("):]
    advance = advance[:advance.index("\ndef ", 1)]
    assert advance.index("reclaim_integrating(") < advance.index("abort_for_stop(")
    for rel in ("flowgate/graph_run/settle.py", "modules/flow_gate/graph_workflow/api/run_routes.py",
                "modules/flow_gate/graph_workflow/services/run_service.py"):
        assert "reclaim_integrating" not in _source(rel), rel
    assert "integrate(attempt_id)" in retry                 # integrate runs only inside the drain
