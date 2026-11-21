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

def test_X2_reset_reporting_success_with_the_wrong_head_is_reset_failed(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    env.hooks.append(on('reset', lambda a: subprocess.CompletedProcess(['git', *a], 0, '', '')))
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_reset_failed', 'reconcile_reset_failed')
    after = assert_consistent(mid, r.work)
    assert after['last_error']['returncode'] == 0 and after['last_error']['observed_head'] == m
    assert after['last_error']['detail'].startswith('post_condition:head')
    assert after['merge_commit'] == m and after.get('preserve_history') in (None, [])
def test_X3_foreign_local_head_is_a_mismatch_without_reset(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    side = diverge_local(env)
    env.commands.clear()
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_local_state_mismatch', 'reconcile_local_state_mismatch')
    after = assert_consistent(mid, r.work)
    assert after['last_error']['observed_head'] == side and after['last_error']['expected_head'] == m
    assert count(env, 'reset') == 0 and count(env, 'ls-remote') == 0 and pushes(env) == []
def test_P_integration_ref_write_failure_is_preserve_failed(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    gitdir = Path(git(['rev-parse', '--absolute-git-dir'], r.work))
    lock = gitdir / 'refs' / 'flowgate' / 'preserve' / f'{mid}-i1.lock'
    lock.write_text('', encoding='utf-8')
    out = reconcile(mid)
    assert out['result']['reconciliation_kind'] == 'preserve_failed'
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert (e['stage'], e['reason']) == ('integration', 'ref_write_failed')
    assert e['preserve_ref'] == f'refs/flowgate/preserve/{mid}-i1'
    assert e['prior_push_error'] == before['last_error']
    assert git(['rev-parse', 'HEAD'], r.work) == m and pushes(env) == []
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}-i1') is None
def test_incident_regression_push_timeout_then_repush_completes_and_survives_recovery(env, common):
    r = env.repo
    states = []
    real_set = gs.db_git.set_session_context

    def watch(mid_, context):
        states.append(context.get('review_state'))
        return real_set(mid_, context)
    env.monkeypatch.setattr(gs.db_git, 'set_session_context', watch)
    mid, m, before = unknown_session(env)  # real Git, push result lost → push_remote_unknown
    assert origin_main(env) == r.B
    out = reconcile(mid)
    assert out['result']['status'] == 'merged' and origin_main(env) == m
    assert common['approvals'] == [(env.group, mid, INTENT)]
    assert gs.REVIEW_STATE_PENDING not in states
    after = assert_consistent(mid, r.work, r.bare)
    gs.db_git.close_session(mid, 'done')  # what the (recorded) attempt close does
    _only(env, mid)
    env.commands.clear()
    gs.merge_session_sweep()
    gs.startup_recovery()
    assert gs.db_git.get_session(mid)['status'] == 'done'
    assert saved(mid)['review_state'] == gs.REVIEW_STATE_COMPLETED
    assert saved(mid)['published_commit'] == after['published_commit'] == m
    assert pushes(env) == [] and count(env, 'reset') == 0
    assert gs.REVIEW_STATE_PENDING not in states
@pytest.mark.parametrize('case', ['auth', 'exhausted', 'reset_failed', 'local_mismatch'])
def test_user_intervention_survives_sweep_and_startup_untouched(env, case):
    r = env.repo
    mid, kind = _park(env, case)
    parked = saved(mid)
    assert parked['reconciliation_kind'] == kind
    head = git(['rev-parse', 'HEAD'], r.work)
    refs = preserve_refs(r.work)
    _only(env, mid)
    env.commands.clear()
    gs.merge_session_sweep()
    gs.startup_recovery()
    assert env.commands == []
    after = saved(mid)
    assert after == parked
    assert gs.db_git.get_session(mid)['status'] == 'open'
    assert after['review_state'] == 'reconciling' and SHA_RE.match(after['merge_commit'])
    assert approval_intent.intent_of_context(after) == INTENT
    assert preserve_refs(r.work) == refs and git(['rev-parse', 'HEAD'], r.work) == head
    assert_consistent(mid, r.work)
def test_static_reconcile_sources_have_no_forbidden_operations():
    texts = {name: inspect.getsource(getattr(gs, name)) for name in RECONCILE_SOURCES}
    texts['reconcile_plan'] = inspect.getsource(rp)
    for name, text in texts.items():
        assert not re.search(r'=\s*REVIEW_STATE_PENDING\b', text), name
        assert 'resolved_pending_review' not in text, name
        assert 'ORIG_HEAD' not in text, name
        assert not re.search(r'--force(?!-with-lease)', text), name
        assert not re.search(r'''["']-f["']''', text), name
        assert not re.search(r'''f?["']\+[\w{]''', text), name
    push_lines = [line for line in texts['_reconcile_repush'].splitlines() if 'force-with-lease' in line]
    assert push_lines and all('refs/heads/{run.branch}:{lease}' in line for line in push_lines)
