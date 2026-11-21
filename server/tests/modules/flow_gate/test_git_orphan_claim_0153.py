"""0153 T#4: the orphan guard and the multi-instance session claim.

``ORPHAN_VERDICTS`` is the verdict table of T0011 §3.1 as module data; the pure
``orphan_guard`` functions are asserted row by row without Git (U1). The claim service is
exercised against the real sqlite ``git_merge_session_claim`` table of migration 181 and
real ``server_instance`` rows (130) for ``inst_A`` / ``inst_B`` / ``inst_C`` on another node
(U2, U3). The scenarios S*/R* run real Git on a local-path bare ``origin`` with the
sqlite/session/Git fixtures of ``test_git_preserve_ref_0153.py`` / ``test_git_reconcile_0153.py``.

``env`` disables ``session_lock``; here it is replaced by a *recording* lock (merge id,
holder kind, mode, acquire/release order), and every session write, orphan close, intent
discard, attempt close and checkpoint invalidation is recorded with "inside the lock?" and
"inside a live claim?" so the scenarios can assert that every close / hold / resume write
happened under both. "Live" is read from the DB, not from the claim object's memory: the
claim row still carries this thread's token and its lease is valid for its holder
(review rej_01M6ZWFPF1E6JE2M). A session write is recorded after it landed (the claim guard
inside ``set_session_context`` refuses a lost one). The instance switch is
``env.clock['instance']``; DEAD is the instance row's ``status='dead'``. ``instance_state``
is never patched.
"""
from __future__ import annotations

import ast
import inspect
import logging
import re
import subprocess
from pathlib import Path

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from tests.modules.flow_gate.test_git_preserve_ref_0153 import (  # noqa: F401
    AT, INTENT, LATER, PROJECT, _only, approved, env, git, git_rc, merge_commit, names, on,
    open_session, preserve_refs, project, push_rejected, push_timeout, ref_value, repo, saved, unknown_session,
)
from tests.modules.flow_gate.test_git_reconcile_0153 import (  # noqa: F401
    assert_consistent, assert_lease_only, common, conflict_session, count, origin_main, pushes,
    remote_commit, unmerged,
)
from modules.flow_gate.db import git_concurrency as gc
from modules.flow_gate.db import merge_session_claim as db_claim
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import approval_intent, branch_merge_publish, cleanup
from modules.flow_gate.services.git import instance_registry as registry
from modules.flow_gate.services.git import lock_manager as locks
from modules.flow_gate.services.git import orphan_guard as og
from modules.flow_gate.services.git import preserve
from modules.flow_gate.services.git import session_claim

pytestmark = pytest.mark.skipif(__import__('shutil').which('git') is None, reason='git binary unavailable')

SERVER = Path(__file__).resolve().parents[3]
CLAIM_LOG = 'modules.flow_gate.services.git.session_claim'
OTHER_NODE = 'node-t4-other'
INSTANCES = ('inst_A', 'inst_B', 'inst_C')
FUTURE = '2099-01-01T00:00:00+00:00'
PAST = '2000-01-01T00:00:00+00:00'
OLD = '2020-01-01T00:00:00+00:00'

# Test-fixture SHAs for the pure table (not operational values).
M, B, H, X = ('1' * 40, 'b' * 40, '3' * 40, '5' * 40)


class ProcessDeath(BaseException):
    """A BaseException that is not an Exception: the process dies mid-reconcile."""


@pytest.fixture(autouse=True)
def _fresh_claim_thread_state():
    session_claim._reset_thread_state()
    yield
    session_claim._reset_thread_state()


# ── U1: the verdict table (T0011 §3.1) ───────────────────────────────────────

INTENT_CTX = {approval_intent.INTENT_KEY: dict(INTENT)}
RESUMABLE = dict(INTENT_CTX, merge_commit=M, expected_remote_head=B, review_state=None)
RESUME_FACTS = dict(merge_head_present=False, base_root_exists=True, head_sha=M,
                    worktree_clean=True, preserve_verified=True)

EVIDENCE_ALONE = [
    ('merge_commit', {'merge_commit': M}, []),
    ('publish_commit', {'publish_commit': M}, []),
    ('published_commit', {'published_commit': M}, []),
    ('preserve_ref', {'preserve_ref': 'refs/flowgate/preserve/7'}, []),
    ('preserve_sha', {'preserve_sha': M}, []),
    ('preserve_history', {'preserve_history': [{'ref': 'refs/flowgate/preserve/7', 'sha': M}]}, []),
    ('integration_commits', {'integration_commits': [{'sha': H}]}, []),
    ('preserve_ref_on_disk', {}, ['refs/flowgate/preserve/7-r1']),
    ('reconcile_attempt_count', {'reconcile_attempt_count': 1}, []),
    ('reconciliation_kind', {'reconciliation_kind': 'reconcile_auth_required'}, []),
    ('resolver_baseline_source', {'resolver_baseline': {'source': 'reconcile_remote_conflict'}}, []),
    ('resolver_baseline_history', {'resolver_baseline_history': [{'base_head': B}]}, []),
    ('reconciling', {'review_state': 'reconciling'}, []),
    ('applying', {'review_state': 'applying'}, []),
]
EVIDENCE_NAME = {
    'published_commit': 'publish_commit', 'preserve_sha': 'preserve_ref',
    'preserve_history': 'preserve_ref', 'integration_commits': 'preserve_ref',
    'reconcile_attempt_count': 'reconcile_history', 'reconciliation_kind': 'reconcile_history',
    'resolver_baseline_source': 'reconcile_handoff', 'resolver_baseline_history': 'reconcile_handoff',
    'applying': 'reconciling',
}

ORPHAN_VERDICTS = [
    # line 1: MERGE_HEAD present wins over everything
    dict(id='merge_head_with_evidence', context=dict(RESUMABLE, reconcile_attempt_count=2), disk=[],
         facts=dict(RESUME_FACTS, merge_head_present=True), expected=og.NOT_ORPHAN),
    dict(id='merge_head_without_evidence', context={}, disk=[],
         facts=dict(merge_head_present=True, base_root_exists=True), expected=og.NOT_ORPHAN),
    # line 2: states owned by other sweep branches
    *[dict(id=f'delegate_{state}', context=dict(RESUMABLE, review_state=state), disk=[],
           facts=dict(RESUME_FACTS), expected=og.DELEGATE)
      for state in ('reconciling', 'applying', 'completed')],
    # line 3: an existing hold is kept, never re-written
    dict(id='hold_keep', context=dict(RESUMABLE, recovery_hold={'code': og.HOLD_CODE}), disk=[],
         facts=dict(RESUME_FACTS), expected=og.HOLD_KEEP),
    # line 4: no evidence keeps the old orphan close
    dict(id='no_evidence', context=dict(INTENT_CTX, review_state='resolved_pending_review'), disk=[],
         facts=dict(base_root_exists=True, head_sha=B, worktree_clean=True), expected=og.CLOSE_ORPHAN),
    dict(id='no_evidence_root_gone', context={}, disk=[], facts={}, expected=og.CLOSE_ORPHAN),
    # every evidence item alone is never a close
    # (reconciling/applying alone are evidence too, decided as delegate by line 2)
    *[dict(id=f'alone_{name}', context=dict(INTENT_CTX, **ctx), disk=disk,
           facts=dict(base_root_exists=True, head_sha=B, worktree_clean=True),
           expected=og.DELEGATE if EVIDENCE_NAME.get(name, name) == 'reconciling' else og.HOLD)
      for name, ctx, disk in EVIDENCE_ALONE],
    # line 5: every resume condition holds
    dict(id='resume', context=dict(RESUMABLE), disk=[], facts=dict(RESUME_FACTS),
         expected=og.RESUME_RECONCILE),
    dict(id='resume_on_publish_commit', context=dict(RESUMABLE, publish_commit=H), disk=[],
         facts=dict(RESUME_FACTS, head_sha=H), expected=og.RESUME_RECONCILE),
    # line 6: one resume condition missing each → hold (with the missing condition named)
    dict(id='missing_merge_commit', context=dict(RESUMABLE, merge_commit=None, preserve_sha=M), disk=[],
         facts=dict(RESUME_FACTS), expected=og.HOLD, missing=(og.RC_SHAS, og.RC_HEAD)),
    dict(id='missing_expected_remote_head', context=dict(RESUMABLE, expected_remote_head=None), disk=[],
         facts=dict(RESUME_FACTS), expected=og.HOLD, missing=(og.RC_SHAS,)),
    dict(id='missing_preserve_verified', context=dict(RESUMABLE), disk=[],
         facts=dict(RESUME_FACTS, preserve_verified=False), expected=og.HOLD, missing=(og.RC_PRESERVE,)),
    dict(id='missing_head_match', context=dict(RESUMABLE), disk=[],
         facts=dict(RESUME_FACTS, head_sha=B), expected=og.HOLD, missing=(og.RC_HEAD,)),
    dict(id='head_is_merge_but_publish_differs', context=dict(RESUMABLE, publish_commit=H), disk=[],
         facts=dict(RESUME_FACTS, head_sha=M), expected=og.HOLD, missing=(og.RC_HEAD,)),
    dict(id='missing_worktree_clean', context=dict(RESUMABLE), disk=[],
         facts=dict(RESUME_FACTS, worktree_clean=False), expected=og.HOLD, missing=(og.RC_CLEAN,)),
    dict(id='missing_base_root', context=dict(RESUMABLE), disk=[],
         facts=dict(RESUME_FACTS, base_root_exists=False), expected=og.HOLD, missing=(og.RC_ROOT,)),
    dict(id='missing_intent', context={k: v for k, v in RESUMABLE.items() if k != approval_intent.INTENT_KEY},
         disk=[], facts=dict(RESUME_FACTS), expected=og.HOLD, missing=(og.RC_INTENT,)),
    # line 3b (review rej_01M6ZWFPF1E6JE2M): a failed read decides nothing — no close, no resume
    dict(id='unobserved_refs_without_context_evidence', context=dict(INTENT_CTX, review_state='resolved_pending_review'),
         disk=[], facts=dict(base_root_exists=True, head_sha=B, worktree_clean=True, unobserved=(og.OBS_PRESERVE_REFS,)),
         expected=og.UNOBSERVED, missing=(og.OBS_PRESERVE_REFS,)),
    dict(id='unobserved_merge_head_of_a_resumable_session', context=dict(RESUMABLE), disk=[],
         facts=dict(RESUME_FACTS, unobserved=(og.OBS_MERGE_HEAD,)), expected=og.UNOBSERVED,
         missing=(og.OBS_MERGE_HEAD,)),
    dict(id='hold_keep_wins_over_unobserved', context=dict(RESUMABLE, recovery_hold={'code': og.HOLD_CODE}), disk=[],
         facts=dict(RESUME_FACTS, unobserved=(og.OBS_PRESERVE_REFS,)), expected=og.HOLD_KEEP),
]


def _facts(row):
    evidence = og.recovery_evidence(row['context'], preserve_refs_on_disk=row['disk'])
    return og.Facts(context=row['context'], evidence=tuple(evidence), **row['facts'])


@pytest.mark.parametrize('row', ORPHAN_VERDICTS, ids=[r['id'] for r in ORPHAN_VERDICTS])
def test_U1_orphan_verdict_table(row):
    verdict = og.orphan_verdict(_facts(row))
    assert verdict.action == row['expected']
    if 'missing' in row:
        assert verdict.missing == row['missing']


def test_U1_every_evidence_item_alone_is_never_a_close():
    rows = [r for r in ORPHAN_VERDICTS if r['id'].startswith('alone_')]
    assert len(rows) == len(EVIDENCE_ALONE)
    for name, ctx, disk in EVIDENCE_ALONE:
        assert og.recovery_evidence(ctx, preserve_refs_on_disk=disk) == [EVIDENCE_NAME.get(name, name)]
    for row in rows:
        assert og.orphan_verdict(_facts(row)).action != og.CLOSE_ORPHAN, row['id']
    # each of the 7 evidence names is covered by at least one single-item row
    covered = {EVIDENCE_NAME.get(name, name) for name, _c, _d in EVIDENCE_ALONE}
    assert covered == set(og.EVIDENCE_ORDER)


def test_U1_evidence_order_is_fixed_and_disk_refs_are_this_merge_only():
    ctx = {'merge_commit': M, 'published_commit': M, 'preserve_history': [{}], 'review_state': 'reconciling',
           'reconciliation_kind': 'x', 'resolver_baseline_history': [{}]}
    assert og.recovery_evidence(ctx, preserve_refs_on_disk=['r']) == list(og.EVIDENCE_ORDER)
    names_on_disk = ['refs/flowgate/preserve/7', 'refs/flowgate/preserve/7-r2', 'refs/flowgate/preserve/7-i1',
                     'refs/flowgate/preserve/7-r1-i2', 'refs/flowgate/preserve/71', 'refs/flowgate/preserve/8-r1',
                     'refs/flowgate/preserve/7x', 'refs/heads/main', '']
    assert og.preserve_refs_of(7, names_on_disk) == names_on_disk[:4]
    assert og.recovery_evidence({'merge_commit': 'short'}, preserve_refs_on_disk=[]) == []


def test_U1_hold_and_resume_records():
    facts = og.Facts(context={'review_state': 'resolved_pending_review'}, evidence=('preserve_ref', 'reconcile_history'),
                     merge_head_present=False, base_root_exists=True, head_sha=B, worktree_clean=True)
    assert og.hold_record(facts, trigger=og.TRIGGER_SWEEP_ORPHAN, at=AT, instance_id='inst_A') == {
        'code': 'orphan_evidence_hold', 'trigger': 'sweep_orphan', 'evidence': ['preserve_ref', 'reconcile_history'],
        'observed': {'merge_head': False, 'base_root_exists': True, 'head': B, 'worktree_clean': True},
        'review_state_at_hold': 'resolved_pending_review', 'at': AT, 'instance_id': 'inst_A'}
    prior = {'code': 'push_remote_unknown', 'local_sha': M}
    ctx = dict(RESUMABLE, last_error=prior, reconcile_attempt_count=3, reconcile_cycle_count=2)
    og.apply_resume(ctx, evidence=['merge_commit'], at=AT, instance_id='inst_A')
    assert ctx['review_state'] == 'reconciling' and ctx['reconciliation_kind'] == 'push_remote_unknown'
    assert ctx['reconcile_next_at'] == AT
    assert ctx['publish_commit'] == M and ctx['publish_lease'] == B
    assert ctx['last_error'] == {'code': 'orphan_resumed', 'prior_push_error': prior, 'at': AT, 'instance_id': 'inst_A'}
    assert ctx['recovery_resumed'] == {'from_review_state': None, 'evidence': ['merge_commit'], 'at': AT,
                                       'instance_id': 'inst_A'}
    assert ctx['reconcile_attempt_count'] == 3 and ctx['reconcile_cycle_count'] == 2
    keep = dict(RESUMABLE, publish_commit=H, publish_lease=X)
    og.apply_resume(keep, evidence=[], at=AT, instance_id='inst_A')
    assert (keep['publish_commit'], keep['publish_lease']) == (H, X) and 'last_error' not in keep


# ── instances / claims (real sqlite rows) ────────────────────────────────────

def register_instances(now=AT, dead=()):
    store = get_store()
    for iid in INSTANCES:
        store._execute('DELETE FROM server_instance WHERE instance_id = ?', [iid])
        gc.insert_instance(iid, OTHER_NODE, 4242, None, now)
        if iid in dead:
            gc.mark_instance_dead(iid)


def claim_row(mid):
    return db_claim.get(mid)


def plant_claim(mid, owner, *, claimed_at=AT, lease_until=None, purpose='reconcile', token='planted-token'):
    db_claim.ensure_row(mid)
    lease_until = lease_until or registry.add_seconds(claimed_at, session_claim.merge_session_claim_lease_sec())
    row = claim_row(mid)
    assert db_claim.take(mid, row.get('claim_token'), token=token, owner=owner, purpose=purpose,
                         claimed_at=claimed_at, lease_until=lease_until) == 1


@pytest.fixture
def clock(monkeypatch, project):
    """U2: the late-bound clock / instance id of git_service and one real merge session row."""
    import uuid
    from modules.flow_gate.db import groups
    state = {'now': AT, 'instance': 'inst_A'}
    monkeypatch.setattr(gs, 'now_iso', lambda: state['now'])
    monkeypatch.setattr(gs, 'current_instance_id', lambda: state['instance'])
    group = PROJECT + '.default.' + uuid.uuid4().hex[:12]
    groups.create({'group_id': group, 'project_id': PROJECT, 'module': 'default', 'title': 'claim'})
    gs.db_git.register_worktree(group, PROJECT, group.replace('.', '_'))
    state['mid'] = gs.db_git.create_session(group, [], finalize_action='merge', context={})
    register_instances()
    return state


def test_U2_acquire_busy_reentry_release_and_reacquire(clock):
    mid = clock['mid']
    claim = session_claim.acquire(mid, 'sweep')
    assert claim is not None and claim.owner == 'inst_A' and claim.depth == 1
    row = claim_row(mid)
    assert (row['claim_token'], row['owner_instance_id'], row['purpose']) == (claim.token, 'inst_A', 'sweep')
    assert row['claimed_at'] == AT and row['lease_until'] == registry.add_seconds(AT, 600)
    # re-entry: same thread, same instance → same claim, depth only
    again = session_claim.acquire(mid, 'reconcile')
    assert again is claim and claim.depth == 2 and claim_row(mid)['purpose'] == 'sweep'
    # another (ALIVE, lease valid) instance is busy, even in this thread
    clock['instance'] = 'inst_B'
    assert session_claim.acquire(mid, 'sweep') is None
    clock['instance'] = 'inst_A'
    session_claim.release(again, 'inner')
    assert claim_row(mid)['claim_token'] == claim.token          # inner level writes nothing
    session_claim.note(mid, 'hold')
    session_claim.release(claim)
    row = claim_row(mid)
    assert row['claim_token'] is None and row['owner_instance_id'] is None and row['lease_until'] is None
    assert (row['last_owner_instance_id'], row['last_purpose'], row['last_outcome'], row['last_released_at']) == (
        'inst_A', 'sweep', 'hold', AT)
    assert session_claim.current(mid) is None
    clock['instance'] = 'inst_B'
    other = session_claim.acquire(mid, 'startup')
    assert other is not None and other.token != claim.token
    session_claim.release(other, 'closed_orphan')
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B'


def test_U2_held_releases_on_exception_and_base_exception(clock):
    mid = clock['mid']
    with pytest.raises(RuntimeError):
        with session_claim.held(mid, 'sweep') as claim:
            assert claim is not None
            raise RuntimeError('boom')
    assert claim_row(mid)['claim_token'] is None and claim_row(mid)['last_outcome'] == 'error'
    with pytest.raises(ProcessDeath):
        with session_claim.held(mid, 'sweep'):
            raise ProcessDeath()
    assert claim_row(mid)['claim_token'] is None


def test_U2_renew_and_fence(clock):
    mid = clock['mid']
    claim = session_claim.acquire(mid, 'reconcile')
    assert session_claim.fence(claim)
    clock['now'] = registry.add_seconds(AT, 300)
    assert session_claim.renew(claim)
    assert claim_row(mid)['lease_until'] == registry.add_seconds(AT, 900) == claim.lease_until
    assert session_claim.check(mid, 'push')
    # past the own validity (lease - skew margin) without renewal → fence false
    clock['now'] = registry.add_seconds(claim.lease_until, -5)
    assert not session_claim.fence(claim)
    # another token in the row → renew and fence false
    clock['now'] = AT
    get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?', ['x', mid])
    assert not session_claim.renew(claim) and not session_claim.fence(claim)
    assert not session_claim.check(mid, 'push') and claim.lost
    assert session_claim.check(mid + 10_000, 'push') is False   # no claim at all














# ── U3 / U4: migration and static checks ─────────────────────────────────────

def _columns(dialect):
    text = (SERVER / 'sql' / 'migrations' / dialect / '181_git_merge_session_claim.sql').read_text(encoding='utf-8')
    body = text[text.index('CREATE TABLE IF NOT EXISTS git_merge_session_claim ('):]
    body = body[body.index('(') + 1:body.rindex(')')]
    cols = {}
    for line in body.splitlines():
        line = line.strip().rstrip(',')
        if not line or line.startswith(('--', 'CONSTRAINT', 'FOREIGN', 'PRIMARY KEY (')):
            continue
        name = line.split()[0]
        cols[name] = 'NOT NULL' in line.upper()
    return cols








# ── scenario fixture: recording lock, write/close/discard spies, instances ───

@pytest.fixture
def t4(env, monkeypatch):
    r = env.repo
    events, depth, emits, releases, lock_hooks = [], {}, [], [], []

    def lock(session, project_id, *, holder_kind, publish=False, mode='interactive'):
        mid = int(session['merge_id'])
        depth[mid] = depth.get(mid, 0) + 1
        events.append(('lock', mid, holder_kind, mode, gs.current_instance_id()))
        for hook in list(lock_hooks):      # what happens while the caller waits for / takes the lock
            hook(mid, holder_kind, mode)
        return ('recording', mid), [mid]

    def unlock(ctx, held):
        if held:
            mid = held[0]
            depth[mid] -= 1
            events.append(('unlock', mid, None, None, gs.current_instance_id()))

    for module in (branch_merge_publish, cleanup):
        monkeypatch.setattr(module, 'session_lock', lock)
        monkeypatch.setattr(module, 'session_unlock', unlock)

    def claim_live(mid):
        """The claim this thread holds is valid IN THE DB (same token, lease valid for its
        holder) — not merely "not flagged lost" in memory."""
        claim = session_claim.current(mid)
        if claim is None or claim.released:
            return False
        row = db_claim.get(mid)
        return bool(row and row.get('claim_token') == claim.token
                    and registry.valid_for_self(row.get('lease_until'), gs.now_iso()))

    def guarded(kind, mid):
        events.append((kind, mid, depth.get(mid, 0) > 0, claim_live(mid), gs.current_instance_id()))

    real_write = gs.db_git.set_session_context

    def write(mid, context):
        result = real_write(mid, context)   # the claim guard inside refuses a lost claim's write
        guarded('write', int(mid))
        return result
    monkeypatch.setattr(gs.db_git, 'set_session_context', write)

    real_close = gs._close_orphan

    def close(session, project_id):
        guarded('close', int(session['merge_id']))
        return real_close(session, project_id)
    monkeypatch.setattr(gs, '_close_orphan', close)

    real_discard = approval_intent.discard_intent

    def discard(mid, *, reason='merge_abort'):
        guarded('discard', int(mid))
        return real_discard(mid, reason=reason)
    monkeypatch.setattr(approval_intent, 'discard_intent', discard)

    real_attempt_close = gs.merge_target.close_session_attempt

    def attempt_close(session, state, *, error=None):
        guarded('close_session_attempt', int(session['merge_id']))
        return real_attempt_close(session, state, error=error)
    monkeypatch.setattr(gs.merge_target, 'close_session_attempt', attempt_close)

    real_invalidate = gs.db_git.invalidate_resolution_checkpoint_for_merge

    def invalidate(mid):
        guarded('invalidate_checkpoint', int(mid))
        return real_invalidate(mid)
    monkeypatch.setattr(gs.db_git, 'invalidate_resolution_checkpoint_for_merge', invalidate)

    real_release = db_claim.release

    def release(mid, token, **kw):
        releases.append(dict(mid=int(mid), **kw))
        return real_release(mid, token, **kw)
    monkeypatch.setattr(session_claim.db_claim, 'release', release)

    monkeypatch.setattr(gs.merge_target, 'session_merge_root', lambda row: (r.work, True))
    monkeypatch.setattr(gs, '_emit', lambda *a, **kw: emits.append(
        (a[0] if a else kw.get('event_type'), a[3] if len(a) > 3 else kw.get('payload'))))
    monkeypatch.setattr(gs, '_emit_pending_changed', lambda *a, **kw: None)
    register_instances()

    class T4:
        pass
    t = T4()
    t.events, t.emits, t.releases, t.env, t.r = events, emits, releases, env, r
    t.lock_hooks = lock_hooks

    def of(kind, mid=None, instance=None):
        return [e for e in events if e[0] == kind and (mid is None or e[1] == mid)
                and (instance is None or e[4] == instance)]
    t.of = of

    def clear():
        events.clear()
        emits.clear()
        releases.clear()
        env.commands.clear()
    t.clear = clear
    return t


def merge_aborts(env):
    return [c for c in env.commands if c[:2] == ['merge', '--abort']]


def assert_writes_guarded(t, mid, kinds=('write', 'close', 'discard', 'close_session_attempt')):
    for e in t.events:
        if e[0] in kinds and e[1] == mid:
            assert e[2] and e[3], f'unguarded {e}'


def assert_untouched_by_guard(t, mid, ctx):
    """§5 rows 'hold'/'claim busy'/'fence failed': nothing of the attempt is ended."""
    assert gs.db_git.get_session(mid)['status'] == 'open'
    assert approval_intent.intent_of_context(ctx) == INTENT
    assert approval_intent.DISCARDED_KEY not in ctx
    assert t.of('discard', mid) == [] and t.of('close_session_attempt', mid) == []
    assert t.of('invalidate_checkpoint', mid) == [] and t.of('close', mid) == []
    assert merge_aborts(t.env) == []


def to_conflict(t, mid):
    gs.db_git.set_status(t.env.group, 'conflict', merge_id=mid)


def legacy_incident_session(t):
    """S2: the shape the incident left behind (pre-T#3 reconcile reset): pending, no
    MERGE_HEAD, merge_commit None, one reconcile attempt, the preserve ref on disk and in
    the history, HEAD = the remote tip, the intent kept."""
    r = t.r
    mid, m, before = unknown_session(t.env)
    git(['reset', '-q', '--hard', r.B], r.work)
    ctx = saved(mid)
    ref = ctx.pop('preserve_ref')
    ctx.pop('preserve_sha', None)
    ctx.pop('reconciliation_kind', None)
    ctx.pop('reconcile_next_at', None)
    ctx.update(review_state=gs.REVIEW_STATE_PENDING, merge_commit=None, apply_phase=None,
               approval_attempt_id=None, reconcile_attempt_count=1,
               preserve_history=[{'ref': ref, 'sha': m, 'closed_reason': 'remote_unchanged', 'at': AT}])
    gs.db_git.set_session_context(mid, ctx)
    to_conflict(t, mid)
    _only(t.env, mid)
    assert ref_value(r.work, ref) == m and ref_value(r.work, 'MERGE_HEAD') is None
    assert git(['rev-parse', 'HEAD'], r.work) == origin_main(t.env) == r.B
    return mid, m, ref


def plain_orphan_session(t):
    """S4: an ordinary conflict session (no approval, no preservation, no reconcile) whose
    MERGE_HEAD is gone from the checkout."""
    r = t.r
    ctx = {'base_head': r.B, 'merge_head': r.F, approval_intent.INTENT_KEY: dict(INTENT)}
    mid = gs.db_git.create_session(t.env.group, ['f.txt'], finalize_action='merge', context=ctx)
    to_conflict(t, mid)
    _only(t.env, mid)
    assert ref_value(r.work, 'MERGE_HEAD') is None
    return mid


# ── S1 / S1u: two sweeps in a row on two instances ───────────────────────────





# ── S2 / S3 / S4: the guard on the sweep path ────────────────────────────────







# ── S5 / S6 / S7: concurrency ────────────────────────────────────────────────







# ── S8: TTL ──────────────────────────────────────────────────────────────────

def _age(mid):
    get_store()._execute('UPDATE git_merge_session SET created_at = ?, touched_at = ? WHERE merge_id = ?',
                         [OLD, OLD, mid])






# ── R1 / R2 / R3: process restart ────────────────────────────────────────────

def _die_mid_reconcile(t, monkeypatch):
    """inst_A starts a reconcile and the process dies inside the push; its claim row is
    left behind (the release is bypassed, like a killed process)."""
    env = t.env
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    monkeypatch.setattr(cleanup, '_finish_completed_review', lambda *a, **kw: None)
    real_release_row = session_claim._release_row
    dying = {'on': True}

    def release_row(claim, outcome):
        if dying['on']:
            return None
        return real_release_row(claim, outcome)
    monkeypatch.setattr(session_claim, '_release_row', release_row)

    def die(args):
        if args and args[0] == 'push':
            raise ProcessDeath()
        return None
    env.hooks.append(die)
    with pytest.raises(ProcessDeath):
        gs.reconcile_push_session(mid, trigger='server_startup')
    env.hooks.remove(die)
    dying['on'] = False
    session_claim._reset_thread_state()
    row = claim_row(mid)
    assert row['claim_token'] is not None and row['owner_instance_id'] == 'inst_A'
    assert row['purpose'] == 'reconcile'
    return mid, m, before








# ── review rej_01M6ZWFPF1E6JE2M: claim lost under the reconcile lock / during the push ──

def _take_over(mid, owner='inst_B', now=AT):
    """Another process takes the claim over (its token replaces this thread's in the DB)."""
    mine = session_claim.current(mid)
    assert mine is not None
    assert db_claim.take(mid, mine.token, token='b-token', owner=owner, purpose='reconcile',
                         claimed_at=now, lease_until=registry.add_seconds(now, 600)) == 1




PUSH_RESULTS = {
    'timeout': push_timeout,
    'auth': lambda a: subprocess.CompletedProcess(['git', *a], 128, '', 'fatal: Authentication failed'),
    'lease_rejected': push_rejected,
}




# ── review rej_01M6ZWFPF1E6JE2M: startup judges the row re-read under its lock ──



# ── review rej_01M6ZWFPF1E6JE2M: a failed read is not "no evidence" ──

READ_FAILURES = {
    'rc128': lambda a: subprocess.CompletedProcess(['git', *a], 128, '', 'fatal: unable to read refs'),
    'timeout': lambda a: subprocess.CompletedProcess(['git', *a], -1, '', 'timeout_expired'),
}


def _disk_only_evidence_session(t, *, merge_in_progress):
    """No evidence in the context; this merge's preservation ref on disk is the only one."""
    r = t.r
    if merge_in_progress:
        git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    ctx = {'base_head': r.B, 'merge_head': r.F, approval_intent.INTENT_KEY: dict(INTENT)}
    mid = gs.db_git.create_session(t.env.group, ['f.txt'], finalize_action='merge', context=ctx)
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B], r.work)
    to_conflict(t, mid)
    _only(t.env, mid)
    assert og.recovery_evidence(saved(mid), preserve_refs_on_disk=[]) == []
    return mid






# ── review rej_01M6ZWFPF1E6JE2M: the TTL is judged again on the row under the lock ──

def _hours_ago(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
