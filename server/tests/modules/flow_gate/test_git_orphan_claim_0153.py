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


@pytest.mark.parametrize('case', ['lease_expired', 'owner_dead', 'owner_alive'])
def test_U2_stale_reclaim(clock, case, caplog):
    mid = clock['mid']
    lease = registry.add_seconds(AT, -120) if case == 'lease_expired' else registry.add_seconds(AT, 600)
    plant_claim(mid, 'inst_A', claimed_at=registry.add_seconds(AT, -720), lease_until=lease)
    if case == 'owner_dead':
        gc.mark_instance_dead('inst_A')
    clock['instance'] = 'inst_B'
    caplog.set_level(logging.INFO, logger=CLAIM_LOG)
    claim = session_claim.acquire(mid, 'sweep')
    if case == 'owner_alive':
        assert claim is None
        assert claim_row(mid)['claim_token'] == 'planted-token'
        return
    assert claim is not None and claim.reclaimed_from == 'inst_A'
    row = claim_row(mid)
    assert row['owner_instance_id'] == 'inst_B' and row['claim_token'] == claim.token
    assert (row['last_owner_instance_id'], row['last_purpose'], row['last_outcome']) == (
        'inst_A', 'reconcile', 'reclaimed_from:inst_A')
    assert any('reclaimed' in r.getMessage() and 'inst_A' in r.getMessage() and 'planted-token' in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    session_claim.release(claim, 'hold')
    assert claim_row(mid)['last_outcome'] == 'hold/reclaimed_from:inst_A'


def test_U2_the_same_observed_token_wins_only_once(clock):
    mid = clock['mid']
    db_claim.ensure_row(mid)
    db_claim.ensure_row(mid)          # idempotent
    first = db_claim.take(mid, None, token='t1', owner='inst_A', purpose='sweep', claimed_at=AT, lease_until=LATER)
    second = db_claim.take(mid, None, token='t2', owner='inst_B', purpose='sweep', claimed_at=AT, lease_until=LATER)
    assert (first, second) == (1, 0) and claim_row(mid)['claim_token'] == 't1'
    third = db_claim.take(mid, 't1', token='t3', owner='inst_B', purpose='sweep', claimed_at=AT, lease_until=LATER)
    fourth = db_claim.take(mid, 't1', token='t4', owner='inst_C', purpose='sweep', claimed_at=AT, lease_until=LATER)
    assert (third, fourth) == (1, 0) and claim_row(mid)['claim_token'] == 't3'
    assert db_claim.release(mid, 't1', owner='inst_A', purpose='sweep', outcome='x', released_at=AT) == 0


def test_U2_no_instance_id_fails_closed(clock, monkeypatch):
    mid = clock['mid']
    clock['instance'] = None
    lazily = []
    monkeypatch.setattr(locks, '_register_lazily', lambda: lazily.append(1))
    assert session_claim.acquire(mid, 'sweep') is None
    assert lazily == [1]
    row = claim_row(mid)
    assert row is None or row['claim_token'] is None


def test_U2_a_lost_claim_refuses_every_session_write_and_audited_git_of_its_thread(clock):
    """Review rej_01M6ZWFPF1E6JE2M: the fence is not limited to the named call sites — every
    session-row write (context, close) and audited Git command of a session this thread
    processes under its claim is refused once the DB no longer carries its token."""
    mid = clock['mid']
    gs.db_git.set_session_context(mid, {'x': 1})               # no claim held: a plain write
    claim = session_claim.acquire(mid, 'reconcile')
    gs.db_git.set_session_context(mid, {'x': 2})               # valid claim: renewed, fenced, written
    assert saved(mid) == {'x': 2}
    get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?', ['other', mid])
    with pytest.raises(session_claim.ClaimLost) as lost:
        gs.db_git.set_session_context(mid, {'x': 3})
    assert lost.value.merge_id == mid and lost.value.step == 'session_set_context' and claim.lost
    with pytest.raises(session_claim.ClaimLost):
        gs.db_git.close_session(mid, 'aborted')
    with pytest.raises(session_claim.ClaimLost) as git_lost:
        gs._run_session_git(mid, {}, 'update-ref', 'probe', ['--version'])
    assert git_lost.value.step == 'git_probe'
    assert saved(mid) == {'x': 2} and gs.db_git.get_session(mid)['status'] == 'open'
    assert issubclass(session_claim.ClaimLost, BaseException)
    assert not issubclass(session_claim.ClaimLost, Exception)    # no `except Exception` swallows it
    session_claim.release(claim)
    assert claim_row(mid)['claim_token'] == 'other'             # the release never clobbers it
    gs.db_git.set_session_context(mid, {'x': 4})               # no claim held any more
    assert saved(mid) == {'x': 4}


def test_U2_held_ends_a_lost_claim_quietly_and_only_for_its_own_merge(clock):
    mid = clock['mid']
    with session_claim.held(mid, 'sweep') as claim:
        get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?',
                             ['other', mid])
        gs.db_git.set_session_context(mid, {'never': True})
    assert claim.outcome == 'claim_lost' and 'never' not in saved(mid)
    assert claim_row(mid)['claim_token'] == 'other' and session_claim.current(mid) is None
    with pytest.raises(session_claim.ClaimLost):
        with session_claim.held(mid, 'sweep') as busy:           # 'other' (inst_A, lease valid): busy
            assert busy is None
            raise session_claim.ClaimLost(mid + 1, 'elsewhere')


def test_U2_lease_parameter_is_clamped(monkeypatch):
    monkeypatch.delenv(session_claim.LEASE_ENV, raising=False)
    assert session_claim.merge_session_claim_lease_sec() == 600
    for raw, expected in (('50', 120), ('99999', 3600), ('900', 900), ('junk', 600)):
        monkeypatch.setenv(session_claim.LEASE_ENV, raw)
        assert session_claim.merge_session_claim_lease_sec() == expected


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


def test_U3_migration_181_applies_and_the_three_dialects_agree(tmp_db):
    mock_db, _ = tmp_db
    rows = mock_db._conn.execute('PRAGMA table_info(git_merge_session_claim)').fetchall()
    assert [r[1] for r in rows] == list(db_claim.COLUMNS)
    sqlite_cols = _columns('sqlite')
    assert list(sqlite_cols) == list(db_claim.COLUMNS)
    assert _columns('mysql') == sqlite_cols == _columns('postgres')
    assert [name for name, not_null in sqlite_cols.items() if not_null] == ['merge_id']
    for dialect in ('sqlite', 'mysql', 'postgres'):
        text = (SERVER / 'sql' / 'migrations' / dialect / '181_git_merge_session_claim.sql').read_text(encoding='utf-8')
        assert 'Rollback = DROP TABLE git_merge_session_claim' in text
        assert 'ON DELETE CASCADE' in text and 'REFERENCES git_merge_session(merge_id)' in text
        assert 'server_instance' not in text.split('CREATE TABLE')[1]   # no FK to instances (158 precedent)
        for line in text.splitlines():               # naive ';' splitters must not cut a comment
            if line.lstrip().startswith('--'):
                assert ';' not in line, (dialect, line)


def test_U4_close_orphan_is_called_only_by_judge_orphan_and_both_entry_points_claim():
    source = Path(cleanup.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    callers = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', None)
                    if name == '_close_orphan':
                        callers.append(fn.name)
    assert callers == ['_judge_orphan']
    for name in ('merge_session_sweep', 'startup_recovery'):
        assert 'session_claim.held(' in inspect.getsource(getattr(cleanup, name)), name
    assert 'session_claim.acquire(' in inspect.getsource(gs.reconcile_push_session)
    # review rej_01M6ZWFPF1E6JE2M: the reconcile fences before its first change under the lock,
    # audited Git is fenced before and after the command, startup judges under its lock
    assert '"reconcile_lock"' in inspect.getsource(gs.reconcile_push_session)
    assert inspect.getsource(gs._run_session_git).count('_session_claim_require(') == 2
    assert 'session_lock(' in inspect.getsource(cleanup._startup_group_session)


def test_U4_new_sources_have_no_forbidden_operations():
    texts = {
        'orphan_guard': inspect.getsource(og),
        '_judge_orphan': inspect.getsource(cleanup._judge_orphan),
        '_orphan_facts': inspect.getsource(cleanup._orphan_facts),
        '_observe_merge_head': inspect.getsource(cleanup._observe_merge_head),
        '_record_hold': inspect.getsource(cleanup._record_hold),
    }
    for name, text in texts.items():
        for forbidden in ('merge", "--abort', 'discard_intent', 'invalidate_resolution_checkpoint_for_merge',
                          '"reset"', 'update-ref', '"push"', '"waiting"', 'ORIG_HEAD'):
            assert forbidden not in text, (name, forbidden)
    facts = inspect.getsource(cleanup._orphan_facts) + inspect.getsource(cleanup._observe_merge_head)
    for read in ('"status", "--porcelain", "--untracked-files=no"', '"for-each-ref"', 'verify_preserved',
                 '_merge_in_progress', '_rev_parse'):
        assert read in facts


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

@pytest.mark.parametrize('variant', ['old_snapshot', 'own_list', 'due', 'incident_snapshot'])
def test_S1_two_sweeps_in_a_row_never_close_a_reconciling_session(t4, variant):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    snapshot = gs.db_git.get_session(mid)          # read BEFORE inst_A's pass
    env.hooks.append(on('push', push_timeout))      # every re-push times out again
    t.clear()
    gs.merge_session_sweep()                         # inst_A: reconcile → repush timeout
    after_a = saved(mid)
    assert after_a['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after_a['reconciliation_kind'] == 'push_remote_unknown'
    assert after_a['last_error']['stage'] == 'reconcile_repush' and after_a['last_error']['instance_id'] == 'inst_A'
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_A'
    assert t.of('discard') == [] and t.of('close') == [] and t.of('close_session_attempt') == []
    assert t.of('invalidate_checkpoint') == [] and merge_aborts(env) == []
    assert_writes_guarded(t, mid)
    if variant == 'own_list':
        after_a['reconcile_next_at'] = FUTURE      # backing off: B's reconcile is not due
        gs.db_git.set_session_context(mid, after_a)
    elif variant == 'due':
        after_a['reconcile_next_at'] = PAST        # pulled due: B reconciles again
        gs.db_git.set_session_context(mid, after_a)
    env.clock['instance'] = 'inst_B'
    t.clear()
    if variant == 'old_snapshot':
        gs.merge_session_sweep(sessions=[snapshot])
    elif variant == 'incident_snapshot':
        stale = dict(snapshot)
        stale_ctx = dict(gs.db_git.session_context(snapshot), review_state=gs.REVIEW_STATE_PENDING)
        stale['context'] = stale_ctx
        gs.merge_session_sweep(sessions=[stale])
    else:
        gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)
    assert gs.db_git.get_state(env.group)['status'] != 'waiting'
    assert after['merge_commit'] == m and preserve_refs(r.work)[f'refs/flowgate/preserve/{mid}'] == m
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B' and claim_row(mid)['claim_token'] is None
    if variant == 'own_list':
        assert env.commands == [] and t.of('write') == []
    else:
        assert len(pushes(env)) == 1 and after['last_error']['instance_id'] == 'inst_B'
        assert after['reconcile_attempt_count'] == before.get('reconcile_attempt_count', 0) + 2
    assert_lease_only(env)
    assert_writes_guarded(t, mid)
    assert_consistent(mid, r.work)


def test_S1u_user_intervention_then_another_instance_sweeps_without_git(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    env.hooks.append(on('ls-remote', lambda a: subprocess.CompletedProcess(
        ['git', *a], 128, '', 'fatal: Authentication failed')))
    gs.merge_session_sweep()                         # inst_A ends at the auth intervention
    env.hooks.clear()
    parked = saved(mid)
    assert parked['reconciliation_kind'] == 'reconcile_auth_required'
    env.clock['instance'] = 'inst_B'
    t.clear()
    gs.merge_session_sweep()
    assert env.commands == []
    assert saved(mid) == parked
    assert_untouched_by_guard(t, mid, parked)
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B'


# ── S2 / S3 / S4: the guard on the sweep path ────────────────────────────────

def test_S2_incident_shape_is_held_once_and_left_alone_afterwards(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, ref = legacy_incident_session(t)
    t.clear()
    gs.merge_session_sweep()                         # inst_A
    held = saved(mid)
    hold = held['recovery_hold']
    assert hold == {
        'code': 'orphan_evidence_hold', 'trigger': 'sweep_orphan',
        'evidence': ['preserve_ref', 'preserve_ref_on_disk', 'reconcile_history'],
        'observed': {'merge_head': False, 'base_root_exists': True, 'head': r.B, 'worktree_clean': True},
        'review_state_at_hold': gs.REVIEW_STATE_PENDING, 'at': AT, 'instance_id': 'inst_A',
        'resume_missing': ['merge_commit_and_expected_remote_head', 'preserve_verified', 'head_is_publish_commit'],
    }
    assert held['review_state'] == gs.REVIEW_STATE_PENDING and held['merge_commit'] is None
    assert_untouched_by_guard(t, mid, held)
    assert [k for k, _p in t.emits] == ['git_merge_recovery_hold']
    assert len(t.of('write', mid)) == 1
    assert_writes_guarded(t, mid)
    locks_taken = [e for e in t.events if e[0] == 'lock']
    assert [(e[2], e[3]) for e in locks_taken] == [('sweep', locks.NO_WAIT)]
    order = [e[0] for e in t.events if e[0] in ('lock', 'write', 'unlock')]
    assert order == ['lock', 'write', 'unlock']
    for cmd in env.commands:                        # the guard's Git is read-only
        assert cmd[0] in ('rev-parse', 'status', 'for-each-ref', 'cat-file', 'rev-list'), cmd
    assert claim_row(mid)['last_outcome'] == 'hold'
    env.clock.update(now=LATER, instance='inst_B')
    t.clear()
    gs.merge_session_sweep()
    gs.merge_session_sweep()
    assert saved(mid) == held                        # not one character changed
    assert t.of('write', mid) == [] and env.commands == []
    assert ref_value(r.work, ref) == m and git(['rev-parse', 'HEAD'], r.work) == r.B
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B' and claim_row(mid)['last_outcome'] == 'hold_kept'


def test_S3_lost_state_write_resumes_into_the_t3_reconcile_and_completes(t4, common):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    ctx = saved(mid)
    ctx['review_state'] = None                       # the state write was lost
    ctx.pop('reconciliation_kind', None)
    ctx.pop('reconcile_next_at', None)
    gs.db_git.set_session_context(mid, ctx)
    to_conflict(t, mid)
    _only(env, mid)
    assert ref_value(r.work, 'MERGE_HEAD') is None and git(['rev-parse', 'HEAD'], r.work) == m
    assert origin_main(env) == r.B == ctx['expected_remote_head']
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and origin_main(env) == m
    assert after['recovery_resumed'] == {
        'from_review_state': None,
        'evidence': ['merge_commit', 'preserve_ref', 'preserve_ref_on_disk'],
        'at': AT, 'instance_id': 'inst_A'}
    assert after['last_error']['code'] == 'orphan_resumed'
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{r.B}', 'origin', f'{m}:refs/heads/main']]
    assert_lease_only(env)
    assert common['approvals'] == [(env.group, mid, INTENT)]   # consumed by the common completer
    assert t.of('discard', mid) == [] and t.of('close', mid) == [] and merge_aborts(env) == []
    assert 'recovery_hold' not in after
    # the resume write and the reconcile both ran inside a lock and the (re-entered) claim
    assert_writes_guarded(t, mid, kinds=('write', 'close', 'discard'))
    lock_kinds = [e[2] for e in t.events if e[0] == 'lock']
    assert lock_kinds == ['sweep', 'review_action']
    resume_write = next(i for i, e in enumerate(t.events) if e[0] == 'write')
    first_unlock = next(i for i, e in enumerate(t.events) if e[0] == 'unlock')
    assert resume_write < first_unlock
    assert claim_row(mid)['last_outcome'] == 'reconcile:merged'
    assert len(t.releases) == 1                      # one claim (sweep), re-entered by reconcile
    assert_consistent(mid, r.work, r.bare)


def test_S4_orphan_without_evidence_is_closed_as_before_inside_lock_and_claim(t4):
    t, env, r = t4, t4.env, t4.r
    mid = plain_orphan_session(t)
    t.clear()
    gs.merge_session_sweep()
    row = gs.db_git.get_session(mid)
    ctx = saved(mid)
    assert row['status'] == 'aborted'
    assert approval_intent.intent_of_context(ctx) is None
    assert ctx[approval_intent.DISCARDED_KEY]['reason'] == 'orphan_recovered'
    assert gs.db_git.get_state(env.group)['status'] == 'waiting'
    assert [k for k, _p in t.emits] == ['git_merge_auto_aborted']
    assert t.emits[0][1]['reason'] == 'orphan_recovered'
    assert len(t.of('close', mid)) == 1 and len(t.of('discard', mid)) == 1
    assert len(t.of('invalidate_checkpoint', mid)) == 1 and len(t.of('close_session_attempt', mid)) == 1
    assert_writes_guarded(t, mid, kinds=('close', 'discard', 'close_session_attempt', 'invalidate_checkpoint'))
    assert claim_row(mid)['last_outcome'] == 'closed_orphan'
    assert merge_aborts(env) == []


# ── S5 / S6 / S7: concurrency ────────────────────────────────────────────────

def test_S5_a_sweep_on_another_instance_during_a_reconcile_push_is_skipped(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    seen = {}

    def other_instance_sweeps(args):
        if args and args[0] == 'push' and 'b' not in seen:
            env.clock['instance'] = 'inst_B'
            commands_before, writes_before = len(env.commands), len(t.of('write'))
            ctx_before = saved(mid)
            gs.merge_session_sweep()
            seen['b'] = dict(commands=env.commands[commands_before:], writes=t.of('write')[writes_before:],
                             same=saved(mid) == ctx_before, row=claim_row(mid))
            env.clock['instance'] = 'inst_A'
        return None
    env.hooks.append(other_instance_sweeps)
    t.clear()
    gs.merge_session_sweep()                          # inst_A
    assert seen['b']['commands'] == [] and seen['b']['writes'] == [] and seen['b']['same']
    assert seen['b']['row']['owner_instance_id'] == 'inst_A'
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and origin_main(env) == m
    row = claim_row(mid)
    assert row['claim_token'] is None and row['last_owner_instance_id'] == 'inst_A'
    assert [rel['owner'] for rel in t.releases] == ['inst_A']
    assert_writes_guarded(t, mid)


@pytest.mark.parametrize('case', ['alive', 'dead', 'expired'])
def test_S6_stale_claims(t4, case, caplog):
    t, env, r = t4, t4.env, t4.r
    mid, m, ref = legacy_incident_session(t)
    if case == 'expired':
        plant_claim(mid, 'inst_A', claimed_at=registry.add_seconds(AT, -900),
                    lease_until=registry.add_seconds(AT, -300))
    else:
        plant_claim(mid, 'inst_A')
    if case == 'dead':
        gc.mark_instance_dead('inst_A')
    before = saved(mid)
    env.clock['instance'] = 'inst_B'
    caplog.set_level(logging.INFO, logger=CLAIM_LOG)
    t.clear()
    gs.merge_session_sweep()
    if case == 'alive':
        assert saved(mid) == before and env.commands == [] and t.of('write') == []
        assert claim_row(mid)['claim_token'] == 'planted-token'
        assert any('busy' in rec.getMessage() for rec in caplog.records)
        return
    after = saved(mid)
    assert after['recovery_hold']['instance_id'] == 'inst_B'
    assert_untouched_by_guard(t, mid, after)
    row = claim_row(mid)
    assert row['claim_token'] is None and row['last_owner_instance_id'] == 'inst_B'
    assert row['last_outcome'] == 'hold/reclaimed_from:inst_A'
    assert any(rec.levelno == logging.WARNING and 'reclaimed' in rec.getMessage() and 'inst_A' in rec.getMessage()
               for rec in caplog.records)


@pytest.mark.parametrize('shape', ['hold', 'close'])
def test_S7_fence_lost_before_the_write_leaves_only_the_other_instance_result(t4, shape, monkeypatch):
    t, env, r = t4, t4.env, t4.r
    if shape == 'hold':
        mid, m, ref = legacy_incident_session(t)
    else:
        mid = plain_orphan_session(t)
    real_renew = session_claim.renew
    fired = {}

    def renew(claim):
        if claim.owner == 'inst_A' and 'b' not in fired:
            fired['b'] = True
            # the lease expires and inst_B reclaims the claim and processes the session
            env.clock.update(now=registry.add_seconds(AT, 700), instance='inst_B')
            gs.merge_session_sweep()
            fired['b_result'] = saved(mid)
            env.clock['instance'] = 'inst_A'
        return real_renew(claim)
    monkeypatch.setattr(session_claim, 'renew', renew)
    t.clear()
    gs.merge_session_sweep()                         # inst_A decides, then its fence fails
    assert fired.get('b')
    assert t.of('write', instance='inst_A') == [] and t.of('close', instance='inst_A') == []
    assert t.of('discard', instance='inst_A') == []
    after = saved(mid)
    assert after == fired['b_result']                 # only inst_B's result is left
    if shape == 'hold':
        assert after['recovery_hold']['instance_id'] == 'inst_B'
        assert_untouched_by_guard(t, mid, after)
    else:
        assert gs.db_git.get_session(mid)['status'] == 'aborted'
        assert len(t.of('close', instance='inst_B')) == 1 and len(t.of('discard', instance='inst_B')) == 1
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_B'


# ── S8: TTL ──────────────────────────────────────────────────────────────────

def _age(mid):
    get_store()._execute('UPDATE git_merge_session SET created_at = ?, touched_at = ? WHERE merge_id = ?',
                         [OLD, OLD, mid])


def test_S8_ttl_of_a_hand_back_resolver_session_holds_without_merge_abort(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, b2, before = conflict_session(env)
    remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'returned_to_resolver'   # T#3 D1 hand-back
    to_conflict(t, mid)
    _only(env, mid)
    _age(mid)
    handed = saved(mid)
    head = git(['rev-parse', 'HEAD'], r.work)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert merge_aborts(env) == []
    hold = after['recovery_hold']
    assert hold['trigger'] == 'sweep_ttl' and hold['instance_id'] == 'inst_A'
    assert hold['evidence'] == ['preserve_ref', 'preserve_ref_on_disk', 'reconcile_history', 'reconcile_handoff']
    assert hold['observed']['merge_head'] is True and hold['review_state_at_hold'] is None
    assert {k: v for k, v in after.items() if k != 'recovery_hold'} == handed
    assert_untouched_by_guard(t, mid, after)
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == head
    assert unmerged(r.work) == ['y.txt']
    assert_writes_guarded(t, mid)
    t.clear()
    gs.merge_session_sweep()                          # held: no TTL judgement any more
    assert saved(mid) == after and merge_aborts(env) == [] and t.of('write') == []


def test_S8_control_ttl_without_evidence_keeps_the_old_abort(t4):
    t, env, r = t4, t4.env, t4.r
    git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    assert ref_value(r.work, 'MERGE_HEAD') == r.F
    ctx = {'base_head': r.B, 'merge_head': r.F, approval_intent.INTENT_KEY: dict(INTENT)}
    mid = gs.db_git.create_session(env.group, ['f.txt'], finalize_action='merge', context=ctx)
    to_conflict(t, mid)
    _only(env, mid)
    _age(mid)
    t.clear()
    gs.merge_session_sweep()
    assert len(merge_aborts(env)) == 1 and ref_value(r.work, 'MERGE_HEAD') is None
    assert gs.db_git.get_session(mid)['status'] == 'aborted'
    after = saved(mid)
    assert approval_intent.intent_of_context(after) is None
    assert after[approval_intent.DISCARDED_KEY]['reason'] == 'ttl_expired'
    assert gs.db_git.get_state(env.group)['status'] == 'waiting'
    assert 'recovery_hold' not in after
    assert_writes_guarded(t, mid, kinds=('discard', 'close_session_attempt'))
    assert claim_row(mid)['last_outcome'] == 'aborted'


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


def test_R1_dead_instance_claim_is_reclaimed_at_startup_and_the_reconcile_proceeds(t4, monkeypatch, caplog):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = _die_mid_reconcile(t, monkeypatch)
    gc.mark_instance_dead('inst_A')                   # = mark_prior_same_node_dead() on its node
    assert registry.instance_state('inst_A', AT) == registry.DEAD
    env.clock['instance'] = 'inst_C'
    caplog.set_level(logging.INFO, logger=CLAIM_LOG)
    t.clear()
    gs.startup_recovery()
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and origin_main(env) == m
    assert gs.db_git.get_session(mid)['status'] == 'open'   # the attempt close is recorded, not run
    assert approval_intent.DISCARDED_KEY not in after
    assert t.of('close', mid) == [] and t.of('discard', mid) == [] and t.of('close_session_attempt', mid) == []
    assert after['ref_operations'][-1]['instance_id'] == 'inst_C'
    outcomes = [rel['outcome'] for rel in t.releases]
    assert outcomes[0] == 'released/reclaimed_from:inst_A'       # the startup claim took it over
    assert all(rel['owner'] == 'inst_C' for rel in t.releases)
    assert claim_row(mid)['claim_token'] is None and claim_row(mid)['last_owner_instance_id'] == 'inst_C'
    assert any(rec.levelno == logging.WARNING and 'reclaimed' in rec.getMessage() and 'inst_A' in rec.getMessage()
               for rec in caplog.records)
    assert_writes_guarded(t, mid)
    assert_consistent(mid, r.work, r.bare)


def test_R2_live_instance_claim_is_left_alone_at_startup_until_its_lease_expires(t4, monkeypatch):
    t, env, r = t4, t4.env, t4.r
    mid, m, before = _die_mid_reconcile(t, monkeypatch)
    parked = saved(mid)
    env.clock['instance'] = 'inst_C'
    t.clear()
    gs.startup_recovery()
    assert env.commands == [] and t.of('write') == [] and saved(mid) == parked
    assert claim_row(mid)['owner_instance_id'] == 'inst_A'
    gs.merge_session_sweep()                          # still inside inst_A's lease
    assert env.commands == [] and saved(mid) == parked
    env.clock['now'] = registry.add_seconds(AT, 700)  # lease (600 s) + skew margin passed
    gs.merge_session_sweep()
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and origin_main(env) == m
    assert t.releases[-1]['owner'] == 'inst_C'
    assert t.releases[-1]['outcome'].endswith('/reclaimed_from:inst_A')
    assert t.of('close', mid) == [] and t.of('discard', mid) == []
    assert_consistent(mid, r.work, r.bare)


def test_R3_startup_holds_the_incident_shape_and_a_later_sweep_changes_nothing(t4):
    t, env, r = t4, t4.env, t4.r
    mid, m, ref = legacy_incident_session(t)
    env.clock['instance'] = 'inst_C'
    t.clear()
    gs.startup_recovery()
    held = saved(mid)
    assert held['recovery_hold']['trigger'] == 'startup_orphan'
    assert held['recovery_hold']['instance_id'] == 'inst_C'
    assert_untouched_by_guard(t, mid, held)
    assert len(t.of('write', mid)) == 1
    assert_writes_guarded(t, mid)
    env.clock['instance'] = 'inst_B'
    t.clear()
    gs.merge_session_sweep()
    assert saved(mid) == held and t.of('write') == [] and env.commands == []
    assert gs.db_git.get_session(mid)['status'] == 'open'


# ── review rej_01M6ZWFPF1E6JE2M: claim lost under the reconcile lock / during the push ──

def _take_over(mid, owner='inst_B', now=AT):
    """Another process takes the claim over (its token replaces this thread's in the DB)."""
    mine = session_claim.current(mid)
    assert mine is not None
    assert db_claim.take(mid, mine.token, token='b-token', owner=owner, purpose='reconcile',
                         claimed_at=now, lease_until=registry.add_seconds(now, 600)) == 1


def test_S9_claim_lost_while_waiting_for_the_reconcile_lock_writes_nothing(t4):
    """The reconcile takes its claim before the lock; the lease runs out while it waits and
    inst_B takes the claim over. Under the lock nothing is changed: no cycle counter, no
    preservation gate, no remote contact."""
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    ctx_before = saved(mid)
    refs_before = preserve_refs(r.work)

    def reclaimed_while_waiting(lock_mid, holder_kind, mode):
        if lock_mid == mid and holder_kind == 'review_action':
            _take_over(mid)
    t.lock_hooks.append(reclaimed_while_waiting)
    t.clear()
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out == {'ok': True, 'result': {'status': 'claim_lost', 'stage': 'reconcile_lock'}}
    assert saved(mid) == ctx_before
    assert env.commands == [] and env.probes == [] and t.of('write') == []
    assert preserve_refs(r.work) == refs_before and git(['rev-parse', 'HEAD'], r.work) == m
    row = claim_row(mid)                                  # inst_B's claim stands in the DB
    assert (row['claim_token'], row['owner_instance_id']) == ('b-token', 'inst_B')
    assert [e[2] for e in t.of('lock')] == ['review_action'] and len(t.of('unlock')) == 1


PUSH_RESULTS = {
    'timeout': push_timeout,
    'auth': lambda a: subprocess.CompletedProcess(['git', *a], 128, '', 'fatal: Authentication failed'),
    'lease_rejected': push_rejected,
}


@pytest.mark.parametrize('result', sorted(PUSH_RESULTS))
def test_S10_claim_lost_during_the_push_leaves_no_write_after_it(t4, result):
    """The push outlives the lease and inst_B takes the claim over; the push then returns a
    timeout / an auth refusal / a lease rejection. Nothing of inst_A is written after the
    push: no audit entry, no last_error, no retry schedule, no user intervention."""
    t, env, r = t4, t4.env, t4.r
    mid, m, before = unknown_session(env)
    to_conflict(t, mid)
    _only(env, mid)
    at_push = {}

    def taken_over_during_push(args):
        if args and args[0] == 'push' and not at_push:
            at_push['ctx'] = saved(mid)
            later = registry.add_seconds(AT, 700)
            env.clock['now'] = later                      # inst_A's lease (600 s) ran out
            _take_over(mid, now=later)
            return PUSH_RESULTS[result](args)
        return None
    env.hooks.append(taken_over_during_push)
    t.clear()
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out == {'ok': True, 'result': {'status': 'claim_lost', 'stage': 'after_reconcile_repush'}}
    assert len(pushes(env)) == 1
    after = saved(mid)
    assert after == at_push['ctx']
    assert after['reconciliation_kind'] == 'push_remote_unknown' and after['last_error'] == before['last_error']
    assert after.get('ref_operations') == at_push['ctx'].get('ref_operations')
    row = claim_row(mid)
    assert (row['claim_token'], row['owner_instance_id']) == ('b-token', 'inst_B')
    assert_writes_guarded(t, mid)                         # every landed write was under a live claim
    assert git(['rev-parse', 'HEAD'], r.work) == m and ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m


# ── review rej_01M6ZWFPF1E6JE2M: startup judges the row re-read under its lock ──

@pytest.mark.parametrize('change', ['completed', 'aborted', 'user_intervention'])
def test_R4_startup_judges_the_row_re_read_under_its_lock_not_the_listed_snapshot(t4, monkeypatch, change):
    """Between startup's list read and its claim/lock, the session is completed by another
    instance, aborted by a person, or parked for a person. Startup decides on the row it
    re-reads under its lock: nothing is re-affirmed, closed, held or written."""
    t, env, r = t4, t4.env, t4.r
    if change == 'user_intervention':
        mid, m, _ref = legacy_incident_session(t)
    else:
        mid, m, before = unknown_session(env)
        to_conflict(t, mid)
        _only(env, mid)
    real_list = gs.db_git.list_open_sessions
    settled = {}

    def listed():
        rows = real_list()
        ctx = saved(mid)
        if change == 'completed':
            ctx.update(review_state=gs.REVIEW_STATE_COMPLETED, apply_phase='completed', published_commit=m)
            ctx.pop('reconciliation_kind', None)
            ctx.pop('reconcile_next_at', None)
            gs.db_git.set_session_context(mid, ctx)
            gs.db_git.close_session(mid, 'done')
            gs.db_git.set_status(env.group, 'merged', merge_id=mid)
        elif change == 'aborted':
            ctx['review_state'] = None
            ctx.pop('reconciliation_kind', None)
            ctx.pop('reconcile_next_at', None)
            gs.db_git.set_session_context(mid, ctx)
            gs.db_git.close_session(mid, 'aborted')
            gs.db_git.set_status(env.group, 'waiting')
        else:
            ctx.update(review_state=gs.REVIEW_STATE_RECONCILING, reconciliation_kind='reconcile_auth_required',
                       reconcile_next_at=None,
                       last_error={'code': 'reconcile_auth_rejected', 'stage': 'push', 'instance_id': 'inst_B'})
            gs.db_git.set_session_context(mid, ctx)
        settled.update(ctx=saved(mid), group=gs.db_git.get_state(env.group)['status'],
                       status=gs.db_git.get_session(mid)['status'], n=len(t.events))
        env.commands.clear()
        return rows
    monkeypatch.setattr(gs.db_git, 'list_open_sessions', listed)
    env.clock['instance'] = 'inst_C'
    t.clear()
    gs.startup_recovery()
    assert settled, 'startup read its list'
    assert saved(mid) == settled['ctx']
    assert gs.db_git.get_state(env.group)['status'] == settled['group']
    assert gs.db_git.get_session(mid)['status'] == settled['status']
    assert env.commands == []
    later = t.events[settled['n']:]
    assert [e for e in later if e[0] in ('write', 'close', 'discard', 'close_session_attempt',
                                         'invalidate_checkpoint') and e[1] == mid] == []
    assert [k for k, _p in t.emits] == []
    locks_taken = [e for e in later if e[0] == 'lock' and e[1] == mid]
    assert locks_taken and locks_taken[0][2:5] == ('sweep', locks.NO_WAIT, 'inst_C')   # startup's own lock
    assert len(locks_taken) == len([e for e in later if e[0] == 'unlock' and e[1] == mid])
    assert claim_row(mid)['last_owner_instance_id'] == 'inst_C' and claim_row(mid)['claim_token'] is None
    if change != 'user_intervention':
        assert claim_row(mid)['last_outcome'] == 'skipped'


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


@pytest.mark.parametrize('failure', [None, 'rc128', 'timeout'])
def test_S11_an_unreadable_preserve_ref_listing_never_closes_the_session(t4, failure):
    t, env, r = t4, t4.env, t4.r
    mid = _disk_only_evidence_session(t, merge_in_progress=False)
    if failure:
        env.hooks.append(on('for-each-ref', READ_FAILURES[failure]))
    before = saved(mid)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)              # open, intent kept, no close/discard/abort
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == r.B
    if failure is None:                                   # control: the disk ref alone is evidence
        assert after['recovery_hold']['evidence'] == ['preserve_ref_on_disk']
        return
    assert after == before and t.of('write', mid) == []
    assert claim_row(mid)['last_outcome'] == 'unobserved'


@pytest.mark.parametrize('failure', [None, 'rc128', 'timeout'])
def test_S11_ttl_with_an_unreadable_preserve_ref_listing_aborts_nothing(t4, failure):
    t, env, r = t4, t4.env, t4.r
    mid = _disk_only_evidence_session(t, merge_in_progress=True)
    _age(mid)
    if failure:
        env.hooks.append(on('for-each-ref', READ_FAILURES[failure]))
    before = saved(mid)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    assert_untouched_by_guard(t, mid, after)
    assert ref_value(r.work, 'MERGE_HEAD') == r.F
    if failure is None:
        assert after['recovery_hold']['trigger'] == 'sweep_ttl'
        assert after['recovery_hold']['evidence'] == ['preserve_ref_on_disk']
        return
    assert after == before and t.of('write', mid) == []
    assert claim_row(mid)['last_outcome'] == 'unobserved'


# ── review rej_01M6ZWFPF1E6JE2M: the TTL is judged again on the row under the lock ──

def _hours_ago(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@pytest.mark.parametrize('case', ['activity', 'review_activity', 'review_cap'])
def test_S12_activity_between_the_sweep_read_and_the_ttl_lock_is_honoured(t4, case):
    t, env, r = t4, t4.env, t4.r
    git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    ctx = {'base_head': r.B, 'merge_head': r.F, approval_intent.INTENT_KEY: dict(INTENT)}
    if case != 'activity':
        ctx['review_state'] = gs.REVIEW_STATE_PENDING
    mid = gs.db_git.create_session(env.group, ['f.txt'], finalize_action='merge', context=ctx)
    to_conflict(t, mid)
    _only(env, mid)
    created, touched = {'activity': (OLD, OLD), 'review_activity': (_hours_ago(30), _hours_ago(25)),
                        'review_cap': (_hours_ago(cleanup.REVIEW_PENDING_MAX_HOURS + 8), _hours_ago(25))}[case]
    get_store()._execute('UPDATE git_merge_session SET created_at = ?, touched_at = ? WHERE merge_id = ?',
                         [created, touched, mid])
    assert cleanup._session_ttl_expired(gs.db_git.get_session(mid), ctx.get('review_state'))   # the sweep's read
    before = saved(mid)

    def person_opens_it(lock_mid, holder_kind, mode):
        if lock_mid == mid and holder_kind == 'sweep':
            gs.db_git.touch_session(mid)                  # the conflict list / review is opened meanwhile
    t.lock_hooks.append(person_opens_it)
    t.clear()
    gs.merge_session_sweep()
    after = saved(mid)
    if case == 'review_cap':
        # activity cannot lift the review wait's cap from creation: the old abort still runs
        assert len(merge_aborts(env)) == 1 and gs.db_git.get_session(mid)['status'] == 'aborted'
        assert after[approval_intent.DISCARDED_KEY]['reason'] == 'ttl_expired'
        assert claim_row(mid)['last_outcome'] == 'aborted'
        return
    assert merge_aborts(env) == [] and ref_value(r.work, 'MERGE_HEAD') == r.F
    assert_untouched_by_guard(t, mid, after)
    assert after == before and t.of('write', mid) == [] and 'recovery_hold' not in after
    assert gs.db_git.get_state(env.group)['status'] == 'conflict'
    assert claim_row(mid)['last_outcome'] == 'ttl_not_expired'
