"""Independently selectable scenarios; shared fixtures remain in test_push_unknown_repro_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_push_unknown_repro_0153 import (
    APP_IDENT,
    FAILURES,
    PASSWORD,
    PAST,
    PROJECT,
    Path,
    SECRET,
    SimpleNamespace,
    USER,
    _final_approve,
    _git,
    _intent,
    _make_conflict_material,
    _resolve,
    _review_status,
    _seed_final_approval_group,
    advance_remote,
    approval_intent,
    approve_failed,
    assert_pending_safety,
    context,
    copy,
    datetime,
    documents,
    due,
    flow,
    gc,
    get_store,
    gs,
    instance_registry,
    invariants,
    job_of,
    json,
    live_instance,
    origin_repo,
    patch_store,
    project,
    pytest,
    recovery,
    rev,
    snapshot,
    subprocess,
    sweep,
    tmp_db,
    uuid,
)

def test_remote_ahead_preserves_both_histories_and_other_group_rows(flow):
    f = flow
    sha, ref = approve_failed(f, 'unclassified')
    f.fault['enabled'] = False
    other = advance_remote(f, 'ahead')
    due(f.mid)
    sweep(f, f.instance_a)
    tip = rev(f.origin['bare'], 'main')
    for ancestor in (sha, other):
        assert _git(['merge-base', ancestor, tip], cwd=f.root).strip() == ancestor
    assert len(context(f.mid)['integration_commits']) == 1
    invariants(f, sha, ref, done=True, integrated=True)
    sweep(f, f.instance_b)
    invariants(f, sha, ref, done=True, integrated=True)
