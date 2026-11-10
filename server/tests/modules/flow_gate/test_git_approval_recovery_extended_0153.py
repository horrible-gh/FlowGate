"""Recovery races, response loss and new remote histories, using real Git/SQLite."""
import copy
import json
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest

from tests.modules.flow_gate.test_git_approval_recovery_0153 import (
    real, env, project, repo, patch_store, tmp_db, ready, request, saved, approve,
    fail_push, names, git, git_rc, ref_value, parents_of, pending, terminal, fa,
)
from tests.modules.flow_gate.test_git_reconcile_0153 import remote_commit
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, branch_merge_publish as bmp
from modules.flow_gate.services.git import session_claim
from modules.flow_gate.db import merge_request_binding as bindings


def test_A2_concurrent_approval_is_serialized_before_git(real):
    mid, fp = ready(real)
    attempt = str(uuid.uuid4())
    entered, resume = threading.Event(), threading.Event()
    def barrier(args):
        if 'commit' in args:
            entered.set()
            assert resume.wait(30), 'approval barrier timeout'
    real.hooks.append(barrier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(approve, real, mid, attempt, fp)
        try:
            assert entered.wait(30)
            before = copy.deepcopy(saved(mid))
            commands = list(real.commands)
            real.clock['instance'] = 'inst_B'
            for competing_id in (attempt, str(uuid.uuid4())):
                with pytest.raises(gs.GitServiceError) as exc:
                    approve(real, mid, competing_id, fp)
                assert exc.value.code == 'git_busy'
            assert real.commands == commands and saved(mid) == before
        finally:
            real.clock['instance'] = 'inst_A'
            resume.set()
        assert first.result(timeout=60)['result']['approval']['approved']
    assert names(real).count('commit') == 1 and names(real).count('push') == 1
    terminal(real, mid)
    real.commands.clear()
    assert approve(real, mid, attempt, fp)['result']['approval']['approved']
    assert not real.commands


def test_A3_approval_commit_response_loss_cannot_resurrect_intent(real, monkeypatch):
    mid, fp = ready(real)
    original = approval_intent.commit_deferred_approval
    consumed = []
    def lose(*args, **kwargs):
        result = original(*args, **kwargs)
        consumed.append(result)
        raise RuntimeError('lost approval response after transaction commit')
    monkeypatch.setattr(approval_intent, 'commit_deferred_approval', lose)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    assert len(consumed) == 1 and consumed[0]['approved']
    assert approval_intent.intent_of_context(saved(mid)) is None
    real.commands.clear()
    out, _ = request(real, mid)
    assert out['result']['approval']['approved']
    assert len(consumed) == 1 and not real.commands
    terminal(real, mid)


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


def test_P1_uuid_is_bound_across_sessions_and_payloads(real):
    mid, fp = ready(real)
    key = str(uuid.uuid4())
    a = {'group_id': real.group, 'merge_id': mid, 'review_fingerprint': fp}
    b = {**a, 'merge_id': mid + 1}
    assert bindings.bind('approval', key, a)
    assert bindings.bind('approval', key, a)
    assert not bindings.bind('approval', key, b)
    assert bindings.matches('approval', key, b) is False
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        approve(real, mid, key, 'other candidate')
    assert exc.value.code == 'approval_attempt_reused' and not real.commands


def test_R1_explicit_retry_grants_one_bounded_budget_and_keeps_total_history(real):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    before = saved(mid)
    _, body = request(real, mid)
    for _ in range(4):
        gs.reconcile_push_session(mid, trigger='server_startup')
    exhausted = saved(mid)
    assert exhausted['reconcile_cycle_count'] == 5
    assert exhausted['reconciliation_kind'] == gs.RECONCILE_KIND_RETRY_EXHAUSTED
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, body=body)
    assert exc.value.code == gs.RECONCILE_KIND_RETRY_EXHAUSTED
    assert not real.commands and saved(mid)['reconcile_cycle_count'] == 5
    request(real, mid)
    after = saved(mid)
    assert after['reconcile_cycle_count'] == 1
    assert after['reconcile_attempt_count'] == exhausted['reconcile_attempt_count'] + 1
    assert len(after['recovery_retry_history']) == 2
    assert after['approval_attempt_id'] == attempt and after['merge_commit'] == before['merge_commit']
    pending(real, mid)


def test_binding_migration_is_idempotent_and_identical_portable_sql():
    import sqlite3
    from pathlib import Path
    root = Path(__file__).resolve().parents[3] / 'sql' / 'migrations'
    scripts = [(root / engine / '182_git_merge_request_binding.sql').read_text(encoding='utf-8')
               for engine in ('sqlite', 'mysql', 'postgres')]
    assert len(set(scripts)) == 1
    with sqlite3.connect(':memory:') as conn:
        for script in scripts:
            conn.executescript(script)
        conn.execute('INSERT INTO git_merge_request_binding VALUES (?, ?, ?, ?, ?, ?)',
                     ['approval', 'id', 'digest', 1, 'group', 'now'])
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO git_merge_request_binding VALUES (?, ?, ?, ?, ?, ?)',
                         ['approval', 'id', 'changed', 2, 'other', 'now'])
