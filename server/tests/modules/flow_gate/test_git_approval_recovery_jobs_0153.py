"""Actual final_approval_publish conflict handoff stays terminal during recovery."""
import copy
import json
import subprocess
import uuid
import pytest

from tests.modules.flow_gate.test_final_approval_conflict_intent_0555 import (
    project, origin_repo, patch_store, tmp_db, live_instance, PROJECT, USER, _seed_final_approval_group,
    _make_conflict_material, _final_approve, _resolve, _review_status, _intent, job_of,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_freeze
from modules.flow_gate.db.connection import get_store


@pytest.mark.parametrize('action,kind', [
    ('retry', 'timeout'), ('abort', 'timeout'), ('retry', 'auth'),
    ('retry', 'unknown'), ('retry', 'rejected'),
])
def test_J1_real_handoff_job_is_not_requeued_and_other_group_is_unchanged(origin_repo, monkeypatch, action, kind):
    group = f'{PROJECT}.default.recovery_{action}_{kind}'
    other = f'{PROJECT}.default.other_{action}_{kind}'
    root, ac = _seed_final_approval_group(group)
    other_root, other_ac = _seed_final_approval_group(other)
    other_before = [_review_status(other_root), _review_status(other_ac)]
    _make_conflict_material(group, origin_repo, 'shared.py', f'"group {action}"\n', f'"main {action}"\n')
    status, out = _final_approve(ac)
    assert status == 200 and out['git']['result']['status'] == 'conflict'
    mid = out['git']['result']['merge_id']
    all_jobs_before = copy.deepcopy(get_store()._fetch_all(
        'SELECT * FROM operation_job WHERE group_id = ? ORDER BY job_id', [group]))
    assert {row['kind'] for row in all_jobs_before} == {'worktree_provision', 'final_approval_publish'}
    assert sum(row['kind'] == 'final_approval_publish' for row in all_jobs_before) == 1
    job = copy.deepcopy(job_of(group))
    assert job['status'] == 'succeeded'
    assert job['result'] == 'handed_off_to_conflict_review'
    assert approval_freeze.freeze_claim_owner(group) is None
    from modules.flow_gate.db import operation_job as jobs
    gs.db_git.register_worktree(other, PROJECT, other.replace('.', '_'))
    other_job = {key: job[key] for key in jobs._INSERT_COLUMNS if key in job}
    from modules.flow_gate.db import git_concurrency
    other_instance = 'inst_' + uuid.uuid4().hex
    git_concurrency.insert_instance(other_instance, 'other-live-node', 4242, None, '2099-01-01T00:00:00+00:00')
    other_job.update(job_id='opj_' + uuid.uuid4().hex[:24], group_id=other, status='freezing', phase='start',
                     request_key='unrelated', request_key_hash=uuid.uuid4().hex * 2,
                     lease_owner=other_instance, lease_token='other-live-lease',
                     lease_until='2099-01-01T00:00:00+00:00')
    other_payload = json.loads(other_job['payload'])
    other_payload['doc_id'] = other_ac
    other_job['payload'] = json.dumps(other_payload)
    jobs.insert_job(other_job)
    assert jobs.set_group_freeze_claim(other, other_job['job_id'], 'freezing', gs.now_iso()) == 1
    other_job_before = copy.deepcopy(jobs.get_job(other_job['job_id']))
    other_state = copy.deepcopy(gs.db_git.get_state(other))
    intent = _intent(mid)
    resolved = _resolve(group, mid, 'shared.py', f'"group {action}"\n"main {action}"\n')
    actual = gs._run_git
    calls = []
    failures = {'timeout': (-1, 'timeout_expired'), 'auth': (128, 'fatal: Authentication failed'),
                'unknown': (128, 'opaque failure'), 'rejected': (1, ' ! [rejected] main -> main (stale info)')}
    fault = {'enabled': True}
    def failed(args, **kwargs):
        calls.append(list(args))
        if args[0] == 'push' and fault['enabled']:
            code, error = failures[kind]
            return subprocess.CompletedProcess(args, code, '', error)
        return actual(args, **kwargs)
    monkeypatch.setattr(gs, '_run_git', failed)
    attempt = str(uuid.uuid4())
    gs.approve_merge_review(group, mid, attempt_id=attempt,
        review_fingerprint=resolved['review_fingerprint'], authority='human')
    context = gs.db_git.session_context(gs.db_git.get_session(mid))
    commit = context['merge_commit']
    root_path = gs.resolve_conflict_src_root(group, mid)
    assert gs._rev_parse(root_path, 'HEAD') == commit
    assert gs._rev_parse(root_path, context['preserve_ref']) == commit
    assert gs._rev_parse(root_path, commit + '^{tree}') == context['snapshot_tree']
    parent_probe = actual(['rev-list', '--parents', '-n', '1', commit], cwd=root_path, timeout=30)
    assert parent_probe.returncode == 0
    assert parent_probe.stdout.split()[1:] == context['preserve_parents'] == [context['base_head'], context['merge_head']]
    assert len([cmd for cmd in calls if 'commit' in cmd]) == 1
    assert len([cmd for cmd in calls if cmd[0] == 'push']) == 1
    assert not any(cmd[0] in ('reset', 'merge') for cmd in calls)
    assert context['approval_attempt_id'] == attempt
    assert context['reconciliation_kind'] == ('reconcile_auth_required' if kind == 'auth' else 'push_remote_unknown')
    assert _intent(mid) == intent and _review_status(ac) == 'pending_review'
    assert job_of(group) == job
    calls.clear()
    replay = gs.approve_merge_review(group, mid, attempt_id=attempt,
        review_fingerprint=resolved['review_fingerprint'], authority='human')['result']
    assert calls == [] and replay['approval']['approved'] is False
    assert replay['approval_attempt_id'] == attempt and replay['git_terminal'] is False
    fault['enabled'] = False
    monkeypatch.setattr(gs, 'PUSH_RECONCILE_DELAYS_SEC', [0])
    view = recovery.get(group, mid)['result']
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': view['approval_attempt_id']}
    assert view['push_error']['cause'] == ('unknown' if kind == 'rejected' else kind)
    assert view['push_error']['returncode'] == failures[kind][0]
    calls.clear()
    result = recovery.mutate(group, mid, action, actor=USER['user_id'], **body)
    assert result['ok']
    assert not any('commit' in cmd for cmd in calls)
    assert not any(cmd[:2] == ['merge', '--abort'] for cmd in calls)
    assert gs._rev_parse(root_path, context['preserve_ref']) == commit
    if action == 'retry':
        assert result['result']['approval']['approved'] is True
        assert result['result']['approval_attempt_id'] == attempt
        assert len([cmd for cmd in calls if cmd[0] == 'push']) == 1
    else:
        assert not any(cmd[0] == 'push' for cmd in calls)
        assert result['result']['session_state'] == 'aborted'
    calls.clear()
    replay = recovery.mutate(group, mid, action, actor=USER['user_id'], **body)
    assert replay['result']['session_state'] == result['result']['session_state'] and calls == []
    assert job_of(group) == job
    assert approval_freeze.freeze_claim_owner(group) is None
    assert [_review_status(other_root), _review_status(other_ac)] == other_before
    assert jobs.get_job(other_job['job_id']) == other_job_before
    assert gs.db_git.get_state(other) == other_state
    assert approval_freeze.freeze_claim_owner(other) == other_job['job_id']
    assert get_store()._fetch_all(
        'SELECT * FROM operation_job WHERE group_id = ? ORDER BY job_id', [group]) == all_jobs_before
    assert _review_status(ac) == ('approved' if action == 'retry' else 'pending_review')
    assert _review_status(root) == ('wf_done' if action == 'retry' else 'wf_in_progress')
    assert _intent(mid) is None
    if action == 'abort':
        assert gs.db_git.get_state(group)['status'] == 'waiting'
    else:
        assert gs.db_git.get_state(group)['status'] == 'merged'
