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

def test_B2_remote_contains_the_merge_commit_plus_more(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    git(['push', '-q', 'origin', f'{m}:refs/heads/main'], r.work)
    tip = remote_commit(env, r.tmp, 'later.txt', 'later\n')
    env.commands.clear()
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    assert pushes(env) == [] and count(env, 'fetch') == 1
    assert git(['rev-parse', 'HEAD'], r.work) == m  # not moved to the remote tip
    assert origin_main(env) == tip
    assert_consistent(mid, r.work, r.bare)
def test_C1_remote_ahead_is_integrated_preserved_and_pushed(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    after = assert_consistent(mid, r.work, r.bare)
    i = after['publish_commit']
    assert i not in (m, c) and parents_of(r.work, i) == [m, c]
    assert git(['show', '-s', '--format=%an <%ae>|%cn <%ce>', i], r.work) == f'{IDENTITY}|{IDENTITY}'
    iref = f'refs/flowgate/preserve/{mid}-i1'
    assert ref_value(r.work, iref) == i and ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert origin_main(env) == i and git(['rev-parse', 'HEAD'], r.work) == i
    assert after['published_commit'] == i and after['publish_lease'] == c
    assert after['merge_commit'] == m and after['expected_remote_head'] == r.B
    assert after['integration_commits'] == [{
        'sha': i, 'ref': iref, 'parents': [m, c], 'onto': c, 'at': AT, 'instance_id': 'inst_A'}]
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{c}', 'origin',
                            f'{i}:refs/heads/main']]
    assert_lease_only(env)
    assert count(env, 'reset') == 0 and count(env, 'fetch') == 1
    # §5.2 audit: every move is on the session with the §3.1 reason; reflogs are FlowGate's.
    assert [x for x in reasons(mid) if x.startswith(('reconcile', 'preserve_integration'))] == [
        'reconcile_fetch', 'preserve_integration_commit', 'reconcile_integrate', 'reconcile_repush']
    assert reflog(r.work, iref) == [f'{IDENTITY}|flowgate: preserve integration {mid}-i1']
    assert git(['reflog', 'show', '-1', '--format=%gn <%ge>', 'HEAD'], r.work) == IDENTITY
def test_C2_repush_timeout_after_integration_then_the_next_cycle_repushes_it(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    env.hooks.append(on('push', push_timeout))
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'retry_scheduled'}}
    first = assert_consistent(mid, r.work)
    i = first['publish_commit']
    assert first['apply_phase'] == 'integrated_local' and first['last_error']['local_sha'] == i
    assert first['last_error']['expected_remote_head'] == c and origin_main(env) == c
    env.hooks.clear()
    env.commands.clear()
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    assert count(env, 'commit-tree') == 0 and count(env, 'fetch') == 0
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{c}', 'origin',
                            f'{i}:refs/heads/main']]
    after = assert_consistent(mid, r.work, r.bare)
    assert len(after['integration_commits']) == 1 and origin_main(env) == i
def test_C4_clean_integration_that_fails_verification_goes_back_to_review(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'data.json', '{"broken": \n')
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    assert out['result']['error'] == 'remote_integration_unverified'
    after = assert_consistent(mid, r.work)
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert after['last_error'] == {'code': 'remote_integration_unverified'}
    assert after['integration_verification'][0]['check'] == 'syntax'
    assert [h['sha'] for h in after['preserve_history']] == [m]
    assert after['preserve_history'][-1]['closed_reason'] == 'reconcile_remote_unverified'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert origin_main(env) == c and pushes(env) == []
    assert after['approval_attempt_id'] is None and after['reconcile_cycle_count'] == 0
    assert 'reconciliation_kind' not in after and 'reconcile_next_at' not in after
    assert approval_intent.intent_of_context(after) == INTENT
    assert preserve_refs(r.work) == {f'refs/flowgate/preserve/{mid}': m}  # no integration pinned
    assert [x for x in reasons(mid) if x.startswith('reconcile')] == [
        'reconcile_fetch', 'reconcile_verified_reset', 'reconcile_handoff_merge']
    # review finding (P1): the clean hand-off re-bases the session on F like a conflict one
    assert after['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                          'source': 'reconcile_remote_unverified', 'clean': True}
    assert after['base_head'] == c and after['merge_head'] == m
    assert [(f['path'], f['resolved']) for f in gs.db_git.session_files(mid)] == [('data.json', 1)]
def test_C4R_reject_after_the_clean_hand_off_returns_the_same_candidate_to_the_resolver(env):
    """Review finding (P1): remote broken JSON → clean hand-off (re_review) → reject. The
    rejection restores the SAME clean candidate (HEAD = F, MERGE_HEAD = M) for the
    resolver, never restoration_verification_failed/base_head_moved, and the resolver's
    fix freezes on the same heads."""
    r = env.repo
    mid, m, before = unknown_session(env)
    original = {'base_head': r.B, 'merge_head': r.F}
    ctx = saved(mid)
    ctx['resolver_baseline'] = dict(original)  # what finalize records at session creation
    gs.db_git.set_session_context(mid, ctx)
    gs.db_git.add_session_files(mid, ['f.txt'])
    gs.db_git.mark_file_resolved(mid, 'f.txt')
    c = remote_commit(env, r.tmp, 'data.json', '{"broken": \n')
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    handed = assert_consistent(mid, r.work)
    assert handed['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                           'source': 'reconcile_remote_unverified', 'clean': True}
    assert handed['resolver_baseline_history'] == [original]
    assert [(f['path'], f['resolved']) for f in gs.db_git.session_files(mid)] == [
        ('data.json', 1), ('f.txt', 1)]
    candidate = handed['snapshot_tree']
    runs = []
    out = _reject(env, mid, reason='fix the json', runs=runs)
    assert out['result']['status'] == 'returned_to_resolver' and runs == ['fix the json']
    after = saved(mid)
    assert after['review_state'] is None and 'last_error' not in after
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert after['resolver_baseline'] == handed['resolver_baseline']
    assert git(['rev-parse', 'HEAD'], r.work) == c and ref_value(r.work, 'MERGE_HEAD') == m
    assert unmerged(r.work) == [] and git(['write-tree'], r.work) == candidate
    assert (r.work / 'data.json').read_text(encoding='utf-8') == '{"broken": \n'
    assert gs.db_git.remaining_conflicts(mid) == ['data.json', 'f.txt']
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m and origin_main(env) == c
    assert pushes(env) == []
    assert_consistent(mid, r.work)
    # the resolver's fix is an ordinary candidate on the same new heads
    (r.work / 'data.json').write_text('{"fixed": true}\n', encoding='utf-8')
    git(['add', 'data.json'], r.work)
    snap = gs._freeze_commit_candidate(r.work, 'main')
    assert (snap['base_head'], snap['merge_head']) == (c, m)
def test_C5_conflict_markers_arriving_from_the_remote_are_handed_back_not_pushed(env):
    """Review finding (P1), T0009 §3.3 (c)-2-3: a clean integration whose remote side adds
    a marker block is not integrated, pinned or pushed; origin stays F. The approval
    validator's own exemption (markers inherited from either parent) is unchanged and
    alone would have let it through — the separate check is what catches it."""
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'remote.txt', MARKER_BLOCK)
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    after = assert_consistent(mid, r.work)
    assert after['integration_verification'] == [{'check': 'conflict_markers', 'paths': ['remote.txt']}]
    assert origin_main(env) == c and pushes(env) == [] and count(env, 'commit-tree') == 0
    assert preserve_refs(r.work) == {f'refs/flowgate/preserve/{mid}': m}  # no integration pinned
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert [f['path'] for f in gs.db_git.session_files(mid)] == ['remote.txt']
    assert_lease_only(env)
    tree = git(['merge-tree', '--write-tree', m, c], r.work).splitlines()[0]
    probe = {
        'snapshot_manifest': gs._parse_ls_tree_z(gs._run_git(['ls-tree', '-r', '-z', tree], cwd=r.work).stdout),
        'changes': gs._parse_name_status_manifest(
            gs._run_git(['diff', '--name-status', '-M', '-z', m, tree, '--'], cwd=r.work).stdout),
        'base_head': m, 'merge_head': c,
    }
    assert gs._validate_review_changed_paths(r.work, probe, unregistered_extension='skip') == []
    assert gs._integration_marker_paths(r.work, m, probe) == ['remote.txt']
