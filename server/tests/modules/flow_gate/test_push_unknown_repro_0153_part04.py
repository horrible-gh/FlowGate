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

def test_real_preserve_ref_lock_keeps_commit_intent_and_other_group(flow):
    f = flow
    gitdir = Path(_git(['rev-parse', '--git-common-dir'], cwd=f.root).strip())
    if not gitdir.is_absolute():
        gitdir = f.root / gitdir
    lock = gitdir / 'refs' / 'flowgate' / 'preserve' / (str(f.mid) + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('actual ref lock\n', encoding='utf-8')
    try:
        gs.approve_merge_review(f.group, f.mid, attempt_id=str(uuid.uuid4()),
            review_fingerprint=f.fingerprint, authority='human')
        ctx = context(f.mid)
        sha = ctx['merge_commit']
        assert rev(f.root, 'HEAD') == sha and rev(f.root, 'MERGE_HEAD') is None
        assert ctx['reconciliation_kind'] == 'preserve_failed'
        assert rev(f.root, f'refs/flowgate/preserve/{f.mid}') is None
        assert not any(c[0] in ('push','reset') for c in f.commands)
        assert rev(f.origin['bare'], 'main') == f.base
        sweep(f, f.instance_a)
        assert_pending_safety(f)
        sweep(f, f.instance_b)
        assert_pending_safety(f)
        assert context(f.mid)['merge_commit'] == sha
        assert gs.db_git.get_session(f.mid)['status'] == 'open'
        assert _intent(f.mid) == f.intent
        assert _review_status(f.ac_id) == 'pending_review'
        assert _review_status(f.root_id) == 'wf_in_progress'
        assert not f.consumes
        assert not any(c[0] in ('push','reset') for c in f.commands)
    finally:
        lock.unlink()
