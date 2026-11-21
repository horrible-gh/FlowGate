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

def test_remote_advances_again_at_repush_until_bounded_user_intervention(flow):
    f = flow
    sha, ref = approve_failed(f, 'unclassified')
    f.fault['enabled'] = False
    tips = [advance_remote(f, 'first')]
    def race():
        tips.append(advance_remote(f, str(len(tips))))
    f.fault['race'] = race
    for cycle in range(gs.RECONCILE_MAX_CYCLES):
        due(f.mid)
        sweep(f, f.instance_a if cycle % 2 == 0 else f.instance_b)
        assert_pending_safety(f)
        if context(f.mid)['reconciliation_kind'] in gs.RECONCILE_USER_INTERVENTION_KINDS:
            break
    ctx = context(f.mid)
    assert len(tips) >= 3  # initial advance, then actual lease races after integration
    assert ctx['reconciliation_kind'] == 'reconcile_retry_exhausted'
    assert ctx['reconcile_cycle_count'] <= gs.RECONCILE_MAX_CYCLES
    assert len(ctx['integration_commits']) == gs.RECONCILE_MAX_INTEGRATIONS
    assert ctx['reconcile_next_at'] is None
    assert rev(f.origin['bare'], 'main') == tips[-1]
    assert rev(f.root, ref) == sha and ctx['merge_commit'] == sha
    for entry in ctx['integration_commits']:
        assert rev(f.root, entry['ref']) == entry['sha']
        assert _git(['merge-base', sha, entry['sha']], cwd=f.root).strip() == sha
    calls = len(f.commands)
    sweep(f, f.instance_b)
    assert not any(c[0] == 'push' for c in f.commands[calls:])
    assert _intent(f.mid) == f.intent and _review_status(f.ac_id) == 'pending_review'
    assert gs.db_git.get_session(f.mid)['status'] == 'open'
    assert not any(a in ('--force','-f') or a.startswith('+') for c in f.commands if c[0] == 'push' for a in c)
