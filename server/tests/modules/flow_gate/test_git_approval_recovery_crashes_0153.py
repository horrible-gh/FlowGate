"""Durable abort admission across released leases and lost database acknowledgements."""
import copy
import subprocess
import uuid

import pytest

from tests.modules.flow_gate.test_git_approval_recovery_0153 import (
    real, env, project, repo, patch_store, tmp_db, ready, request, saved, approve,
    fail_push, names, git, ref_value, pending, terminal,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, cleanup, session_claim


@pytest.mark.parametrize('failure', ['reset', 'db_after_git'])
def test_B3_B4_incomplete_abort_survives_released_claim_sweep_and_startup(real, monkeypatch, failure):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    view = recovery.get(real.group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=attempt)
    if failure == 'reset':
        hook = lambda args: subprocess.CompletedProcess(args, 1, '', 'reset refused') if args[0] == 'reset' else None
        real.hooks.append(hook)
    else:
        write = gs.db_git.set_session_context
        failed = []
        def lose(mid, ctx):
            if not failed and (ctx.get('abort_progress') or {}).get('phase') == 'git_cleaned':
                failed.append(True)
                raise RuntimeError('lost database acknowledgement')
            return write(mid, ctx)
        monkeypatch.setattr(gs.db_git, 'set_session_context', lose)
    with pytest.raises(gs.GitServiceError):
        request(real, mid, 'abort', body)
    # The failed request's claim/lock is gone. This is the dangerous next sweep,
    # rather than a competing call that is merely blocked by a still-live lock.
    before = copy.deepcopy(saved(mid))
    assert before['abort_progress']['phase'] == 'prepared'
    real.commands.clear()
    assert gs.reconcile_push_session(mid, trigger='server_startup') is None
    gs.merge_session_sweep(sessions=[gs.db_git.get_session(mid)])
    claim = session_claim.acquire(mid, session_claim.PURPOSE_STARTUP)
    assert claim is not None
    try:
        cleanup._startup_group_session(gs.db_git.get_session(mid), gs._session_project(gs.db_git.get_session(mid)))
    finally:
        session_claim.release(claim)
    assert saved(mid) == before and not real.commands
    pending(real, mid)
    view = recovery.get(real.group, mid)['result']
    assert view['allowed_actions'] == ['refresh', 'abort']
    assert view['next_action'] == 'resume_abort'
    assert {k: view['pending_request'][k] for k in body} == body
    if failure == 'reset':
        real.hooks.remove(hook)
    real.commands.clear()
    request(real, mid, 'abort', body)
    assert gs.db_git.get_session(mid)['status'] == 'aborted'
    assert gs.db_git.get_state(real.group)['status'] == 'waiting'
    assert names(real).count('reset') == int(failure == 'reset')
    assert 'push' not in names(real) and 'commit' not in names(real)


@pytest.mark.parametrize('phase', ['initial', 'prepared'])
def test_B2_staged_and_working_versions_cannot_be_silently_discarded(real, phase):
    mid, fp = ready(real)
    body = None
    if phase == 'prepared':
        hook = lambda args: subprocess.CompletedProcess(args, 1, '', 'abort interrupted') if args[:2] == ['merge', '--abort'] else None
        real.hooks.append(hook)
        view = recovery.get(real.group, mid)['result']
        body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=None)
        with pytest.raises(gs.GitServiceError) as exc:
            request(real, mid, 'abort', body)
        assert exc.value.code == 'recovery_abort_failed'
        assert saved(mid)['abort_progress']['phase'] == 'prepared'
        real.hooks.remove(hook)
        # Keep the saved worktree bytes, but stage a new distinct version after
        # the durable snapshot. A replay must recheck index preservation too.
        working = (real.repo.work / 'f.txt').read_text(encoding='utf-8')
        (real.repo.work / 'f.txt').write_text('new staged version\n', encoding='utf-8')
        git(['add', 'f.txt'], real.repo.work)
        (real.repo.work / 'f.txt').write_text(working, encoding='utf-8')
    else:
        (real.repo.work / 'f.txt').write_text('unsaved working version\n', encoding='utf-8')
    working = (real.repo.work / 'f.txt').read_text(encoding='utf-8')
    staged = git(['show', ':f.txt'], real.repo.work)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'abort', body)
    assert exc.value.code == 'recovery_unpreserved_index'
    assert git(['show', ':f.txt'], real.repo.work) == staged
    assert (real.repo.work / 'f.txt').read_text() == working
    assert ref_value(real.repo.work, 'MERGE_HEAD') == real.repo.F
    assert not any(c in names(real) for c in ('reset', 'merge', 'commit-tree', 'update-ref'))
    pending(real, mid)


def test_B3_a_new_merge_head_is_not_owned_by_an_already_committed_attempt(real):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    git(['checkout', '-b', 'other-attempt', real.repo.B], real.repo.work)
    (real.repo.work / 'other.txt').write_text('other attempt\n', encoding='utf-8')
    git(['add', 'other.txt'], real.repo.work)
    git(['commit', '-m', 'other attempt'], real.repo.work)
    foreign = ref_value(real.repo.work, 'HEAD')
    git(['checkout', 'main'], real.repo.work)
    git(['merge', '--no-ff', '--no-commit', foreign], real.repo.work)
    tree = git(['write-tree'], real.repo.work)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'abort')
    assert exc.value.code == 'recovery_local_mismatch'
    assert ref_value(real.repo.work, 'MERGE_HEAD') == foreign
    assert git(['write-tree'], real.repo.work) == tree
    assert not any(c in names(real) for c in ('reset', 'merge', 'commit-tree', 'update-ref'))
    pending(real, mid)
