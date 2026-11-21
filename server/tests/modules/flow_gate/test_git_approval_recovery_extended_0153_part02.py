"""Independently selectable scenarios; shared fixtures remain in test_git_approval_recovery_extended_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_approval_recovery_extended_0153 import (
    ThreadPoolExecutor,
    approval_intent,
    approve,
    bindings,
    bmp,
    copy,
    env,
    fa,
    fail_push,
    git,
    git_rc,
    gs,
    json,
    names,
    parents_of,
    patch_store,
    pending,
    project,
    pytest,
    ready,
    real,
    recovery,
    ref_value,
    remote_commit,
    repo,
    request,
    saved,
    session_claim,
    subprocess,
    terminal,
    threading,
    tmp_db,
    uuid,
)

@pytest.mark.parametrize('conflict', [False, True])
def test_C1_remote_advance_integrates_or_hands_back_only_new_conflict(real, conflict):
    mid, fp = ready(real)
    hook = fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    before = saved(mid)
    sha = before['merge_commit']
    real.hooks.remove(hook)
    other = remote_commit(real, real.repo.tmp, 'f.txt' if conflict else 'other.txt', 'other group\n')
    real.commands.clear()
    out, _ = request(real, mid)
    assert ref_value(real.repo.work, before['preserve_ref']) == sha
    assert saved(mid)['approval_attempts'][attempt]['review_fingerprint'] == fp
    assert saved(mid)['first_push_error'] == before['first_push_error']
    if conflict:
        assert out['result']['allowed_actions'] == ['refresh', 'resolve', 'abort']
        assert out['result']['requires_recovery'] is False
        assert ref_value(real.repo.work, 'HEAD') == other
        assert ref_value(real.repo.work, 'MERGE_HEAD') == sha
        assert git(['diff', '--name-only', '--diff-filter=U'], real.repo.work) == 'f.txt'
        pending(real, mid)
        real.commands.clear()
        old = approve(real, mid, attempt, fp)['result']
        assert old['status'] == 'superseded' and not real.commands
        with pytest.raises(gs.GitServiceError) as exc:
            gs.abort_merge(real.group, mid)
        assert exc.value.code == 'recovery_required' and not real.commands
        # New unmerged working bytes are also preserved before explicitly aborting.
        (real.repo.work / 'f.txt').write_text('new work after remote conflict\n', encoding='utf-8')
        request(real, mid, 'abort')
        snapshot = saved(mid)['abort_snapshot']
        assert git(['show', snapshot['sha'] + ':f.txt'], real.repo.work) == 'new work after remote conflict'
        assert parents_of(real.repo.work, snapshot['sha']) == [other, sha]
        assert ref_value(real.repo.bare, 'main') == other
    else:
        terminal(real, mid)
        published = ref_value(real.repo.bare, 'main')
        assert git_rc(['merge-base', '--is-ancestor', sha, published], real.repo.work).returncode == 0
        assert git_rc(['merge-base', '--is-ancestor', other, published], real.repo.work).returncode == 0
        assert ref_value(real.repo.work, 'feature') == real.repo.F
def test_R2_preserve_command_failure_is_retryable_after_cause_is_fixed(real):
    mid, fp = ready(real)
    def no_ref(args):
        if args[0] == 'update-ref' and any('refs/flowgate/preserve/' in a for a in args):
            return subprocess.CompletedProcess(args, 1, '', 'ref storage unavailable')
    real.hooks.append(no_ref)
    approve(real, mid, str(uuid.uuid4()), fp)
    assert saved(mid)['reconciliation_kind'] == gs.RECONCILE_KIND_PRESERVE_FAILED
    sha = ref_value(real.repo.work, 'HEAD')
    real.hooks.remove(no_ref)
    request(real, mid)
    terminal(real, mid)
    assert saved(mid)['merge_commit'] == sha
    assert names(real).count('commit') == 1
def test_B4_abort_wins_against_retry_approve_sweep_and_startup(real, monkeypatch):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    view = recovery.get(real.group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=attempt)
    entered, resume = threading.Event(), threading.Event()
    actual = bmp.session_lock
    owner = {'thread': None}
    def barrier(*args, **kwargs):
        result = actual(*args, **kwargs)
        if threading.get_ident() == owner['thread']:
            entered.set()
            assert resume.wait(30)
        return result
    monkeypatch.setattr(bmp, 'session_lock', barrier)
    def abort():
        owner['thread'] = threading.get_ident()
        return request(real, mid, 'abort', body)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(abort)
        try:
            assert entered.wait(30)
            before = copy.deepcopy(saved(mid))
            commands = list(real.commands)
            real.clock['instance'] = 'inst_B'
            with pytest.raises(gs.GitServiceError) as exc:
                request(real, mid, body={**body, 'request_id': str(uuid.uuid4())})
            assert exc.value.code == 'recovery_busy'
            with pytest.raises(gs.GitServiceError) as exc:
                approve(real, mid, attempt, fp)
            assert exc.value.code == 'git_busy'
            gs.merge_session_sweep(sessions=[gs.db_git.get_session(mid)])
            gs.reconcile_push_session(mid, trigger='server_startup')
            assert saved(mid) == before and real.commands == commands
        finally:
            real.clock['instance'] = 'inst_A'
            resume.set()
        assert first.result(timeout=60)[0]['result']['session_state'] == 'aborted'
    assert ref_value(real.repo.bare, 'main') == real.repo.B
    assert approval_intent.intent_of_context(saved(mid)) is None
    assert saved(mid)['abort_progress']['phase'] == 'completed'
