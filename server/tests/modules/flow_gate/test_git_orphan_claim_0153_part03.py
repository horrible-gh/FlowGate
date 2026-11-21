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

def test_U4_close_orphan_is_called_only_by_judge_orphan_and_both_entry_points_claim():
    source = Path(cleanup.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    callers = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', None)
                    if name == '_close_orphan':
                        callers.append(fn.name)
    assert callers == ['_judge_orphan']
    for name in ('merge_session_sweep', 'startup_recovery'):
        assert 'session_claim.held(' in inspect.getsource(getattr(cleanup, name)), name
    assert 'session_claim.acquire(' in inspect.getsource(gs.reconcile_push_session)
    # review rej_01M6ZWFPF1E6JE2M: the reconcile fences before its first change under the lock,
    # audited Git is fenced before and after the command, startup judges under its lock
    assert '"reconcile_lock"' in inspect.getsource(gs.reconcile_push_session)
    assert inspect.getsource(gs._run_session_git).count('_session_claim_require(') == 2
    assert 'session_lock(' in inspect.getsource(cleanup._startup_group_session)
def test_U4_new_sources_have_no_forbidden_operations():
    texts = {
        'orphan_guard': inspect.getsource(og),
        '_judge_orphan': inspect.getsource(cleanup._judge_orphan),
        '_orphan_facts': inspect.getsource(cleanup._orphan_facts),
        '_observe_merge_head': inspect.getsource(cleanup._observe_merge_head),
        '_record_hold': inspect.getsource(cleanup._record_hold),
    }
    for name, text in texts.items():
        for forbidden in ('merge", "--abort', 'discard_intent', 'invalidate_resolution_checkpoint_for_merge',
                          '"reset"', 'update-ref', '"push"', '"waiting"', 'ORIG_HEAD'):
            assert forbidden not in text, (name, forbidden)
    facts = inspect.getsource(cleanup._orphan_facts) + inspect.getsource(cleanup._observe_merge_head)
    for read in ('"status", "--porcelain", "--untracked-files=no"', '"for-each-ref"', 'verify_preserved',
                 '_merge_in_progress', '_rev_parse'):
        assert read in facts
@pytest.mark.parametrize('variant', ['old_snapshot', 'own_list', 'due', 'incident_snapshot'])
def test_S1_two_sweeps_in_a_row_never_close_a_reconciling_session(t4, variant):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    snapshot = gs.db_git.get_session(mid)          # read BEFORE inst_A's pass
    env.hooks.append(on('push', push_timeout))      # every re-push times out again
    t.clear()
    gs.merge_session_sweep()                         # inst_A: reconcile → repush timeout
    after_a = saved(mid)
    assert after_a['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after_a['reconciliation_kind'] == 'push_remote_unknown'
    assert after_a['last_error']['stage'] == 'reconcile_repush' and after_a['last_error']['instance_id'] == 'inst_A'
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_A'
    assert t.of('discard') == [] and t.of('close') == [] and t.of('close_session_attempt') == []
    assert t.of('invalidate_checkpoint') == [] and merge_aborts(env) == []
    assert_writes_guarded(t, mid)
    if variant == 'own_list':
        after_a['reconcile_next_at'] = FUTURE      # backing off: B's reconcile is not due
        gs.db_git.set_session_context(mid, after_a)
    elif variant == 'due':
        after_a['reconcile_next_at'] = PAST        # pulled due: B reconciles again
        gs.db_git.set_session_context(mid, after_a)
    env.clock['instance'] = 'inst_B'
    t.clear()
    if variant == 'old_snapshot':
        gs.merge_session_sweep(sessions=[snapshot])
    elif variant == 'incident_snapshot':
        stale = dict(snapshot)
        stale_ctx = dict(gs.db_git.session_context(snapshot), review_state=gs.REVIEW_STATE_PENDING)
        stale['context'] = stale_ctx
        gs.merge_session_sweep(sessions=[stale])
    else:
        gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)
    assert gs.db_git.get_state(env.group)['status'] != 'waiting'
    assert after['merge_commit'] == m and preserve_refs(r.work)[f'refs/flowgate/preserve/{mid}'] == m
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B' and claim_row(mid)['claim_token'] is None
    if variant == 'own_list':
        assert env.commands == [] and t.of('write') == []
    else:
        assert len(pushes(env)) == 1 and after['last_error']['instance_id'] == 'inst_B'
        assert after['reconcile_attempt_count'] == before.get('reconcile_attempt_count', 0) + 2
    assert_lease_only(env)
    assert_writes_guarded(t, mid)
    assert_consistent(mid, r.work)
def test_S1u_user_intervention_then_another_instance_sweeps_without_git(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    env.hooks.append(on('ls-remote', lambda a: subprocess.CompletedProcess(
        ['git', *a], 128, '', 'fatal: Authentication failed')))
    gs.merge_session_sweep()                         # inst_A ends at the auth intervention
    env.hooks.clear()
    parked = saved(mid)
    assert parked['reconciliation_kind'] == 'reconcile_auth_required'
    env.clock['instance'] = 'inst_B'
    t.clear()
    gs.merge_session_sweep()
    assert env.commands == []
    assert saved(mid) == parked
    assert_untouched_by_guard(t, mid, parked)
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B'
def test_S2_incident_shape_is_held_once_and_left_alone_afterwards(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, ref = legacy_incident_session(t)
    t.clear()
    gs.merge_session_sweep()                         # inst_A
    held = saved(mid)
    hold = held['recovery_hold']
    assert hold == {
        'code': 'orphan_evidence_hold', 'trigger': 'sweep_orphan',
        'evidence': ['preserve_ref', 'preserve_ref_on_disk', 'reconcile_history'],
        'observed': {'merge_head': False, 'base_root_exists': True, 'head': r.B, 'worktree_clean': True},
        'review_state_at_hold': gs.REVIEW_STATE_PENDING, 'at': AT, 'instance_id': 'inst_A',
        'resume_missing': ['merge_commit_and_expected_remote_head', 'preserve_verified', 'head_is_publish_commit'],
    }
    assert held['review_state'] == gs.REVIEW_STATE_PENDING and held['merge_commit'] is None
    assert_untouched_by_guard(t, mid, held)
    assert [k for k, _p in t.emits] == ['git_merge_recovery_hold']
    assert len(t.of('write', mid)) == 1
    assert_writes_guarded(t, mid)
    locks_taken = [e for e in t.events if e[0] == 'lock']
    assert [(e[2], e[3]) for e in locks_taken] == [('sweep', locks.NO_WAIT)]
    order = [e[0] for e in t.events if e[0] in ('lock', 'write', 'unlock')]
    assert order == ['lock', 'write', 'unlock']
    for cmd in env.commands:                        # the guard's Git is read-only
        assert cmd[0] in ('rev-parse', 'status', 'for-each-ref', 'cat-file', 'rev-list'), cmd
    assert claim_row(mid)['last_outcome'] == 'hold'
    env.clock.update(now=LATER, instance='inst_B')
    t.clear()
    gs.merge_session_sweep()
    gs.merge_session_sweep()
    assert saved(mid) == held                        # not one character changed
    assert t.of('write', mid) == [] and env.commands == []
    assert ref_value(r.work, ref) == m and git(['rev-parse', 'HEAD'], r.work) == r.B
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B' and claim_row(mid)['last_outcome'] == 'hold_kept'
def test_S3_lost_state_write_resumes_into_the_t3_reconcile_and_completes(t4, common):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    ctx = saved(mid)
    ctx['review_state'] = None                       # the state write was lost
    ctx.pop('reconciliation_kind', None)
    ctx.pop('reconcile_next_at', None)
    gs.db_git.set_session_context(mid, ctx)
    to_conflict(t, mid)
    _only(env, mid)
    assert ref_value(r.work, 'MERGE_HEAD') is None and git(['rev-parse', 'HEAD'], r.work) == m
    assert origin_main(env) == r.B == ctx['expected_remote_head']
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and origin_main(env) == m
    assert after['recovery_resumed'] == {
        'from_review_state': None,
        'evidence': ['merge_commit', 'preserve_ref', 'preserve_ref_on_disk'],
        'at': AT, 'instance_id': 'inst_A'}
    assert after['last_error']['code'] == 'orphan_resumed'
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{r.B}', 'origin', f'{m}:refs/heads/main']]
    assert_lease_only(env)
    assert common['approvals'] == [(env.group, mid, INTENT)]   # consumed by the common completer
    assert t.of('discard', mid) == [] and t.of('close', mid) == [] and merge_aborts(env) == []
    assert 'recovery_hold' not in after
    # the resume write and the reconcile both ran inside a lock and the (re-entered) claim
    assert_writes_guarded(t, mid, kinds=('write', 'close', 'discard'))
    lock_kinds = [e[2] for e in t.events if e[0] == 'lock']
    assert lock_kinds == ['sweep', 'review_action']
    resume_write = next(i for i, e in enumerate(t.events) if e[0] == 'write')
    first_unlock = next(i for i, e in enumerate(t.events) if e[0] == 'unlock')
    assert resume_write < first_unlock
    assert claim_row(mid)['last_outcome'] == 'reconcile:merged'
    assert len(t.releases) == 1                      # one claim (sweep), re-entered by reconcile
    assert_consistent(mid, r.work, r.bare)
def test_S4_orphan_without_evidence_is_closed_as_before_inside_lock_and_claim(t4):
    t, env, r = t4, t4.env, t4.r
    mid = plain_orphan_session(t)
    t.clear()
    gs.merge_session_sweep()
    row = gs.db_git.get_session(mid)
    ctx = saved(mid)
    assert row['status'] == 'aborted'
    assert approval_intent.intent_of_context(ctx) is None
    assert ctx[approval_intent.DISCARDED_KEY]['reason'] == 'orphan_recovered'
    assert gs.db_git.get_state(env.group)['status'] == 'waiting'
    assert [k for k, _p in t.emits] == ['git_merge_auto_aborted']
    assert t.emits[0][1]['reason'] == 'orphan_recovered'
    assert len(t.of('close', mid)) == 1 and len(t.of('discard', mid)) == 1
    assert len(t.of('invalidate_checkpoint', mid)) == 1 and len(t.of('close_session_attempt', mid)) == 1
    assert_writes_guarded(t, mid, kinds=('close', 'discard', 'close_session_attempt', 'invalidate_checkpoint'))
    assert claim_row(mid)['last_outcome'] == 'closed_orphan'
    assert merge_aborts(env) == []
