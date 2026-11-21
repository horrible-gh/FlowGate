"""Independently selectable scenarios; shared fixtures remain in test_git_orphan_claim_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_orphan_claim_0153 import (
    AT,
    B,
    CLAIM_LOG,
    EVIDENCE_ALONE,
    EVIDENCE_NAME,
    FUTURE,
    H,
    INSTANCES,
    INTENT,
    INTENT_CTX,
    LATER,
    M,
    OLD,
    ORPHAN_VERDICTS,
    OTHER_NODE,
    PAST,
    PROJECT,
    PUSH_RESULTS,
    Path,
    ProcessDeath,
    READ_FAILURES,
    RESUMABLE,
    RESUME_FACTS,
    SERVER,
    X,
    _age,
    _columns,
    _die_mid_reconcile,
    _disk_only_evidence_session,
    _facts,
    _fresh_claim_thread_state,
    _hours_ago,
    _only,
    _take_over,
    approval_intent,
    approved,
    assert_consistent,
    assert_lease_only,
    assert_untouched_by_guard,
    assert_writes_guarded,
    ast,
    branch_merge_publish,
    claim_row,
    cleanup,
    clock,
    common,
    conflict_session,
    count,
    db_claim,
    env,
    gc,
    get_store,
    git,
    git_rc,
    gs,
    inspect,
    legacy_incident_session,
    locks,
    logging,
    merge_aborts,
    merge_commit,
    names,
    og,
    on,
    open_session,
    origin_main,
    patch_store,
    plain_orphan_session,
    plant_claim,
    preserve,
    preserve_refs,
    project,
    push_rejected,
    push_timeout,
    pushes,
    pytest,
    pytestmark,
    re,
    ref_value,
    register_instances,
    registry,
    remote_commit,
    repo,
    saved,
    session_claim,
    subprocess,
    t4,
    tmp_db,
    to_conflict,
    unknown_session,
    unmerged,
)

def test_R3_startup_holds_the_incident_shape_and_a_later_sweep_changes_nothing(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, ref = legacy_incident_session(t)
    env.clock['instance'] = 'inst_C'
    t.clear()
    gs.startup_recovery()
    held = saved(mid)
    assert held['recovery_hold']['trigger'] == 'startup_orphan'
    assert held['recovery_hold']['instance_id'] == 'inst_C'
    assert_untouched_by_guard(t, mid, held)
    assert len(t.of('write', mid)) == 1
    assert_writes_guarded(t, mid)
    env.clock['instance'] = 'inst_B'
    t.clear()
    gs.merge_session_sweep()
    assert saved(mid) == held and t.of('write') == [] and env.commands == []
    assert gs.db_git.get_session(mid)['status'] == 'open'
def test_S9_claim_lost_while_waiting_for_the_reconcile_lock_writes_nothing(t4):
    """The reconcile takes its claim before the lock; the lease runs out while it waits and
    inst_B takes the claim over. Under the lock nothing is changed: no cycle counter, no
    preservation gate, no remote contact."""
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    ctx_before = saved(mid)
    refs_before = preserve_refs(r.work)

    def reclaimed_while_waiting(lock_mid, holder_kind, mode):
        if lock_mid == mid and holder_kind == 'review_action':
            _take_over(mid)
    t.lock_hooks.append(reclaimed_while_waiting)
    t.clear()
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out == {'ok': True, 'result': {'status': 'claim_lost', 'stage': 'reconcile_lock'}}
    assert saved(mid) == ctx_before
    assert env.commands == [] and env.probes == [] and t.of('write') == []
    assert preserve_refs(r.work) == refs_before and git(['rev-parse', 'HEAD'], r.work) == m
    row = claim_row(mid)                                  # inst_B's claim stands in the DB
    assert (row['claim_token'], row['owner_instance_id']) == ('b-token', 'inst_B')
    assert [e[2] for e in t.of('lock')] == ['review_action'] and len(t.of('unlock')) == 1
@pytest.mark.parametrize('result', sorted(PUSH_RESULTS))
def test_S10_claim_lost_during_the_push_leaves_no_write_after_it(t4, result):
    """The push outlives the lease and inst_B takes the claim over; the push then returns a
    timeout / an auth refusal / a lease rejection. Nothing of inst_A is written after the
    push: no audit entry, no last_error, no retry schedule, no user intervention."""
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    at_push = {}

    def taken_over_during_push(args):
        if args and args[0] == 'push' and not at_push:
            at_push['ctx'] = saved(mid)
            later = registry.add_seconds(AT, 700)
            env.clock['now'] = later                      # inst_A's lease (600 s) ran out
            _take_over(mid, now=later)
            return PUSH_RESULTS[result](args)
        return None
    env.hooks.append(taken_over_during_push)
    t.clear()
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out == {'ok': True, 'result': {'status': 'claim_lost', 'stage': 'after_reconcile_repush'}}
    assert len(pushes(env)) == 1
    after = saved(mid)
    assert after == at_push['ctx']
    assert after['reconciliation_kind'] == 'push_remote_unknown' and after['last_error'] == before['last_error']
    assert after.get('ref_operations') == at_push['ctx'].get('ref_operations')
    row = claim_row(mid)
    assert (row['claim_token'], row['owner_instance_id']) == ('b-token', 'inst_B')
    assert_writes_guarded(t, mid)                         # every landed write was under a live claim
    assert git(['rev-parse', 'HEAD'], r.work) == m and ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
@pytest.mark.parametrize('change', ['completed', 'aborted', 'user_intervention'])
def test_R4_startup_judges_the_row_re_read_under_its_lock_not_the_listed_snapshot(t4, monkeypatch, change):
    """Between startup's list read and its claim/lock, the session is completed by another
    instance, aborted by a person, or parked for a person. Startup decides on the row it
    re-reads under its lock: nothing is re-affirmed, closed, held or written."""
    t, env, r = t4, t4.env, t4.r
    if change == 'user_intervention':
        mid, m, _ref = legacy_incident_session(t)
    else:
        mid, m, before = unknown_session(env)
        to_conflict(t, mid)
        _only(env, mid)
    real_list = gs.db_git.list_open_sessions
    settled = {}

    def listed():
        rows = real_list()
        ctx = saved(mid)
        if change == 'completed':
            ctx.update(review_state=gs.REVIEW_STATE_COMPLETED, apply_phase='completed', published_commit=m)
            ctx.pop('reconciliation_kind', None)
            ctx.pop('reconcile_next_at', None)
            gs.db_git.set_session_context(mid, ctx)
            gs.db_git.close_session(mid, 'done')
            gs.db_git.set_status(env.group, 'merged', merge_id=mid)
        elif change == 'aborted':
            ctx['review_state'] = None
            ctx.pop('reconciliation_kind', None)
            ctx.pop('reconcile_next_at', None)
            gs.db_git.set_session_context(mid, ctx)
            gs.db_git.close_session(mid, 'aborted')
            gs.db_git.set_status(env.group, 'waiting')
        else:
            ctx.update(review_state=gs.REVIEW_STATE_RECONCILING, reconciliation_kind='reconcile_auth_required',
                       reconcile_next_at=None,
                       last_error={'code': 'reconcile_auth_rejected', 'stage': 'push', 'instance_id': 'inst_B'})
            gs.db_git.set_session_context(mid, ctx)
        settled.update(ctx=saved(mid), group=gs.db_git.get_state(env.group)['status'],
                       status=gs.db_git.get_session(mid)['status'], n=len(t.events))
        env.commands.clear()
        return rows
    monkeypatch.setattr(gs.db_git, 'list_open_sessions', listed)
    env.clock['instance'] = 'inst_C'
    t.clear()
    gs.startup_recovery()
    assert settled, 'startup read its list'
    assert saved(mid) == settled['ctx']
    assert gs.db_git.get_state(env.group)['status'] == settled['group']
    assert gs.db_git.get_session(mid)['status'] == settled['status']
    assert env.commands == []
    later = t.events[settled['n']:]
    assert [e for e in later if e[0] in ('write', 'close', 'discard', 'close_session_attempt',
                                         'invalidate_checkpoint') and e[1] == mid] == []
    assert [k for k, _p in t.emits] == []
    locks_taken = [e for e in later if e[0] == 'lock' and e[1] == mid]
    assert locks_taken and locks_taken[0][2:5] == ('sweep', locks.NO_WAIT, 'inst_C')   # startup's own lock
    assert len(locks_taken) == len([e for e in later if e[0] == 'unlock' and e[1] == mid])
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_C' and claim_row(mid)['claim_token'] is None
    if change != 'user_intervention':
        assert claim_row(mid)['last_outcome'] == 'skipped'
@pytest.mark.parametrize('failure', [None, 'rc128', 'timeout'])
def test_S11_an_unreadable_preserve_ref_listing_never_closes_the_session(t4, failure):
    t, env, r = t4, t4.env, t4.r
    mid = _disk_only_evidence_session(t, merge_in_progress=False)
    if failure:
        env.hooks.append(on('for-each-ref', READ_FAILURES[failure]))
    before = saved(mid)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)              # open, intent kept, no close/discard/abort
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == r.B
    if failure is None:                                   # control: the disk ref alone is evidence
        assert after['recovery_hold']['evidence'] == ['preserve_ref_on_disk']
        return
    assert after == before and t.of('write', mid) == []
    assert claim_row(mid)['last_outcome'] == 'unobserved'
@pytest.mark.parametrize('failure', [None, 'rc128', 'timeout'])
def test_S11_ttl_with_an_unreadable_preserve_ref_listing_aborts_nothing(t4, failure):
    t, env, r = t4, t4.env, t4.r
    mid = _disk_only_evidence_session(t, merge_in_progress=True)
    _age(mid)
    if failure:
        env.hooks.append(on('for-each-ref', READ_FAILURES[failure]))
    before = saved(mid)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)
    assert ref_value(r.work, 'MERGE_HEAD') == r.F
    if failure is None:
        assert after['recovery_hold']['trigger'] == 'sweep_ttl'
        assert after['recovery_hold']['evidence'] == ['preserve_ref_on_disk']
        return
    assert after == before and t.of('write', mid) == []
    assert claim_row(mid)['last_outcome'] == 'unobserved'
@pytest.mark.parametrize('case', ['activity', 'review_activity', 'review_cap'])
def test_S12_activity_between_the_sweep_read_and_the_ttl_lock_is_honoured(t4, case):
    t, env, r = t4, t4.env, t4.r
    git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    ctx = {'base_head': r.B, 'merge_head': r.F, approval_intent.INTENT_KEY: dict(INTENT)}
    if case != 'activity':
        ctx['review_state'] = gs.REVIEW_STATE_PENDING
    mid = gs.db_git.create_session(env.group, ['f.txt'], finalize_action='merge', context=ctx)
    to_conflict(t, mid)
    _only(env, mid)
    created, touched = {'activity': (OLD, OLD), 'review_activity': (_hours_ago(30), _hours_ago(25)),
                        'review_cap': (_hours_ago(cleanup.REVIEW_PENDING_MAX_HOURS + 8), _hours_ago(25))}[case]
    get_store()._execute('UPDATE git_merge_session SET created_at = ?, touched_at = ? WHERE merge_id = ?',
                         [created, touched, mid])
    assert cleanup._session_ttl_expired(gs.db_git.get_session(mid), ctx.get('review_state'))   # the sweep's read
    before = saved(mid)

    def person_opens_it(lock_mid, holder_kind, mode):
        if lock_mid == mid and holder_kind == 'sweep':
            gs.db_git.touch_session(mid)                  # the conflict list / review is opened meanwhile
    t.lock_hooks.append(person_opens_it)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    if case == 'review_cap':
        # activity cannot lift the review wait's cap from creation: the old abort still runs
        assert len(merge_aborts(env)) == 1 and gs.db_git.get_session(mid)['status'] == 'aborted'
        assert after[approval_intent.DISCARDED_KEY]['reason'] == 'ttl_expired'
        assert claim_row(mid)['last_outcome'] == 'aborted'
        return
    assert merge_aborts(env) == [] and ref_value(r.work, 'MERGE_HEAD') == r.F
    assert_untouched_by_guard(t, mid, after)
    assert after == before and t.of('write', mid) == [] and 'recovery_hold' not in after
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert claim_row(mid)['last_outcome'] == 'ttl_not_expired'
