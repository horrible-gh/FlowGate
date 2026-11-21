"""Independently selectable scenarios; shared fixtures remain in test_git_orphan_claim_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_orphan_claim_0153 import (
    AT,
    B,
    CLAIM_LOG,
    EVIDENCE_ALONE,
    EVIDENCE_NAME,
    FUTURE,
    H,
    INSTANCES,
    INTENT,
    INTENT_CTX,
    LATER,
    M,
    OLD,
    ORPHAN_VERDICTS,
    OTHER_NODE,
    PAST,
    PROJECT,
    PUSH_RESULTS,
    Path,
    ProcessDeath,
    READ_FAILURES,
    RESUMABLE,
    RESUME_FACTS,
    SERVER,
    X,
    _age,
    _columns,
    _die_mid_reconcile,
    _disk_only_evidence_session,
    _facts,
    _fresh_claim_thread_state,
    _hours_ago,
    _only,
    _take_over,
    approval_intent,
    approved,
    assert_consistent,
    assert_lease_only,
    assert_untouched_by_guard,
    assert_writes_guarded,
    ast,
    branch_merge_publish,
    claim_row,
    cleanup,
    clock,
    common,
    conflict_session,
    count,
    db_claim,
    env,
    gc,
    get_store,
    git,
    git_rc,
    gs,
    inspect,
    legacy_incident_session,
    locks,
    logging,
    merge_aborts,
    merge_commit,
    names,
    og,
    on,
    open_session,
    origin_main,
    patch_store,
    plain_orphan_session,
    plant_claim,
    preserve,
    preserve_refs,
    project,
    push_rejected,
    push_timeout,
    pushes,
    pytest,
    pytestmark,
    re,
    ref_value,
    register_instances,
    registry,
    remote_commit,
    repo,
    saved,
    session_claim,
    subprocess,
    t4,
    tmp_db,
    to_conflict,
    unknown_session,
    unmerged,
)

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
