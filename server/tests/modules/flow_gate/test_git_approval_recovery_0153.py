"""0153 T#5: durable approval/recovery journal with real Git and sqlite.

Only remote command failures are injected. Locks, claims, preservation refs and the
deferred AC/root/consume transaction are real. Fixtures reuse T#2 and 0555.
"""
import copy
import json
import subprocess
import uuid
from types import SimpleNamespace

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from tests.modules.flow_gate.test_git_preserve_ref_0153 import (  # noqa: F401
    env, project, repo, git, git_rc, saved, names, ref_value, parents_of, preserve_refs,
    open_session, approve_ready, approve, merge_commit, approved, on, push_timeout, PROJECT,
)
from tests.modules.flow_gate import test_final_approval_conflict_intent_0555 as fa
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, branch_merge_publish as bmp
from modules.flow_gate.services.git import merge_target, session_claim, lock_manager, instance_registry
from modules.flow_gate.db import documents, git_concurrency as gc
from modules.flow_gate.db.connection import get_store

REAL_OWNER = gs._review_session_of_owner
REAL_VALIDATE = gs._validate_review_changed_paths
REAL_COMPLETE = gs._complete_merge_review
REAL_LOCK, REAL_UNLOCK = bmp.session_lock, bmp.session_unlock


@pytest.fixture
def real(env, monkeypatch):
    session_claim._reset_thread_state()
    for iid in ('inst_A', 'inst_B'):
        get_store()._execute('DELETE FROM server_instance WHERE instance_id = ?', [iid])
        gc.insert_instance(iid, '0153-other-node', 4242, None, env.clock['now'])
    monkeypatch.setattr(bmp, 'session_lock', REAL_LOCK)
    monkeypatch.setattr(bmp, 'session_unlock', REAL_UNLOCK)
    monkeypatch.setattr(gs, '_complete_merge_review', REAL_COMPLETE)
    monkeypatch.setattr(gs, '_base_root_of', lambda p: env.repo.work)
    monkeypatch.setattr(gs, '_cleanup_group_slot', lambda *a, **kw: None)
    monkeypatch.setattr(gs, '_emit_pending_changed', lambda *a, **kw: None)
    monkeypatch.setattr(merge_target, 'resolve_session_target', lambda row: merge_target.MergeTargetContext(
        project_id=PROJECT, base_branch='main', target_branch='main', is_project_base=True,
        root=env.repo.work, merge_id=int(row['merge_id']), legacy=True, managed_workspace=False))
    monkeypatch.setattr(fa, 'PROJECT', PROJECT)
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    from flowgate.card_document_edit import integration
    monkeypatch.setattr(ports, '_handlers', dict(ports._handlers))
    integration.register()
    root, ac = fa._seed_final_approval_group(env.group)
    env.root_id, env.ac_id = root, ac
    env.intent = approval_intent.build_intent(group_id=env.group, ac_doc_id=ac,
        requested_by=fa.USER['user_id'], git_action='merge', approval_intent_id=str(uuid.uuid4()))
    yield env
    session_claim._reset_thread_state()


def ready(real):
    mid, fp = approve_ready(real)
    real.monkeypatch.setattr(gs, '_review_session_of_owner', REAL_OWNER)
    real.monkeypatch.setattr(gs, '_validate_review_changed_paths', REAL_VALIDATE)
    ctx = saved(mid)
    ctx[approval_intent.INTENT_KEY] = real.intent
    gs.db_git.set_session_context(mid, ctx)
    gs._set_status(real.group, 'conflict', merge_id=mid)
    return mid, fp


def request(real, mid, action='retry', body=None):
    if body is None:
        view = recovery.get(real.group, mid)['result']
        body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
                'approval_attempt_id': view['approval_attempt_id']}
    return gs.mutate_merge_recovery(real.group, mid, action, actor='reviewer', **body), body


# T0013 section 5: shared postconditions, checked against real rows below.
TRANSITIONS = {
    'pending': ('conflict', 'open', 'pending_review', 'wf_in_progress', True),
    'approval_pending': ('merged', 'done', 'pending_review', 'wf_in_progress', True),
    'approved': ('merged', 'done', 'approved', 'wf_done', False),
    'aborted': ('waiting', 'aborted', 'pending_review', 'wf_in_progress', False),
}


def assert_transition(real, mid, phase):
    group, session, ac, root, live_intent = TRANSITIONS[phase]
    assert gs.db_git.get_state(real.group)['status'] == group
    assert gs.db_git.get_session(mid)['status'] == session
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == ac
    assert documents.get_by_id(real.root_id)['doc_review_status'] == root
    assert bool(approval_intent.intent_of_context(saved(mid))) is live_intent


def pending(real, mid):
    assert_transition(real, mid, 'approval_pending' if saved(mid).get('git_terminal') else 'pending')
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'pending_review'
    assert documents.get_by_id(real.root_id)['doc_review_status'] == 'wf_in_progress'
    assert approval_intent.intent_of_context(saved(mid)) == real.intent


def terminal(real, mid):
    assert_transition(real, mid, 'approved')
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'approved'
    assert documents.get_by_id(real.root_id)['doc_review_status'] == 'wf_done'
    assert approval_intent.intent_of_context(saved(mid)) is None
    assert gs.db_git.get_session(mid)['status'] == 'done'
    assert gs.db_git.get_state(real.group)['status'] == 'merged'


def fail_push(real, kind='timeout'):
    errors = {'timeout': (-1, 'timeout_expired'), 'auth': (128, 'fatal: Authentication failed'),
              'unknown': (128, 'opaque failure'), 'rejected': (1, ' ! [rejected] main -> main (stale info)')}
    rc, err = errors[kind]
    hook = on('push', lambda args: subprocess.CompletedProcess(args, rc, '', err))
    real.hooks.append(hook)
    return hook


@pytest.mark.parametrize('kind', ['timeout', 'auth', 'unknown', 'rejected'])
def test_A1_initial_failure_is_non_destructive(real, kind):
    mid, fp = ready(real)
    fail_push(real, kind)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    ctx = saved(mid)
    sha = ctx['merge_commit']
    assert ref_value(real.repo.work, 'HEAD') == sha == ref_value(real.repo.work, ctx['preserve_ref'])
    assert parents_of(real.repo.work, sha) == [real.repo.B, real.repo.F]
    assert git(['rev-parse', sha + '^{tree}'], real.repo.work) == ctx['snapshot_tree']
    assert ref_value(real.repo.bare, 'main') == real.repo.B
    assert 'reset' not in names(real)
    assert ctx['approval_attempt_id'] == attempt
    assert ctx['reconciliation_kind'] == ('reconcile_auth_required' if kind == 'auth' else 'push_remote_unknown')
    pending(real, mid)
    view = recovery.get(real.group, mid)['result']
    assert view['push_error']['returncode'] == (-1 if kind == 'timeout' else 1 if kind == 'rejected' else 128)
    assert view['preserved_refs'][0]['verification'] == 'verified'


def test_A2_same_attempt_replay_different_payload_and_id(real):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    real.commands.clear()
    out = approve(real, mid, attempt, fp)['result']
    assert not out['git_terminal'] and out['approval']['approved'] is False
    assert out['approval_attempt_id'] == attempt
    assert not real.commands
    for key, fingerprint, code in [(attempt, 'other', 'approval_attempt_reused'),
                                    (str(uuid.uuid4()), fp, 'approval_attempt_active')]:
        with pytest.raises(gs.GitServiceError) as exc:
            approve(real, mid, key, fingerprint)
        assert exc.value.code == code and exc.value.status == 409
    assert not real.commands


def test_A3_commit_audit_db_failure_recovers_exact_commit(real, monkeypatch):
    mid, fp = ready(real)
    original = gs.db_git.set_session_context
    lost = []
    def fail_once(merge_id, ctx):
        if not lost and ctx.get('apply_phase') == 'committing' and any(
            e.get('reason') == 'approve_merge_review' for e in ctx.get('ref_operations') or []):
            lost.append(True)
            raise RuntimeError('injected write loss after commit')
        return original(merge_id, ctx)
    monkeypatch.setattr(gs.db_git, 'set_session_context', fail_once)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    sha = ref_value(real.repo.work, 'HEAD')
    assert sha != real.repo.B and saved(mid)['apply_phase'] == 'commit_unknown'
    assert saved(mid)['approval_attempt_id'] == attempt
    real.commands.clear()
    result, body = request(real, mid)
    assert result['result']['approval']['approved']
    terminal(real, mid)
    assert saved(mid)['merge_commit'] == sha
    assert 'commit' not in names(real)
    real.commands.clear()
    again, _ = request(real, mid, body=body)
    assert again['result']['approval']['approved'] and not real.commands




























# ---------------------------------------------------------------------------
# TR0014 rev1 (review rej_01M71H9VTMH02NF3): receipt in error envelopes, the
# request's own first preservation, and unknown remote outcomes.
# ---------------------------------------------------------------------------

def route_call(routes, real, mid, action, body):
    handler = routes.post_merge_recovery_retry if action == 'retry' else routes.post_merge_recovery_abort
    response = handler(real.group, mid, routes.RecoveryBody(**body), {'user_id': 'reviewer'})
    if isinstance(response, dict):
        return 200, response
    return response.status_code, json.loads(response.body)






def legacy_commit_without_round(real, monkeypatch):
    """A committed candidate whose DB write was lost after commit: no preserve round
    has been recorded yet (T0013 3.1 / A3)."""
    mid, fp = ready(real)
    original = gs.db_git.set_session_context
    lost = []
    def fail_once(merge_id, ctx):
        if not lost and ctx.get('apply_phase') == 'committing' and any(
                e.get('reason') == 'approve_merge_review' for e in ctx.get('ref_operations') or []):
            lost.append(True)
            raise RuntimeError('injected write loss after commit')
        return original(merge_id, ctx)
    monkeypatch.setattr(gs.db_git, 'set_session_context', fail_once)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    monkeypatch.setattr(gs.db_git, 'set_session_context', original)
    ctx = saved(mid)
    ctx.pop('preserve_round', None)
    gs.db_git.set_session_context(mid, ctx)
    assert saved(mid).get('preserve_round') is None
    return mid, fp, attempt, ref_value(real.repo.work, 'HEAD')


def crash_after_first_preservation(monkeypatch):
    """Stop the request right after _preserve_gate persisted round 0."""
    real_verify = recovery.verify_refs
    fired = []
    def crash(root, entries):
        if not fired:
            fired.append(True)
            raise RuntimeError('injected crash after the first preservation write')
        return real_verify(root, entries)
    monkeypatch.setattr(recovery, 'verify_refs', crash)
    return fired
