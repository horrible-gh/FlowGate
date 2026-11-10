"""Durable, explicitly requested recovery of group merge approvals (0153 T#5).

The session context is the journal. Domain locks serialize writers across instances;
the outer session claim fences Git and context writes. Receipts are checked before
versions, and completion always reloads context after the approval consume transaction.
No recovery path creates a final-approval job or changes another group's locks.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from . import approval_intent, merge_target, preserve, session_claim
from .credentials import GitServiceError
from modules.flow_gate.db import merge_request_binding as request_binding


def gs():
    from modules.flow_gate.services import git_service
    return git_service


def clean(value):
    """Only explicitly selected evidence is passed here, never an entire context."""
    if isinstance(value, str):
        return gs()._scrub(value)[:600]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if k not in ('stderr', 'stdout', 'secret')}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value[:100]]
    return value


def original_push_error(context):
    error = context.get('first_push_error') or context.get('last_error')
    found = None
    for _ in range(40):
        if not isinstance(error, dict):
            break
        if error.get('code') == 'push_remote_unknown' or error.get('stage') == 'initial_push':
            found = error
        error = error.get('prior_push_error')
    return clean({k: found.get(k) for k in ('cause', 'returncode', 'detail', 'at', 'instance_id')}) if found else None


def remember_push(context):
    if not context.get('first_push_error'):
        error = original_push_error(context)
        if error:
            context['first_push_error'] = {'code': 'push_remote_unknown', **error}


def supported(session):
    return (session is not None and session.get('group_id')
            and gs().db_git.session_kind(session) == gs().db_git.SESSION_KIND_MERGE
            and not gs()._is_branch_merge_session(session)
            and not merge_target.is_manual_session(session))


def session_of(group_id, merge_id):
    row = gs()._review_session_of_owner(group_id, merge_id)
    if row.get('group_id') != group_id:
        raise GitServiceError(404, 'review_not_found', 'merge session not found')
    if not supported(row):
        raise GitServiceError(409, 'recovery_unsupported', 'recovery is only available for group merges')
    return row


def needed(context):
    return bool(context.get('recovery_hold') or context.get('merge_commit')
                or context.get('approval_attempt_id') or context.get('abort_progress')
                or context.get('review_state') in ('applying', 'reconciling', 'completed'))


def has_preservation(context):
    """A new resolver round still owns earlier results, even without an active attempt."""
    return bool(context.get('preserve_ref') or context.get('preserve_history')
                or context.get('integration_commits') or context.get('abort_snapshot'))


def automatic_blocked(context):
    # An explicit abort remains exclusive after its process/claim has ended. A
    # cleanup failure must never turn the still-preserved commit into another push.
    return bool(context.get('abort_progress') or context.get('recovery_hold'))


def approval_view(context):
    consumed = context.get(approval_intent.CONSUMED_KEY)
    if consumed:
        return {'approved': True, 'stage': 'complete', 'error': None,
                'approval_intent_id': consumed.get('approval_intent_id')}
    result = context.get('approval_result')
    if isinstance(result, dict):
        return clean({**result, 'error': result.get('error')})
    intent = approval_intent.intent_of_context(context)
    return {'approved': False, 'stage': 'pending' if intent else 'not_required', 'error': None,
            'approval_intent_id': (intent or {}).get('approval_intent_id')}


def version(session, context):
    keys = ('review_state', 'review_fingerprint', 'approval_attempt_id', 'merge_commit',
            'apply_phase', 'preserve_round', 'preserve_sha', 'preserve_ref', 'publish_commit',
            'publish_lease', 'reconciliation_kind', 'reconcile_cycle_count', 'reconcile_attempt_count',
            'recovery_hold', 'recovery_generation', 'abort_progress', 'git_terminal', 'approval_result',
            'reconcile_integration_budget_start', 'snapshot_tree', 'base_head', 'merge_head',
            'git_cleanup_done', 'preserve_parents', 'preserve_history', 'integration_commits', 'merge_target',
            'recovery_preserve_parents', 'expected_remote_head', 'published_commit',
            'abort_snapshot', 'resolver_baseline', 'resolver_baseline_history',
            approval_intent.INTENT_KEY, approval_intent.CONSUMED_KEY, approval_intent.DISCARDED_KEY)
    data = {k: context.get(k) for k in keys}
    data['status'] = session.get('status')
    data['finalize_action'] = session.get('finalize_action')
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def refs_of(context):
    entries = []
    if context.get('preserve_ref'):
        entries.append({'ref': context['preserve_ref'], 'sha': context.get('preserve_sha'),
                        'parents': context.get('preserve_parents')})
    entries.extend(context.get('preserve_history') or [])
    entries.extend(context.get('integration_commits') or [])
    if context.get('abort_snapshot'):
        entries.append(context['abort_snapshot'])
    return [{'ref': e.get('ref'), 'sha': e.get('sha'),
             'parents': e.get('parents') or (context.get('recovery_preserve_parents') or {}).get(e.get('ref'))}
            for e in entries if isinstance(e, dict)]


def read_git(root, args, *, optional=False):
    p = gs()._run_git(args, cwd=root, timeout=gs().GIT_LOCAL_TIMEOUT_SEC)
    if optional and p.returncode == 1:
        return None
    if p.returncode:
        raise GitServiceError(500, 'recovery_probe_failed', 'Git evidence could not be read',
                              details={'probe': args[0], 'returncode': p.returncode,
                                       'detail': gs()._reconcile_detail(p.stderr)})
    return (p.stdout or '').strip()


def rev(root, ref, optional=False):
    return read_git(root, ['rev-parse', '--verify', '--quiet', ref], optional=optional)


def checked_target(session):
    target = merge_target.resolve_session_target(session)
    if target.root is None or not target.root.exists():
        raise GitServiceError(409, 'recovery_workspace_missing', 'the recorded workspace is unavailable')
    if merge_target.workspace_ownership(target) != merge_target.OWN_OWNED:
        raise GitServiceError(409, 'recovery_owner_mismatch', 'workspace ownership does not match')
    branch = read_git(target.root, ['symbolic-ref', '-q', 'HEAD'], optional=True)
    # Managed integration workspaces may have a detached HEAD; their owner marker is
    # authoritative, but a shared checkout must still be on its pinned branch.
    if branch != 'refs/heads/' + target.target_branch and not (branch is None and target.managed_workspace and not target.is_project_base):
        raise GitServiceError(409, 'recovery_branch_mismatch', 'the pinned target branch changed')
    return target


def verify_refs(root, entries):
    result = []
    for entry in entries:
        ref, sha = entry.get('ref'), entry.get('sha')
        if not ref or not ref.startswith(preserve.preserve_ref_namespace()) or not gs()._is_sha40(sha):
            raise GitServiceError(409, 'recovery_preserve_invalid', 'preservation evidence is incomplete')
        actual = rev(root, ref, optional=True)
        parents = read_git(root, ['rev-list', '--parents', '-n', '1', sha]).split()[1:]
        if actual != sha or not entry.get('parents') or parents != entry['parents']:
            raise GitServiceError(409, 'recovery_preserve_invalid', 'preserved ref or parents do not match')
        result.append({**entry, 'verification': 'verified'})
    return result


def verify_abort_preservation(session, context):
    """Read shared refs even after our linked workspace was successfully removed."""
    target = merge_target.resolve_session_target(session)
    if target.root is not None and target.root.exists():
        checked_target(session)
        progress = context.get('abort_progress') or {}
        if progress.get('phase') in ('git_cleaned', 'workspace_released'):
            if (rev(target.root, 'HEAD') != progress.get('return_head')
                    or rev(target.root, 'MERGE_HEAD', optional=True)
                    or read_git(target.root, ['status', '--porcelain'])):
                raise GitServiceError(409, 'recovery_local_mismatch', 'local target changed after abort cleanup')
    elif not (target.managed_workspace and not target.is_project_base
              and merge_target.workspace_ownership(target) == merge_target.OWN_ABSENT):
        raise GitServiceError(409, 'recovery_workspace_missing', 'the recorded workspace is unavailable')
    root = gs()._base_root_of(gs()._session_project(session))
    if root is None or not root.exists():
        raise GitServiceError(409, 'recovery_workspace_missing', 'the shared preservation repository is unavailable')
    entries = refs_of(context)
    if not entries:
        raise GitServiceError(409, 'recovery_preserve_invalid', 'abort preservation is missing')
    return verify_refs(root, entries)


def projection(session, *, probe=False):
    s = gs()
    ctx = s.db_git.session_context(session)
    mid = int(session['merge_id'])
    approval = approval_view(ctx)
    terminal = bool(ctx.get('git_terminal') or ctx.get('review_state') == 'completed')
    cleanup_pending = terminal and ctx.get('git_cleanup_done') is False
    aborted = session.get('status') == 'aborted' or ctx.get('apply_phase') == 'aborted'
    aborting = ctx.get('abort_progress') if not aborted else None
    refs = [{**e, 'verification': 'unobserved'} for e in refs_of(ctx)]
    blocked = []
    probe_error = None
    if probe and not terminal and not aborted:
        try:
            if aborting and aborting.get('phase') in ('git_cleaned', 'workspace_released'):
                refs = verify_abort_preservation(session, ctx)
            else:
                target = checked_target(session)
                rev(target.root, 'HEAD')
                rev(target.root, 'MERGE_HEAD', optional=True)
                refs = verify_refs(target.root, refs)
        except GitServiceError as exc:
            blocked.append(exc.code)
            probe_error = clean({'code': exc.code, 'message': exc.message, **exc.details})
            if exc.code == 'recovery_preserve_invalid':
                refs = [{**e, 'verification': 'failed'} for e in refs]
        except Exception:
            blocked.append('recovery_probe_failed')
            probe_error = {'code': 'recovery_probe_failed'}
    actions = ['refresh']
    if not aborted:
        if terminal:
            if cleanup_pending:
                actions.append('retry')
            elif approval['stage'] != 'not_required' and not approval['approved']:
                actions.append('retry_approval')
        elif aborting:
            if not blocked:
                actions.append('abort')
        elif (ctx.get('reconciliation_kind') == s.RECONCILE_KIND_PRESERVE_FAILED
              and blocked == ['recovery_preserve_invalid']):
            actions.append('retry')  # ensure_preserved is create-only and still verifies collisions
        elif needed(ctx) and not blocked:
            actions.extend(['retry', 'abort'])
        elif ctx.get('review_state') in s.REVIEW_PENDING_STATES:
            actions.append('review')
        elif not blocked:
            actions.append('resolve')
        if has_preservation(ctx) and not terminal and not blocked and 'abort' not in actions:
            actions.append('abort')
    kind = ctx.get('reconciliation_kind')
    automatic = bool(ctx.get('review_state') == 'reconciling' and kind in s.RECONCILE_AUTO_RETRY_KINDS
                     and ctx.get('reconcile_next_at') and not ctx.get('recovery_hold') and not aborting)
    next_action = ('refresh' if blocked else 'resume_abort' if aborting else 'retry_approval' if 'retry_approval' in actions else
                   'fix_auth' if kind == s.RECONCILE_KIND_AUTH_REQUIRED else
                   'inspect_retry_budget' if kind == s.RECONCILE_KIND_RETRY_EXHAUSTED else
                   'verify_preservation' if kind == s.RECONCILE_KIND_PRESERVE_FAILED else
                   'inspect_local_state' if kind in (s.RECONCILE_KIND_RESET_FAILED, s.RECONCILE_KIND_LOCAL_STATE_MISMATCH) else
                   'inspect_remote_history' if kind == s.RECONCILE_KIND_PUSH_REMOTE_THIRD else
                   'await_reconcile' if automatic else
                   'review' if ctx.get('review_state') in s.REVIEW_PENDING_STATES else
                   'resolve' if 'resolve' in actions else 'retry' if 'retry' in actions else 'refresh')
    return clean({
        'merge_id': mid, 'session_state': session.get('status'), 'attempt_state': ctx.get('attempt_state'),
        'review_state': ctx.get('review_state'), 'apply_phase': ctx.get('apply_phase'),
        'approval_attempt_id': ctx.get('approval_attempt_id'), 'approval_intent_id': approval.get('approval_intent_id'),
        'recovery_version': version(session, ctx), 'reconciliation_kind': kind,
        'automatic_retry_scheduled': automatic,
        'reconcile_next_at': ctx.get('reconcile_next_at'), 'round': ctx.get('preserve_round', 0),
        'round_retry_count': ctx.get('reconcile_cycle_count', 0),
        'total_retry_count': ctx.get('reconcile_attempt_count', 0),
        'git_terminal': terminal, 'pushed': bool(ctx.get('pushed')), 'approval': approval,
        'requires_recovery': cleanup_pending or (needed(ctx) and not aborted and not
            (terminal and (approval['approved'] or approval['stage'] == 'not_required'))),
        'merge_commit': ctx.get('merge_commit'), 'last_error': ctx.get('last_error'),
        'push_error': original_push_error(ctx), 'preserve_ref': ctx.get('preserve_ref'),
        'preserve_sha': ctx.get('preserve_sha'), 'preserved_refs': refs,
        'recovery_hold': ctx.get('recovery_hold'),
        'resume_missing': (ctx.get('recovery_hold') or {}).get('resume_missing', []),
        'unobserved': [probe_error['code']] if probe_error else [], 'probe_error': probe_error,
        'allowed_actions': actions, 'blocked_reasons': blocked, 'next_action': next_action,
        'remote_outcome': (ctx.get('abort_progress') or {}).get('remote_outcome'),
        'pending_request': pending_abort_request(ctx),
        'deferred': not terminal and not aborted,
    })


def pending_abort_request(context):
    progress = context.get('abort_progress') or {}
    if progress.get('phase') == 'completed':
        return None
    request_id = progress.get('request_id')
    receipt = (context.get('recovery_requests') or {}).get(request_id) or {}
    binding = receipt.get('binding') or {}
    if not request_id or not binding:
        return None
    return {'request_id': request_id, 'action': 'abort',
            'recovery_version': binding['recovery_version'],
            'approval_attempt_id': binding['approval_attempt_id']}


def get(group_id, merge_id):
    return {'ok': True, 'result': projection(session_of(group_id, merge_id), probe=True)}


def replay_result(session, *, status=None):
    ctx = gs().db_git.session_context(session)
    approval = approval_view(ctx)
    terminal = bool(ctx.get('git_terminal') or ctx.get('review_state') == 'completed')
    error = approval.get('error')
    if terminal and ctx.get('git_cleanup_done') is False and not error:
        error = {'code': 'post_push_cleanup', 'message': 'Git is complete; remaining cleanup must be retried'}
    return {'status': status or ('already_applied' if terminal else ctx.get('review_state') or 'reconciling'),
            'review_state': ctx.get('review_state'), 'reconciliation_kind': ctx.get('reconciliation_kind'),
            'merge_commit': (ctx.get('merge_commit') or '')[:7] or None,
            'git_terminal': terminal, 'pushed': bool(ctx.get('pushed')), 'approval': approval,
            'approval_intent_id': approval.get('approval_intent_id'),
            'approval_attempt_id': ctx.get('approval_attempt_id'),
            'error': error, 'deferred': not terminal,
            'recovery': projection(session) if supported(session) else None}


def approval_binding_key(merge_id, attempt_id):
    # Public IDs are UUIDs. Pre-contract internal IDs remain scoped to their session.
    try:
        return str(UUID(attempt_id))
    except (ValueError, TypeError, AttributeError):
        return f"legacy:{merge_id}:{attempt_id}"


def attempt_replay(session, attempt_id, fingerprint):
    """Return a durable receipt before any Git work, or admit a genuinely new candidate."""
    ctx = gs().db_git.session_context(session)
    binding = {'group_id': session.get('group_id'), 'merge_id': int(session['merge_id']), 'review_fingerprint': fingerprint}
    if request_binding.matches('approval', approval_binding_key(session['merge_id'], attempt_id), binding) is False:
        raise GitServiceError(409, 'approval_attempt_reused', 'attempt id belongs to another candidate or session')
    records = ctx.get('approval_attempts') or {}
    record = records.get(attempt_id)
    if record:
        if record['review_fingerprint'] != fingerprint:
            raise GitServiceError(409, 'approval_attempt_reused', 'attempt id belongs to a different candidate')
        if ctx.get('approval_attempt_id') != attempt_id:
            return {'ok': True, 'result': record.get('result') or {
                'status': 'superseded', 'approval_attempt_id': attempt_id,
                'git_terminal': False, 'approval': {'approved': False, 'stage': 'superseded', 'error': None}}}
        return {'ok': True, 'result': replay_result(session, status='aborted' if session.get('status') == 'aborted' else None)}
    if ctx.get('approval_attempt_id'):
        if ctx['approval_attempt_id'] == attempt_id:
            if fingerprint != ctx.get('review_fingerprint'):
                raise GitServiceError(409, 'approval_attempt_reused', 'attempt id belongs to a different candidate')
            return {'ok': True, 'result': replay_result(session)}
        if ctx.get('git_terminal') or ctx.get('review_state') == 'completed':
            raise GitServiceError(409, 'review_not_ready', 'this candidate is already complete')
        raise GitServiceError(409, 'approval_attempt_active', 'the candidate already has an approval attempt',
                              details={'approval_attempt_id': ctx['approval_attempt_id']})
    if session.get('status') != 'open' or ctx.get('abort_progress'):
        raise GitServiceError(409, 'recovery_closed', 'this merge attempt is closed or aborting')
    if ctx.get('recovery_hold') or ctx.get('merge_commit'):
        raise GitServiceError(409, 'recovery_required', 'recover the recorded candidate before approving',
                              details={'recovery': projection(session)})
    return None


def bind_attempt(session, context, attempt_id, fingerprint):
    binding = {'group_id': session.get('group_id'), 'merge_id': int(session['merge_id']), 'review_fingerprint': fingerprint}
    session_claim.require(int(session['merge_id']), 'approval_bind')
    if not request_binding.bind('approval', approval_binding_key(session['merge_id'], attempt_id), binding):
        raise GitServiceError(409, 'approval_attempt_reused', 'attempt id belongs to another candidate or session')
    context.setdefault('approval_attempts', {})[attempt_id] = {
        'group_id': session.get('group_id'), 'merge_id': int(session['merge_id']),
        'approval_attempt_id': attempt_id, 'review_fingerprint': fingerprint,
        'phase': 'applying', 'at': gs().now_iso(), 'instance_id': gs().current_instance_id(),
    }
    context['approval_attempt_id'] = attempt_id


def archive_attempt(context, status):
    # T#2 history keeps the ref/SHA but omits parents. Retain independently proven
    # parent evidence before a handoff replaces base_head/merge_head for a new round.
    if context.get('preserve_ref') and context.get('preserve_parents'):
        context.setdefault('recovery_preserve_parents', {})[context['preserve_ref']] = list(context['preserve_parents'])
    attempt = context.get('approval_attempt_id')
    if attempt and attempt in (context.get('approval_attempts') or {}):
        record = context['approval_attempts'][attempt]
        record['phase'] = status
        record['result'] = {'status': status, 'approval_attempt_id': attempt,
                            'merge_commit': context.get('merge_commit'), 'git_terminal': False,
                            'approval': {'approved': False, 'stage': status, 'error': None}}


def save_approval(merge_id, approval):
    """Consume is in another transaction: never write its old context back."""
    s = gs()
    row = s.db_git.get_session(merge_id)
    fresh = s.db_git.session_context(row)
    fresh['approval_result'] = clean(approval or approval_view(fresh))
    attempt = fresh.get('approval_attempt_id')
    if attempt in (fresh.get('approval_attempts') or {}):
        fresh['approval_attempts'][attempt]['phase'] = 'completed'
    s.db_git.set_session_context(merge_id, fresh)
    return fresh


def recover_commit(session, context, target):
    """Identify one exact approved tree and its two parents; never guess the newest SHA."""
    s = gs()
    root = target.root
    head = rev(root, 'HEAD')
    mh = rev(root, 'MERGE_HEAD', optional=True)
    if context.get('merge_commit'):
        if mh:
            raise GitServiceError(409, 'recovery_local_mismatch', 'a committed candidate cannot own a new MERGE_HEAD')
        if context.get('snapshot_tree') and rev(root, context['merge_commit'] + '^{tree}') != context['snapshot_tree']:
            raise GitServiceError(409, 'recovery_candidate_mismatch', 'the committed tree is not the approved candidate')
        return mh
    if mh:
        if (head != (context.get('base_head') or (context.get('resolver_baseline') or {}).get('base_head'))
                or mh != (context.get('merge_head') or (context.get('resolver_baseline') or {}).get('merge_head'))):
            raise GitServiceError(409, 'recovery_candidate_mismatch', 'the pending merge inputs changed')
        return mh
    candidates = {head}
    if context.get('preserve_sha'):
        candidates.add(context['preserve_sha'])
    expected_ref = preserve.preserve_ref_name(int(session['merge_id']), int(context.get('preserve_round') or 0))
    pinned = rev(root, expected_ref, optional=True)
    if pinned:
        candidates.add(pinned)
    proven = []
    for sha in candidates:
        if not s._is_sha40(sha):
            continue
        check = preserve._check_commit(root, sha, context, expected_ref)
        tree = rev(root, sha + '^{tree}')
        if check is None and context.get('snapshot_tree') and tree == context['snapshot_tree']:
            proven.append(sha)
    if len(proven) != 1:
        raise GitServiceError(409, 'recovery_evidence_missing', 'one exact reviewed commit could not be proven')
    context['merge_commit'] = proven[0]
    context['apply_phase'] = 'committed_local'
    s.db_git.set_session_context(int(session['merge_id']), context)
    return None


def remote_outcome(observed, expected):
    """T0013 4.4: claim "not published" only from a valid observation equal to a
    recorded lease. A failed read (timeout/auth/ref_missing -> None) or an unrecorded
    lease is unknown, never "not published"."""
    s = gs()
    if s._is_sha40(observed) and s._is_sha40(expected) and observed == expected:
        return 'not_published'
    return 'unknown'


def candidate_of(context):
    """Identity of the candidate a started request belongs to. A missing round and the
    first recorded round (0) are the same candidate: the request's own first
    preservation must not make its resend stale. Only a real next round differs."""
    return {'approval_attempt_id': context.get('approval_attempt_id'),
            'review_fingerprint': context.get('review_fingerprint'),
            'preserve_round': preserve._round_of(context)}


def receipt_view(context, request_id):
    receipt = (context.get('recovery_requests') or {}).get(request_id)
    if not isinstance(receipt, dict) or not isinstance(receipt.get('binding'), dict):
        return None
    return {'phase': receipt.get('phase'), 'action': receipt['binding'].get('action')}


def release_hold(context, action):
    if context.get('recovery_hold'):
        context.setdefault('recovery_hold_history', []).append({
            **context.pop('recovery_hold'), 'released_at': gs().now_iso(), 'action': action})


def retry_locked(session, context, request_id):
    s = gs()
    mid = int(session['merge_id'])
    group = session['group_id']
    if context.get('abort_progress'):
        raise GitServiceError(409, 'recovery_aborting', 'resume the recorded abort before retrying')
    if context.get('git_terminal') or context.get('review_state') == 'completed':
        approval_only = session.get('status') != 'open' and context.get('git_cleanup_done') is True
        if not approval_only:
            s._finish_reviewed_attempt(group, mid, s._session_project(session),
                                       (context.get('merge_commit') or '')[:7], bool(context.get('pushed')),
                                       cleanup_slot=False)
            context = s.db_git.session_context(s.db_git.get_session(mid))
            context['git_cleanup_done'] = True
            s.db_git.set_session_context(mid, context)
        intent = approval_intent.intent_of_context(context)
        if intent:
            result = approval_intent.commit_deferred_approval(group, mid, intent)
            context = save_approval(mid, result)
            if not result.get('approved'):
                error = result.get('error') or {'code': 'approval_commit_failed'}
                raise GitServiceError(500, error['code'], clean(error.get('message') or error['code']))
        # Approval-only recovery performs no Git writes, including optional slot
        # teardown. The ordinary terminal-slot cleanup path can handle it later.
        if not approval_only:
            s._cleanup_group_slot(s._session_project(session), group)
        fresh = s.db_git.session_context(s.db_git.get_session(mid))
        if (fresh.get('last_error') or {}).get('code') == 'post_push_cleanup':
            fresh.pop('last_error', None)
            s.db_git.set_session_context(mid, fresh)
        return False
    target = checked_target(session)
    mh = recover_commit(session, context, target)
    if mh:
        # A TTL hold with a still-live merge returns to that exact candidate, without
        # pretending an existing resolution commit is pending again.
        if context.get('merge_commit'):
            raise GitServiceError(409, 'recovery_local_mismatch', 'a committed candidate also has MERGE_HEAD')
        if context.get('snapshot_tree') and not s._live_candidate_matches_snapshot(target.root, context):
            raise GitServiceError(409, 'recovery_candidate_mismatch', 'the reviewed tree changed')
        release_hold(context, 'review' if context.get('snapshot_tree') else 'resolve')
        archive_attempt(context, 'not_committed')
        context['approval_attempt_id'] = None
        context['apply_phase'] = None
        context['review_state'] = s.REVIEW_STATE_PENDING if context.get('snapshot_tree') else None
        context.pop('reconciliation_kind', None)
        # The attempt id changes here, so the receipt completes in the same write: a
        # resend replays this result instead of comparing against the old candidate.
        context['recovery_requests'][request_id]['phase'] = 'completed'
        s.db_git.set_session_context(mid, context)
        return False
    failed = s._preserve_gate(mid, context, target.root, project_id=s._session_project(session), group_id=group)
    if failed:
        raise GitServiceError(409, 'preserve_failed', 'the resolved commit could not be preserved')
    verify_refs(target.root, refs_of(context))
    local, _ = s._reconcile_local_state(target.root, context.get('publish_commit') or context['merge_commit'])
    if local not in (s.reconcile_plan.LOCAL_OK, s.reconcile_plan.LOCAL_RESTORABLE):
        raise GitServiceError(409, 'recovery_local_mismatch', 'the local target has unpreserved changes')
    # Only transport/auth/budget failures gain a user budget. A rewritten remote is
    # re-observed with its original pinned lease, never silently accepted as a new base.
    context.setdefault('recovery_retry_history', []).append({
        'request_id': request_id, 'kind': context.get('reconciliation_kind'),
        'cycles': context.get('reconcile_cycle_count', 0), 'error': clean(context.get('last_error')),
        'at': s.now_iso()})
    context['reconcile_cycle_count'] = 0
    context['reconcile_integration_budget_start'] = len(preserve.round_integrations(mid, context))
    release_hold(context, 'reconcile')
    context['review_state'] = s.REVIEW_STATE_RECONCILING
    context['reconciliation_kind'] = 'push_remote_unknown'
    context['reconcile_next_at'] = s.now_iso()
    context['recovery_generation'] = int(context.get('recovery_generation') or 0) + 1
    context['recovery_requests'][request_id]['phase'] = 'reconcile_prepared'
    context['recovery_requests'][request_id]['attempts_at_prepare'] = int(context.get('reconcile_attempt_count') or 0)
    s.db_git.set_session_context(mid, context)
    return True


def require_snapshotable_index(root):
    staged = set(read_git(root, ['diff', '--cached', '--name-only', '--diff-filter=ACDMRT', '-z']).split('\0')) - {''}
    unstaged = set(read_git(root, ['diff', '--name-only', '--diff-filter=ACDMRT', '-z']).split('\0')) - {''}
    if staged & unstaged:
        raise GitServiceError(409, 'recovery_unpreserved_index', 'staged and working versions differ; save both before abort')


def pending_tree(mid, root):
    """Snapshot tracked working bytes with an isolated index, including conflict markers.

    The real index and MERGE_HEAD are untouched. New untracked files are rejected by
    the caller. Tests set tempfile's root to the run scratch; no source artifacts.
    """
    import tempfile
    from pathlib import Path
    s = gs()
    with tempfile.TemporaryDirectory(prefix="fg-recovery-index-") as directory:
        env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        for args in (["read-tree", "HEAD"], ["add", "-A"], ["write-tree"]):
            session_claim.require(mid, 'recovery_snapshot_index')
            p = s._run_git(args, cwd=root, author_env=env, timeout=s.GIT_LOCAL_TIMEOUT_SEC)
            session_claim.require(mid, 'after_recovery_snapshot_index')
            if p.returncode:
                raise GitServiceError(500, "recovery_snapshot_failed", s._reconcile_detail(p.stderr))
        return p.stdout.strip()


def abort_locked(session, context, request_id, actor):
    s = gs()
    mid = int(session['merge_id'])
    group = session['group_id']
    if context.get('git_terminal') or context.get('review_state') == 'completed':
        raise GitServiceError(409, 'recovery_already_published', 'Git is complete; retry the remaining approval')
    progress = context.get('abort_progress') or {}
    if not progress:
        target = checked_target(session)
        mh = recover_commit(session, context, target)
        if context.get('merge_commit'):
            if s._preserve_gate(mid, context, target.root, project_id=s._session_project(session), group_id=group):
                raise GitServiceError(409, 'preserve_failed', 'preservation is required before abort')
        verify_refs(target.root, refs_of(context))
        head = rev(target.root, 'HEAD')
        if read_git(target.root, ['ls-files', '--others', '--exclude-standard']):
            raise GitServiceError(409, 'recovery_unpreserved_edits', 'untracked files must be saved before abort')
        if mh:
            # A working-tree snapshot cannot save a different staged version of
            # the same path. Refuse before any destructive command in that case.
            require_snapshotable_index(target.root)
            # Preserve the current working bytes off-branch, including new conflict
            # edits. Existing resolution/integration refs were verified above.
            tree = pending_tree(mid, target.root)
            ref = f'{preserve.preserve_ref_name(mid)}-abort-{request_id}'
            sha = rev(target.root, ref, optional=True)
            if not sha:
                p = s._run_session_git(mid, context, 'commit-tree', 'recovery_abort_snapshot',
                    [*s._GIT_IDENT, 'commit-tree', tree, '-p', head, '-p', mh,
                     '-m', f'flowgate: preserve pending review {mid} before abort'], cwd=target.root)
                if p.returncode:
                    raise GitServiceError(500, 'recovery_snapshot_failed', 'the pending review could not be saved')
                sha = p.stdout.strip()
                p = s._run_session_git(mid, context, 'update-ref', 'recovery_abort_snapshot',
                                      ['update-ref', ref, sha, preserve.ZERO_OID], cwd=target.root)
                if p.returncode:
                    raise GitServiceError(500, 'recovery_snapshot_failed', 'the pending review ref could not be saved')
            context['abort_snapshot'] = {'ref': ref, 'sha': sha, 'parents': [head, mh], 'tree': tree}
            verify_refs(target.root, [context['abort_snapshot']])
            if rev(target.root, sha + '^{tree}') != tree:
                raise GitServiceError(409, 'recovery_snapshot_mismatch', 'the saved review tree changed')
        elif read_git(target.root, ['status', '--porcelain', '--untracked-files=no']):
            raise GitServiceError(409, 'recovery_unpreserved_edits', 'local edits must be saved before abort')
        allowed = {context.get('merge_commit'), context.get('publish_commit'), context.get('base_head') or (context.get('resolver_baseline') or {}).get('base_head')}
        allowed.update(e.get('sha') for e in context.get('integration_commits') or [])
        if head not in allowed:
            raise GitServiceError(409, 'recovery_local_mismatch', 'HEAD is not owned by this attempt')
        cfg = s.db_git.get_config(s._session_project(session)) or {}
        remote = s._query_remote_ref_detail(target.root, cfg, target.target_branch)
        observed = remote.get('sha')
        resolved = context.get('merge_commit')
        if resolved and observed and (observed == resolved or s._reconcile_ancestry(target.root, resolved, observed) == s.reconcile_plan.YES):
            context['git_terminal'] = True
            context['git_cleanup_done'] = False
            context['reconciliation_kind'] = 'post_push_cleanup'
            context['pushed'] = True
            context['review_state'] = 'completed'
            context['apply_phase'] = 'completed'
            s.db_git.set_session_context(mid, context)
            raise GitServiceError(409, 'recovery_already_published', 'the remote contains the resolution; retry completion')
        progress = {'request_id': request_id, 'phase': 'prepared', 'actor': actor, 'at': s.now_iso(),
                    'cause': context.get('reconciliation_kind'), 'error': clean(context.get('last_error')),
                    'head': head, 'merge_head': mh, 'return_head': head if mh else context.get('base_head'),
                    'remote_outcome': remote_outcome(observed, context.get('expected_remote_head')),
                    'remote_observation': clean({k: remote.get(k) for k in ('cause', 'returncode', 'detail')})}
        context['abort_progress'] = progress
        s.db_git.set_session_context(mid, context)
    if progress['phase'] == 'prepared':
        target = checked_target(session)
        verify_refs(target.root, refs_of(context))
        head = rev(target.root, 'HEAD')
        mh = rev(target.root, 'MERGE_HEAD', optional=True)
        back = progress['return_head']
        if not s._is_sha40(back):
            raise GitServiceError(409, 'recovery_return_missing', 'safe local return commit is missing')
        if mh:
            require_snapshotable_index(target.root)
            if (mh != progress['merge_head'] or head != progress['head']
                    or pending_tree(mid, target.root) != context['abort_snapshot']['tree']
                    or read_git(target.root, ['ls-files', '--others', '--exclude-standard'])):
                raise GitServiceError(409, 'recovery_unpreserved_edits', 'merge state changed after preservation')
            p = s._run_session_git(mid, context, 'merge', 'recovery_abort', ['merge', '--abort'], cwd=target.root)
            if p.returncode:
                raise GitServiceError(500, 'recovery_abort_failed', s._reconcile_detail(p.stderr))
        elif head != back:
            if head != progress['head'] or read_git(target.root, ['status', '--porcelain']):
                raise GitServiceError(409, 'recovery_local_mismatch', 'local target changed after preservation')
            p = s._run_session_git(mid, context, 'reset', 'recovery_abort', ['reset', '--hard', back], cwd=target.root)
            if p.returncode:
                raise GitServiceError(500, 'recovery_reset_failed', s._reconcile_detail(p.stderr))
        if rev(target.root, 'HEAD') != back or rev(target.root, 'MERGE_HEAD', optional=True) or read_git(target.root, ['status', '--porcelain']):
            raise GitServiceError(500, 'recovery_cleanup_failed', 'local abort postconditions failed')
        progress['phase'] = 'git_cleaned'
        s.db_git.set_session_context(mid, context)
    if progress['phase'] == 'git_cleaned':
        verify_abort_preservation(session, context)
        target = merge_target.resolve_session_target(session)
        if target.managed_workspace and not target.is_project_base and target.target_kind != "worktree":
            ownership = merge_target.workspace_ownership(target)
            if ownership != merge_target.OWN_ABSENT:
                session_claim.require(mid, 'recovery_release_workspace')
                if ownership != merge_target.OWN_OWNED or not merge_target.release_workspace(target):
                    raise GitServiceError(500, 'recovery_workspace_release_failed', 'workspace cleanup is incomplete')
                session_claim.require(mid, 'after_recovery_release_workspace')
        progress['phase'] = 'workspace_released'
        s.db_git.set_session_context(mid, context)
    if progress['phase'] == 'workspace_released':
        verify_abort_preservation(session, context)
        # Discard, session/attempt closure and ledger transition commit together. A
        # crash after Git cleanup can replay this transaction without another reset.
        from modules.flow_gate.db.connection import get_store
        session_claim.require(mid, 'recovery_abort_transaction')
        with get_store().transaction():
            approval_intent.discard_intent(mid, reason='preserve_abort:' + request_id)
            context = s.db_git.session_context(s.db_git.get_session(mid))
            context['abort_progress']['phase'] = 'completed'
            context['apply_phase'] = 'aborted'
            context['attempt_state'] = 'aborted'
            context['review_state'] = None
            context['reconcile_next_at'] = None
            context.pop('reconciliation_kind', None)
            release_hold(context, 'abort')
            archive_attempt(context, 'aborted')
            s.db_git.set_session_context(mid, context)
            s.db_git.invalidate_resolution_checkpoint_for_merge(mid)
            if not merge_target.close_session_attempt(s.db_git.get_session(mid), merge_target.ATTEMPT_ABORTED,
                                                      error={'code': 'preserve_abort', 'request_id': request_id}):
                raise GitServiceError(500, 'recovery_close_failed', 'attempt closure failed')
            s._set_status(group, 'waiting', emit=False)
        s._emit_pending_changed(s._session_project(session), group, 'waiting')


def mutate(group_id, merge_id, action, *, request_id, recovery_version, approval_attempt_id, actor=None):
    s = gs()
    try:
        request_id = str(UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        raise GitServiceError(400, 'invalid_request_id', 'request_id must be a UUID')
    if action not in ('retry', 'abort'):
        raise GitServiceError(400, 'invalid_recovery_action', 'unknown recovery action')
    session = session_of(group_id, merge_id)
    claim = session_claim.acquire(merge_id, session_claim.PURPOSE_RECONCILE)
    if claim is None:
        raise GitServiceError(409, 'recovery_busy', 'another session operation is active')
    from . import branch_merge_publish as bmp
    binding = {'action': action, 'group_id': group_id, 'merge_id': merge_id,
               'recovery_version': recovery_version, 'approval_attempt_id': approval_attempt_id}
    execute_reconcile = False
    try:
        lock, held = bmp.session_lock(session, s._session_project(session), holder_kind='review_action', publish=True)
        try:
            session = session_of(group_id, merge_id)
            context = s.db_git.session_context(session)
            if request_binding.matches('recovery', request_id, binding) is False:
                raise GitServiceError(409, 'recovery_request_reused', 'request id belongs to another payload or session')
            receipt = (context.get('recovery_requests') or {}).get(request_id)
            if receipt:
                if receipt['binding'] != binding:
                    raise GitServiceError(409, 'recovery_request_reused', 'request_id is bound to another payload')
                if receipt.get('phase') == 'completed':
                    return receipt_response(session, request_id, receipt)
                candidate = candidate_of(context)
                if (receipt.get('phase') == 'started' and isinstance(receipt.get('candidate'), dict)
                        and candidate_of(receipt['candidate']) != candidate):
                    raise GitServiceError(409, 'recovery_stale', 'the pending request belongs to an older candidate')
                # Prepared retry is a durable one-shot budget grant. Resume only the
                # existing reconcile state, without resetting counters or the round.
                execute_reconcile = receipt.get('phase') == 'reconcile_prepared'
                if execute_reconcile and int(context.get('reconcile_attempt_count') or 0) > receipt.get('attempts_at_prepare', -1):
                    receipt['phase'] = 'completed'
                    s.db_git.set_session_context(merge_id, context)
                    return receipt_response(session_of(group_id, merge_id), request_id, receipt)
            else:
                if recovery_version != version(session, context) or approval_attempt_id != context.get('approval_attempt_id'):
                    raise GitServiceError(409, 'recovery_stale', 're-read recovery before acting')
                if session.get('status') == 'aborted':
                    raise GitServiceError(409, 'recovery_closed', 'this attempt was aborted')
                from . import approval_freeze
                if approval_freeze.freeze_claim_owner(group_id):
                    raise GitServiceError(409, 'recovery_job_busy', 'the approval job still owns its freeze')
                state = s.db_git.get_state(group_id) or {}
                if state.get('merge_id') and int(state['merge_id']) != int(merge_id):
                    raise GitServiceError(409, 'recovery_owner_mismatch', 'another attempt owns the group ledger')
                session_claim.require(merge_id, 'recovery_prepare')
                if not request_binding.bind('recovery', request_id, binding):
                    raise GitServiceError(409, 'recovery_request_reused', 'request id belongs to another payload or session')
                receipt = {'binding': binding, 'phase': 'started', 'actor': actor, 'at': s.now_iso(),
                           'candidate': candidate_of(context)}
                context.setdefault('recovery_requests', {})[request_id] = receipt
                s.db_git.set_session_context(merge_id, context)
            from . import approval_freeze
            if approval_freeze.freeze_claim_owner(group_id):
                raise GitServiceError(409, 'recovery_job_busy', 'the approval job still owns its freeze')
            ledger = s.db_git.get_state(group_id) or {}
            if ledger.get('merge_id') and int(ledger['merge_id']) != int(merge_id):
                raise GitServiceError(409, 'recovery_owner_mismatch', 'another attempt owns the group ledger')
            if not execute_reconcile:
                if action == 'abort':
                    abort_locked(session, context, request_id, actor)
                else:
                    execute_reconcile = retry_locked(session, context, request_id)
                context = s.db_git.session_context(s.db_git.get_session(merge_id))
                receipt = context['recovery_requests'][request_id]
                receipt['phase'] = 'reconcile_prepared' if execute_reconcile else 'completed'
                s.db_git.set_session_context(merge_id, context)
        finally:
            bmp.session_unlock(lock, held)
        if execute_reconcile:
            # Keep the claim, release the domain lock; reconcile re-enters the claim
            # and obtains its own lock. Never session_lock -> claim or recursive lock.
            result = s.reconcile_push_session(merge_id, trigger='server_startup')
            if (result or {}).get('result', {}).get('status') == 'claim_lost':
                raise session_claim.ClaimLost(merge_id, 'recovery_reconcile')
            if result is None:
                # Another domain operation can win between unlock and reconcile.
                # Keep the accepted one-shot budget receipt for a later replay.
                return receipt_response(session_of(group_id, merge_id), request_id, receipt)
            lock, held = bmp.session_lock(session, s._session_project(session), holder_kind='review_action', publish=True)
            try:
                context = s.db_git.session_context(s.db_git.get_session(merge_id))
                receipt = context['recovery_requests'][request_id]
                receipt['phase'] = 'completed'
                s.db_git.set_session_context(merge_id, context)
            finally:
                bmp.session_unlock(lock, held)
        session = session_of(group_id, merge_id)
        return receipt_response(session, request_id, receipt)
    except session_claim.ClaimLost:
        raise GitServiceError(409, 'recovery_claim_lost', 'session ownership changed; re-read recovery')
    except GitServiceError as exc:
        exc.details['recovery'] = error_projection(group_id, merge_id, request_id, exc.details.get('recovery'))
        raise
    except Exception:
        raise GitServiceError(500, 'recovery_failed', 'recovery is incomplete; retry the same request',
                              details={'recovery': error_projection(group_id, merge_id, request_id, None)})
    finally:
        session_claim.release(claim)


def error_projection(group_id, merge_id, request_id, prior):
    """The error envelope always says what happened to this request. A completed
    receipt (e.g. auth still failing after its one budget) tells the client to mint a
    new request_id for the next explicit retry; a started/prepared receipt keeps the
    key for a transport resend. A receipt already in the envelope is never dropped."""
    session = session_of(group_id, merge_id)
    result = projection(session)
    receipt = receipt_view(gs().db_git.session_context(session), request_id)
    if receipt is None and isinstance(prior, dict) and prior.get('request_id') == request_id:
        receipt = prior.get('receipt')
    result['request_id'] = request_id
    result['receipt'] = receipt
    return result


def receipt_response(session, request_id, receipt):
    result = projection(session)
    result['request_id'] = request_id
    result['receipt'] = {'phase': receipt['phase'], 'action': receipt['binding']['action']}
    result['status'] = 'aborted' if session.get('status') == 'aborted' else ('merged' if result['git_terminal'] else result['review_state'] or 'conflict')
    if result['git_terminal'] and result['approval'].get('error'):
        error = result['approval']['error']
        raise GitServiceError(500, error['code'], clean(error.get('message') or error['code']), details={'recovery': result})
    if result['git_terminal'] and gs().db_git.session_context(session).get('git_cleanup_done') is False:
        raise GitServiceError(500, 'post_push_cleanup', 'Git is complete; remaining cleanup must be retried',
                              details={'recovery': result})
    if result['reconciliation_kind'] in (gs().RECONCILE_KIND_PRESERVE_FAILED, gs().RECONCILE_KIND_RESET_FAILED,
                                       gs().RECONCILE_KIND_LOCAL_STATE_MISMATCH, gs().RECONCILE_KIND_AUTH_REQUIRED,
                                       gs().RECONCILE_KIND_PUSH_REMOTE_THIRD, gs().RECONCILE_KIND_RETRY_EXHAUSTED):
        raise GitServiceError(409, result['reconciliation_kind'], 'recovery needs the reported cause to be addressed',
                              details={'recovery': result})
    return {'ok': True, 'result': result}
