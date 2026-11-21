"""Independently selectable scenarios; shared fixtures remain in test_git_reconcile_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_reconcile_0153 import (
    AT,
    BASE,
    F,
    FETCHED,
    I,
    IDENTITY,
    INTENT,
    LATER,
    LEASE,
    LEASE_ARG_RE,
    M,
    MARKER_BLOCK,
    NEXT_AT,
    P,
    PROJECT,
    PUSH_CLASSES,
    Path,
    REAL_COMPLETE,
    REAL_SCENARIOS,
    RECONCILE_SOURCES,
    SHA_RE,
    TRANSITIONS,
    X,
    _network_failure,
    _only,
    _park,
    _reject,
    approval_intent,
    approved,
    assert_consistent,
    assert_lease_only,
    common,
    conflict_session,
    count,
    diverge_local,
    env,
    git,
    git_rc,
    gs,
    inspect,
    merge_commit,
    names,
    on,
    open_session,
    origin_main,
    other_clone,
    parents_of,
    patch_store,
    preserve,
    preserve_refs,
    project,
    publish,
    push_timeout,
    pushes,
    pytest,
    pytestmark,
    re,
    reasons,
    reconcile,
    ref_value,
    reflog,
    remote_commit,
    repo,
    rp,
    saved,
    subprocess,
    tmp_db,
    unknown_session,
    unmerged,
    user_intervention,
)

def test_D1_real_conflict_returns_only_the_new_conflict_to_the_resolver(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    c = remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    out = reconcile(mid)
    assert out['result'] == {'status': 'returned_to_resolver', 'review_state': None,
                             'conflict_paths': ['y.txt'], 'error': 'remote_conflict'}
    after = assert_consistent(mid, r.work)
    assert unmerged(r.work) == ['y.txt']
    assert (r.work / 'x.txt').read_text(encoding='utf-8') == 'x resolved\n'  # resolution kept
    assert [f['path'] for f in gs.db_git.session_files(mid)] == ['y.txt']
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert after['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                          'source': 'reconcile_remote_conflict'}
    assert after['resolver_baseline_history'] == [before['resolver_baseline']]
    assert after['last_error'] == {'code': 'remote_conflict', 'paths': ['y.txt']}
    assert approval_intent.intent_of_context(after) == INTENT
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert after['preserve_history'][-1]['closed_reason'] == 'reconcile_remote_conflict'
    assert origin_main(env) == c and pushes(env) == [] and count(env, 'commit-tree') == 0
def test_D2_reject_after_the_hand_off_recreates_the_same_new_conflict(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    c = remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    reconcile(mid)
    (r.work / 'y.txt').write_text('y resolved again\n', encoding='utf-8')
    git(['add', 'y.txt'], r.work)
    ctx = saved(mid)
    ctx.update(gs._freeze_commit_candidate(r.work, 'main'))
    ctx['review_state'] = gs.REVIEW_STATE_PENDING  # the resolver submitted (test setup)
    gs.db_git.set_session_context(mid, ctx)
    env.monkeypatch.setattr(gs, '_merge_review_session',
                            lambda *a, **kw: (gs.db_git.get_session(mid), saved(mid), PROJECT, r.work, 'main'))
    env.monkeypatch.setattr(gs, 'apply_eol_separation', lambda *a, **kw: None)
    out = gs.reject_merge_review(env.group, mid, reason='again', provider_id=None,
                                 provider_pinned=False, start_run=lambda reason: 'run-d2')
    assert out['result']['status'] == 'returned_to_resolver'
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert unmerged(r.work) == ['y.txt']
    assert (r.work / 'x.txt').read_text(encoding='utf-8') == 'x resolved\n'
    after = saved(mid)
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert after['review_state'] is None
    assert_consistent(mid, r.work)
def test_D1Q_quoted_conflict_paths_are_stored_and_restored_as_the_real_path(env):
    """Review finding (P2): with core.quotePath on (Git's default) a non-ASCII conflict
    path is C-quoted in ``diff --name-only`` lines. The hand-off stores, reports and
    restores the REAL path string — compared here with the literal name, not with another
    line-form parse."""
    r = env.repo
    git(['config', 'core.quotePath', 'true'], r.work)
    kept, changed = 'naïve.txt', '한글.txt'
    mid, m, b2, before = conflict_session(env, x=kept, y=changed)
    c = remote_commit(env, r.tmp, changed, 'y remote\n')
    out = reconcile(mid)
    assert out['result'] == {'status': 'returned_to_resolver', 'review_state': None,
                             'conflict_paths': [changed], 'error': 'remote_conflict'}
    line_form = git(['diff', '--name-only', '--diff-filter=U'], r.work)
    assert line_form.startswith('"') and line_form != changed  # the quoting is really on
    assert [f['path'] for f in gs.db_git.session_files(mid)] == [changed]
    assert all((r.work / f['path']).is_file() for f in gs.db_git.session_files(mid))
    assert gs._unmerged_paths_exact(r.work) == [changed]
    after = saved(mid)
    assert after['last_error'] == {'code': 'remote_conflict', 'paths': [changed]}
    assert (r.work / kept).read_text(encoding='utf-8') == 'x resolved\n'  # resolution kept
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert_consistent(mid, r.work)
    # D2 on the same paths: the rejection compares the rows with the same real-path query
    (r.work / changed).write_text('y resolved again\n', encoding='utf-8')
    git(['add', changed], r.work)
    ctx = saved(mid)
    ctx.update(gs._freeze_commit_candidate(r.work, 'main'))
    ctx['review_state'] = gs.REVIEW_STATE_PENDING  # the resolver submitted (test setup)
    gs.db_git.set_session_context(mid, ctx)
    out = _reject(env, mid)
    assert out['result']['status'] == 'returned_to_resolver'
    after = saved(mid)
    assert after['review_state'] is None
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert unmerged(r.work) == [changed] and gs.db_git.remaining_conflicts(mid) == [changed]
    assert_consistent(mid, r.work)
def test_R_rewritten_remote_history_is_manual_without_push_or_reset(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    other = other_clone(env)
    git(['reset', '-q', '--hard', r.A], other)
    (other / 'rewrite.txt').write_text('rewritten\n', encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-q', '-m', 'rewrite'], other)
    git(['push', '-q', '--force', 'origin', 'main'], other)  # the remote's own rewrite
    rewritten = git(['rev-parse', 'HEAD'], other)
    env.commands.clear()
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'manual_reconciliation_needed'}}
    after = assert_consistent(mid, r.work)
    assert after['reconciliation_kind'] == 'push_remote_third' and after['reconcile_next_at'] is None
    assert after['last_error']['code'] == 'remote_rewritten' and after['last_error']['observed'] == rewritten
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert pushes(env) == [] and count(env, 'reset') == 0 and origin_main(env) == rewritten
def test_X1_handoff_reset_really_fails_on_index_lock(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    lock = Path(git(['rev-parse', '--absolute-git-dir'], r.work)) / 'index.lock'

    def lock_index(args):
        if args and args[0] == 'reset':
            lock.write_text('', encoding='utf-8')
        return None
    env.hooks.append(lock_index)
    try:
        out = reconcile(mid)
    finally:
        lock.unlink(missing_ok=True)
    assert out['result'] == user_intervention('reconcile_reset_failed', 'reconcile_reset_failed')
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert e['returncode'] not in (0, None) and e['observed_head'] == m and e['detail']
    assert after['merge_commit'] == m and after['apply_phase'] == 'committed_local'
    assert after['approval_attempt_id'] == 'attempt-1' and after['preserve_sha'] == m
    assert after.get('preserve_history') in (None, [])  # round not closed
    assert after['review_state'] == 'reconciling'
