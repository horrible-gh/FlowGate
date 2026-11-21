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

def test_ref_outlives_reconcile_completion(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(m)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'merged'
    gs.db_git.close_session(mid, 'done')
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
def test_release_is_explicit_and_guarded(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    ref = f'refs/flowgate/preserve/{mid}'
    publish(env, mid)
    late = '2026-12-09T10:00:00+09:00'
    # still open
    assert preserve.release_preserved_ref(r.work, ref, m, now=late) == {
        'released': False, 'reason': 'session_not_completed'}
    gs.db_git.close_session(mid, 'done')
    assert preserve.release_preserved_ref(r.work, ref, m, now='2026-12-01T10:00:00+09:00')['reason'] == \
        'retention_not_elapsed'
    git(['update-ref', 'refs/remotes/origin/main', r.B], r.work)
    assert preserve.release_preserved_ref(r.work, ref, m, now=late)['reason'] == 'remote_missing_commit'
    git(['update-ref', 'refs/remotes/origin/main', m], r.work)
    assert preserve.release_preserved_ref(r.work, ref, r.B, now=late)['reason'] == 'ref_conflict'
    assert ref_value(r.work, ref) == m
    assert preserve.release_preserved_ref(r.work, ref, m, now=late) == {'released': True, 'reason': 'released'}
    assert ref_value(r.work, ref) is None
@pytest.mark.parametrize('outcome', ['released', 'delete_failed'])
def test_release_delete_is_audited_on_the_session(env, outcome):
    """T#1 §5.3 / §3.1-4: the explicit delete is a session ref move. It runs through the
    session audit runner with the CAS kept, and the re-read sqlite row records it —
    success or a real nonzero failure — without disturbing the earlier push evidence.
    The caller's live context carries the same entry, so its later write keeps it."""
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(m)
    assert gs.reconcile_push_session(mid, trigger='server_startup')['result']['status'] == 'merged'
    gs.db_git.close_session(mid, 'done')
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', 'refs/remotes/origin/main', m], r.work)
    push_error = saved(mid)['last_error']
    assert push_error == before['last_error'] and push_error['code'] == 'push_remote_unknown'
    if outcome == 'delete_failed':
        gitdir = Path(git(['rev-parse', '--absolute-git-dir'], r.work))
        (gitdir / 'refs' / 'flowgate' / 'preserve' / f'{mid}.lock').write_text('', encoding='utf-8')
    late = '2026-12-09T10:00:00+09:00'
    env.clock.update(now=late, instance='inst_C')
    env.commands.clear()
    ctx = saved(mid)
    out = preserve.release_preserved_ref(r.work, ref, m, now=late, context=ctx)
    assert [c for c in env.commands if c[:2] == ['update-ref', '-d']] == [['update-ref', '-d', ref, m]]
    if outcome == 'released':
        assert out == {'released': True, 'reason': 'released'}
        assert ref_value(r.work, ref) is None
        rc = 0
    else:
        assert out == {'released': False, 'reason': 'ref_delete_failed'}
        assert ref_value(r.work, ref) == m
        rc = git_rc(['update-ref', '-d', ref, m], r.work).returncode  # same real failure
        assert rc != 0
    after = saved(mid)
    entry = after['ref_operations'][-1]
    assert entry == {'operation': 'update-ref', 'reason': 'release_preserve_ref', 'at': late,
                     'instance_id': 'inst_C', 'returncode': rc, 'status': 'completed'}
    assert after['last_error'] == push_error
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED
    assert ctx['ref_operations'][-1] == entry
    ctx['caller_note'] = 'written later by the caller'
    gs.db_git.set_session_context(mid, ctx)
    assert saved(mid)['ref_operations'][-1] == entry
def test_no_automatic_path_deletes_preserve_refs():
    """§5.2/§5.4: only preserve.release_preserved_ref deletes, and nothing calls it. No
    other product module deletes under the prefix or sweeps the whole refs/flowgate/."""
    server = Path(gs.__file__).resolve().parents[3]
    offenders = []
    for top in ('modules', 'flowgate'):
        for path in (server / top).rglob('*.py'):
            if path.name == 'preserve.py':
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if 'release_preserved_ref' in text or 'PRESERVE_REF_PREFIX' in text:
                offenders.append(str(path))
            for quote in ('"', "'"):
                if f'refs/flowgate/{quote}' in text or f'refs/flowgate/preserve/{quote}' in text:
                    offenders.append(str(path))
    assert offenders == []
def test_preserve_ref_alone_keeps_commit_and_parents_through_external_gc(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    # 0153 T#3: reconcile no longer rewinds the branch; the same rewind is made directly.
    git(['reset', '-q', '--hard', r.B], r.work)
    assert git(['rev-parse', 'HEAD'], r.work) == r.B
    git(['update-ref', '-d', 'ORIG_HEAD'], r.work)
    git(['branch', '-D', 'feature'], r.work)
    _expire_and_gc(r.work)
    for sha in (m, r.B, r.F):
        assert _exists(r.work, sha)
    # Control: without the preservation ref the same history is collected.
    git(['update-ref', '-d', f'refs/flowgate/preserve/{mid}', m], r.work)
    _expire_and_gc(r.work)
    assert not _exists(r.work, m)
    assert not _exists(r.work, r.F)
