"""Fast 0153 contract checks without the migrated DB or real-Git E2E fixtures.

Use this small selection when the integration harness exceeds the execution
budget. It checks classification and reconciliation decisions only; actual Git,
SQLite, approval and cross-instance behavior remain in test_push_unknown_repro_0153.
"""
from dataclasses import replace
from subprocess import CompletedProcess

import pytest

from modules.flow_gate.services.git.command import git_failure_cause
from modules.flow_gate.services.git import reconcile_plan as rp


@pytest.mark.parametrize('rc,stderr,cause,push_class', [
    pytest.param(128, 'RPC failed: connection reset by peer', 'unknown',
                 rp.PUSH_UNKNOWN, id='unclassified'),
    pytest.param(-1, 'timeout_expired', 'timeout', rp.PUSH_UNKNOWN, id='timeout'),
    pytest.param(128, 'fatal: Authentication failed', 'auth', rp.PUSH_AUTH, id='auth'),
    pytest.param(1, ' ! [remote rejected] main -> main (pre-receive hook declined)',
                 'unknown', rp.PUSH_PERMISSION, id='rejected'),
])
def test_push_failure_cause_and_reconcile_class_agree(rc, stderr, cause, push_class):
    observed = git_failure_cause(CompletedProcess(['git', 'push'], rc, '', stderr))
    assert observed == cause
    # A hook rejection's raw cause remains unknown, but reconciliation must hold
    # for user intervention rather than treating the word "rejected" as a lease race.
    assert rp.classify_push(rc, observed, stderr) == push_class


M, LEASE, REMOTE, MOVED = ('1' * 40, '2' * 40, '3' * 40, '4' * 40)
BASE = rp.Inputs(cycle_count=1, merge_commit=M, publish_commit=M,
                 publish_lease=LEASE, local=rp.LOCAL_OK)


@pytest.mark.parametrize('facts,action,reason', [
    pytest.param(dict(probe=rp.Probe(LEASE)), rp.REPUSH, None, id='remote-unchanged'),
    pytest.param(dict(probe=rp.Probe(M)), rp.COMPLETE, 'observed', id='response-lost'),
    pytest.param(dict(probe=rp.Probe(REMOTE), fetch=rp.Probe(REMOTE),
                      merge_in_remote=rp.NO, lease_in_remote=rp.YES),
                 rp.INTEGRATE, None, id='remote-ahead'),
    pytest.param(dict(probe=rp.Probe(None, 'auth')), rp.AUTH, None, id='auth-hold'),
    pytest.param(dict(cycle_count=5, local=None), rp.EXHAUSTED,
                 rp.OUT_CYCLE_BUDGET, id='budget-before-remote-contact'),
    pytest.param(dict(probe=rp.Probe(REMOTE), fetch=rp.Probe(MOVED)),
                 rp.TRANSIENT, rp.OUT_REMOTE_MOVING, id='remote-race'),
    pytest.param(dict(cycle_count=4, probe=rp.Probe(REMOTE), fetch=rp.Probe(MOVED)),
                 rp.EXHAUSTED, rp.OUT_REMOTE_MOVING, id='last-cycle-race'),
])
def test_reconcile_safety_decision(facts, action, reason):
    decision = rp.decide(replace(BASE, **facts))
    assert (decision.action, decision.reason) == (action, reason)
    if action == rp.AUTH:
        assert (decision.stage, decision.cause) == ('ls_remote', 'auth')


def test_retry_backoff_is_bounded():
    assert [rp.backoff_seconds(cycle) for cycle in range(1, 7)] == [60, 120, 240, 480, 900, 900]