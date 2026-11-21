"""Independently selectable scenarios; shared fixtures remain in test_git_preserve_ref_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_preserve_ref_0153 import (
    AT,
    IDENTITY,
    INTENT,
    LATER,
    NEXT_AT,
    PRESERVE_FAILED,
    PROJECT,
    Path,
    RETAIN,
    SimpleNamespace,
    _Crash,
    _exists,
    _expire_and_gc,
    _only,
    _read_error,
    _remote_breaks_json,
    approval_intent,
    approve,
    approve_ready,
    approved,
    assert_parked,
    assert_parked_approval_response,
    branch_merge,
    branch_merge_publish,
    done,
    env,
    git,
    git_rc,
    groups,
    gs,
    merge_commit,
    names,
    on,
    open_session,
    parents_of,
    patch_store,
    preserve,
    preserve_refs,
    project,
    projects,
    publish,
    push_rejected,
    push_timeout,
    pytest,
    pytestmark,
    ref_value,
    reflog,
    repo,
    saved,
    shutil,
    subprocess,
    tmp_db,
    unknown_session,
    uuid,
)

def test_rejected_then_reapproved_pins_round_one_with_previous_commit_as_parent(env):
    r = env.repo
    other = r.tmp / 'other'
    git(['clone', str(r.bare), str(other)], r.tmp)
    git(['config', 'user.name', 'other'], other)
    git(['config', 'user.email', 'other@local'], other)
    (other / 'f.txt').write_text('concurrent\n', encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-m', 'concurrent'], other)
    git(['push', 'origin', 'main'], other)
    c = git(['rev-parse', 'HEAD'], other)
    mid, fp = approve_ready(env)
    out = approve(env, mid, 'att-1', fp)
    assert out['result']['status'] == 'reconciling', out
    assert 'reset' not in names(env)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'returned_to_resolver', out
    first = saved(mid)
    r0 = f'refs/flowgate/preserve/{mid}'
    m0 = first['preserve_history'][0]['sha']
    assert ref_value(r.work, r0) == m0 and parents_of(r.work, m0) == [r.B, r.F]
    assert ref_value(r.work, 'MERGE_HEAD') == m0 and ref_value(r.work, 'HEAD') == c
    assert git(['diff', '--name-only', '--diff-filter=U'], r.work) == 'f.txt'
    (r.work / 'f.txt').write_text('feature\nconcurrent\n', encoding='utf-8')
    git(['add', 'f.txt'], r.work)
    snapshot = gs._freeze_commit_candidate(r.work, 'main')
    first.update(snapshot, review_state=gs.REVIEW_STATE_PENDING)
    gs.db_git.set_session_context(mid, first)
    out = approve(env, mid, 'att-2', snapshot['review_fingerprint'])
    assert out['result']['status'] == 'merged', out
    m1 = git(['rev-parse', 'HEAD'], r.work)
    r1 = f'refs/flowgate/preserve/{mid}-r1'
    assert ref_value(r.work, r1) == m1
    assert ref_value(r.work, r0) == m0
    assert parents_of(r.work, m1) == [c, m0]
    after = saved(mid)
    assert after['preserve_ref'] == r1 and after['preserve_round'] == 1 and after['preserve_sha'] == m1
    assert after['preserve_parents'] == [c, m0]
    assert len(after['preserve_history']) == 1 and after['preserve_history'][0]['ref'] == r0
    assert git(['rev-parse', 'main'], r.bare) == m1
def test_a_different_commit_for_the_open_round_is_a_conflict_not_a_round(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m, preserve_sha='d' * 40,
                                     preserve_ref='refs/flowgate/preserve/1'))
    out = publish(env, mid)
    assert out['result'] == PRESERVE_FAILED
    after = saved(mid)
    assert after['last_error']['reason'] == 'ref_conflict' and after.get('preserve_round', 0) == 0
    assert preserve_refs(r.work) == {}
def test_crash_after_update_ref_before_context_write_is_recovered_as_same(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m, review_state=gs.REVIEW_STATE_RECONCILING,
                                     reconciliation_kind='push_remote_unknown'))
    real_record = preserve.record_preserved

    def crash(*a, **kw):
        raise _Crash()
    env.monkeypatch.setattr(preserve, 'record_preserved', crash)
    env.remote(None)
    with pytest.raises(_Crash):
        gs.reconcile_push_session(mid, trigger='server_startup')
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m
    assert 'preserve_sha' not in saved(mid)
    env.monkeypatch.setattr(preserve, 'record_preserved', real_record)
    env.commands.clear()
    env.clock.update(now=LATER, instance='inst_B')
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'retry_scheduled'
    assert 'update-ref' not in names(env)
    after = saved(mid)
    assert after['preserve_sha'] == m and after['preserve_ref'] == ref and after['preserved_at'] == LATER
    assert len(reflog(r.work, ref)) == 1
def test_push_timeout_then_restart_on_another_instance_reuses_the_ref(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    _only(env, mid)
    env.clock.update(now=LATER, instance='inst_B')
    env.remote(lambda a: git(['rev-parse', 'main'], r.bare))
    # The completed row is closed by the attempt close in production; the sweep's
    # completed-row finisher is outside this contract.
    from modules.flow_gate.services.git import cleanup
    env.monkeypatch.setattr(cleanup, '_finish_completed_review', lambda *a, **kw: None)
    gs.startup_recovery()
    after = saved(mid)
    assert len(env.probes) == 1
    # the unchanged remote gets the same preserved commit re-pushed.
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED
    assert names(env).count('reset') == 0 and names(env).count('push') == 1
    assert 'update-ref' not in names(env)
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m and len(reflog(r.work, ref)) == 1
    assert after['preserved_at'] == AT
    assert after['ref_operations'][-1]['instance_id'] == 'inst_B'
def test_preserve_failed_survives_startup_and_sweeps_untouched(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B], r.work)
    publish(env, mid)
    parked = saved(mid)
    _only(env, mid)
    env.remote(r.B)
    env.commands.clear()
    for _ in range(2):
        gs.startup_recovery()
        gs.merge_session_sweep()
    after = saved(mid)
    assert gs.db_git.get_session(mid)['status'] == 'open'
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after['reconciliation_kind'] == 'preserve_failed'
    assert after['merge_commit'] == m and approval_intent.intent_of_context(after) == INTENT
    assert after['last_error'] == parked['last_error']
    assert env.probes == []
    for forbidden in ('reset', 'push', 'update-ref', 'merge'):
        assert forbidden not in names(env)
    assert git(['rev-parse', 'HEAD'], r.work) == m
def test_ref_outlives_push_success_reconcile_completion_and_close(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    publish(env, mid)
    gs.db_git.close_session(mid, 'done')
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
