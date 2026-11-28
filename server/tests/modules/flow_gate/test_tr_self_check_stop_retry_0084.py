"""F84: failed stop retry, proof boundaries, operator override and detached children."""
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_queue as queue
from modules.flow_gate.db import tr_self_check_runs as runs


def run(**extra):
    value = dict(self_check_run_id='retry', status='running', recovery_state='incomplete',
                 cancel_requested=1, project_id='p', group_id='g', timeout_seconds=60,
                 started_at=(datetime.now(timezone.utc)-timedelta(seconds=121)).isoformat())
    value.update(extra)
    return value


@pytest.fixture
def retry(monkeypatch):
    row = run()
    calls = []
    monkeypatch.setattr(proof, 'owner_instance_of', lambda r: 'self')
    monkeypatch.setattr(proof, 'owner_server_state', lambda owner: proof.Proof(proof.MATCH, detail={'self': True}))
    monkeypatch.setattr(service, '_worker_alive', lambda rid: False)
    monkeypatch.setattr(runs, 'get_run', lambda rid: dict(row))
    def fence(rid):
        calls.append('fence')
        row['recovery_state'] = 'recovering'
        return dict(row)
    monkeypatch.setattr(runs, 'retry_recovery_incomplete', fence)
    monkeypatch.setattr(runs, 'mark_recovering', fence)
    monkeypatch.setattr(proof, 'terminate_tree', lambda r: calls.append('kill') or proof.Proof(proof.ABSENT))
    monkeypatch.setattr(proof, 'tree_state', lambda r: proof.Proof(proof.ABSENT))
    def finish(rid):
        calls.append('finish')
        row.update(status='cancelled', recovery_state='recovered')
        return dict(row)
    monkeypatch.setattr(runs, 'finish_recovered_cancelled', finish)
    monkeypatch.setattr(service, '_release_recovered', lambda *a: calls.append('release'))
    monkeypatch.setattr(service, '_emit', lambda r: None)
    return row, calls


def test_held_cancel_retries_kill_before_releasing(retry):
    row, calls = retry
    assert service._cancel_unowned(row) == ('stopped', None)
    assert calls == ['fence', 'kill', 'finish', 'release']


@pytest.mark.parametrize('state', [proof.MATCH, proof.UNKNOWN])
def test_failed_proof_never_finishes(retry, monkeypatch, state):
    row, calls = retry
    monkeypatch.setattr(proof, 'terminate_tree', lambda r: calls.append('kill') or proof.Proof(state))
    monkeypatch.setattr(service, '_hold_recovery_incomplete', lambda *a: calls.append('hold'))
    assert service._cancel_unowned(row) == ('unconfirmed', proof.TREE_UNPROVEN)
    assert calls == ['fence', 'kill', 'hold']


def test_overdue_live_worker_is_fenced_then_killed(retry, monkeypatch):
    row, calls = retry
    monkeypatch.setattr(service, '_worker_alive', lambda rid: True)
    assert service._reprove(row) == (service.RELEASED, None)
    assert calls == ['fence', 'kill', 'finish', 'release']


@pytest.mark.parametrize('extra', [{'cancel_requested':0}, {'started_at':None},
                                 {'started_at':datetime.now(timezone.utc).isoformat()}])
def test_live_worker_before_deadline_or_spawn_is_kept(retry, monkeypatch, extra):
    row, calls = retry
    row.update(extra)
    monkeypatch.setattr(service, '_worker_alive', lambda rid: True)
    assert service._reprove(row) == (service.KEPT, proof.OWNER_ALIVE)
    assert calls == []


def test_foreign_host_never_killed_and_reason_is_public(retry, monkeypatch):
    row, calls = retry
    monkeypatch.setattr(proof, 'owner_server_state', lambda owner: proof.Proof(proof.UNKNOWN,
        proof.OWNER_SERVER_UNPROVEN, {'cause':'other_node'}))
    assert service._reprove(row)[0] == service.KEPT
    assert service.stop_reason(row) == 'host_changed'
    assert not calls


@pytest.mark.parametrize('owner_state', [proof.MATCH, proof.ABSENT])
def test_stop_without_process_evidence_is_unconfirmed(retry, monkeypatch, owner_state):
    row, calls = retry
    monkeypatch.setattr(proof, 'owner_server_state',
                        lambda owner: proof.Proof(owner_state, detail={'self': True}))
    assert service.stop_reason(row) == 'release_unconfirmed'
    assert calls == []


@pytest.mark.parametrize('reason', ['processes_remain', proof.TREE_UNPROVEN, 'process_terminate_failed'])
def test_stop_with_process_evidence_keeps_process_reason(retry, reason):
    row, calls = retry
    assert service.stop_reason(row, reason) == 'processes_remain'
    row['recovery_reason'] = reason
    assert service.stop_reason(row) == 'processes_remain'
    assert calls == []


def test_saved_release_unconfirmed_remains_public(retry):
    row, calls = retry
    row['recovery_reason'] = 'release_unconfirmed'
    assert service.stop_reason(row) == 'release_unconfirmed'
    assert calls == []


def test_force_stop_keeps_error_reason(retry, monkeypatch):
    row, calls = retry
    monkeypatch.setattr(service, '_controls', {})
    monkeypatch.setattr(runs, 'request_cancel', lambda rid: None)
    def refuse(*a):
        raise service.SelfCheckError(409, 'selfcheck_cancel_unconfirmed',
            details={'cancel':{'reason':proof.TREE_UNPROVEN}})
    monkeypatch.setattr(service, '_cancel', refuse)
    monkeypatch.setattr(runs, 'record_stop_reason', lambda rid, why: calls.append(why))
    assert queue.force_stop('retry', wait_sec=0)['stop_reason'] == 'processes_remain'
    assert calls == ['processes_remain']


def test_startup_dead_owner_empty_tree_ends_cancelled(retry, monkeypatch):
    row, calls = retry
    monkeypatch.setattr(proof, 'owner_server_state', lambda owner: proof.Proof(proof.ABSENT))
    monkeypatch.setattr(proof, 'release_verdict', lambda *a, **kw: SimpleNamespace(decision=proof.RELEASE))
    service._recover_row(row, set())
    assert calls == ['fence', 'finish', 'release']


def test_marker_signal_pins_and_rechecks_identity(monkeypatch):
    row = run(owner_marker=json.dumps({'kind':'posix_marker'}))
    calls=[]
    monkeypatch.setattr(proof, 'marker_scan', lambda rid: (proof.MATCH, (1234,)))
    monkeypatch.setattr(proof.executor, 'start_identity', lambda pid: 'first')
    monkeypatch.setattr(proof.os, 'pidfd_open', lambda pid: calls.append('pin') or 99, raising=False)
    monkeypatch.setattr(proof.os, 'close', lambda fd: calls.append('close'))
    monkeypatch.setattr(proof.executor, 'identity_state', lambda *a: proof.MISMATCH)
    monkeypatch.setattr(proof.signal, 'pidfd_send_signal', lambda *a: pytest.fail('reused PID signalled'), raising=False)
    proof._terminate_marked(row)
    assert calls == ['pin','close']


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'pidfd_open'), reason='Linux pidfd required')
def test_real_detached_process_is_killed_without_touching_neighbor(monkeypatch):
    rid='test_detached_f84'
    env=dict(os.environ, FG_SELFCHECK_RUN_ID=rid)
    child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], env=env, start_new_session=True)
    neighbor=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    try:
        assert os.getsid(child.pid) == child.pid
        if not hasattr(signal, 'pidfd_send_signal'):
            pytest.skip('pidfd signals unavailable')
        try:
            fd = os.pidfd_open(child.pid)
            signal.pidfd_send_signal(fd, 0)
            os.close(fd)
            proof.Path(f'/proc/{child.pid}/environ').read_bytes()
        except OSError as exc:
            pytest.skip(f'child identity probes unavailable: {exc}')
        # Limit discovery to our children; identity, marker and pidfd signalling stay real.
        monkeypatch.setattr(proof, 'marker_scan', lambda run_id: (proof.MATCH, (child.pid, neighbor.pid)))
        proof._terminate_marked(run(self_check_run_id=rid, owner_marker=json.dumps({'kind':'posix_marker'})))
        assert child.wait(timeout=5) == -signal.SIGKILL
        assert neighbor.poll() is None
    finally:
        for proc in (child,neighbor):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def audit_store(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    from modules.flow_gate.db import workflow_events
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.executescript("""
        CREATE TABLE tr_self_check_runs (self_check_run_id TEXT PRIMARY KEY, project_id TEXT,
          group_id TEXT, status TEXT, recovery_state TEXT, recovery_reason TEXT, error_code TEXT,
          active_key TEXT, cancel_requested INTEGER, updated_at TEXT, finished_at TEXT);
        CREATE TABLE workflow_events (id INTEGER PRIMARY KEY, event_type TEXT, project_id TEXT,
          group_id TEXT, document_id TEXT, actor_user_id TEXT, from_state TEXT, to_state TEXT,
          metadata TEXT, created_at TEXT);
        INSERT INTO tr_self_check_runs VALUES
          ('r', 'p', 'g', 'running', 'incomplete', 'processes_remain', NULL, 'p:g', 1, 'old', NULL);
    """)
    class Store:
        @contextmanager
        def transaction(self):
            with db:
                yield
        def _execute_affected(self, sql, args):
            return db.execute(sql,args).rowcount
        def _execute(self, sql, args):
            db.execute(sql,args)
        def _fetch_one(self, sql, args=None):
            value=db.execute(sql,args or []).fetchone()
            return dict(value) if value else None
    store=Store()
    monkeypatch.setattr(runs, 'get_store', lambda: store)
    monkeypatch.setattr(workflow_events, 'get_store', lambda: store)
    monkeypatch.setattr(runs, 'get_run', lambda rid: store._fetch_one(
        'SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ?', [rid]))
    yield store
    db.close()


def test_operator_override_is_atomic_audited_and_cas_fenced(audit_store):
    before=runs.get_run('r')
    ended=runs.mark_ended_by_user(before, 'operator')
    assert ended['status'] == 'cancelled' and ended['active_key'] is None
    assert ended['error_code'] == 'selfcheck_marked_ended'
    event=audit_store._fetch_one('SELECT * FROM workflow_events')
    assert event['actor_user_id'] == 'operator'
    assert event['event_type'] == 'selfcheck_mark_ended'
    assert json.loads(event['metadata'])['process_exit_confirmed'] is False
    assert runs.mark_ended_by_user(before, 'operator') is None
    assert audit_store._fetch_one('SELECT count(*) AS n FROM workflow_events')['n'] == 1


def test_failed_audit_rolls_back_terminal_write(audit_store, monkeypatch):
    from modules.flow_gate.db import workflow_events
    def fail(data):
        raise RuntimeError('audit store failed')
    monkeypatch.setattr(workflow_events, 'create', fail)
    with pytest.raises(RuntimeError):
        runs.mark_ended_by_user(runs.get_run('r'), 'operator')
    assert runs.get_run('r')['status'] == 'running'
    assert runs.get_run('r')['active_key'] == 'p:g'


def test_stop_reason_persists_only_on_active_cancelled_request(audit_store):
    runs.record_stop_reason('r','host_changed')
    assert runs.get_run('r')['recovery_reason'] == 'host_changed'
    runs.mark_ended_by_user(runs.get_run('r'),'operator')
    runs.record_stop_reason('r','processes_remain')
    assert runs.get_run('r')['recovery_reason'] == 'marked_ended_by_user'


def test_overdue_pending_live_worker_gets_kill_but_no_release(retry, monkeypatch):
    row,calls=retry
    row.update(status='pending', recovery_state='none')
    monkeypatch.setattr(service, '_worker_alive', lambda rid: True)
    assert service._reprove(row) == (service.KEPT, proof.OWNER_ALIVE)
    assert calls == ['kill']


def test_overdue_pending_without_worker_can_finish(retry):
    row,calls=retry
    row.update(status='pending', recovery_state='none')
    assert service._reprove(row) == (service.RELEASED, None)
    assert calls == ['fence', 'kill', 'finish', 'release']


def test_restart_does_not_reexecute_old_snapshot(retry, monkeypatch):
    row,calls=retry
    row.update(cancel_requested=0, enqueued_at='before-restart', snapshot_path='old-snapshot')
    monkeypatch.setattr(proof,'owner_server_state',lambda owner: proof.Proof(proof.ABSENT))
    monkeypatch.setattr(proof,'release_verdict',lambda *a, **kw: SimpleNamespace(decision=proof.RELEASE))
    monkeypatch.setattr(queue,'snapshot_exists',lambda r: True)
    monkeypatch.setattr(runs,'requeue_recovered',lambda *a: pytest.fail('interrupted command executed again'))
    def finish(rid):
        calls.append('finish-interrupted')
        row.update(status='failed',error_code='interrupted_by_restart')
        return dict(row)
    monkeypatch.setattr(runs,'finish_recovered_interrupted',finish)
    service._recover_row(row,set())
    assert row['status']=='failed' and row['error_code']=='interrupted_by_restart'
    assert calls==['fence','finish-interrupted','release']


def test_windows_job_termination_still_requires_empty_tree(monkeypatch):
    calls=[]
    monkeypatch.setattr(proof, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(proof, '_marker', lambda r: {'job':'Local-test'})
    monkeypatch.setattr(proof, 'tree_state', lambda r: proof.Proof(proof.MATCH))
    monkeypatch.setattr(proof, 'windows_job_state', lambda name, **kw: calls.append((name,kw)) or proof.MATCH)
    assert proof.terminate_tree(run(),wait=0).state == proof.MATCH
    assert calls == [('Local-test', {'terminate':True, 'wait':0})]


@pytest.mark.parametrize('code', ['selfcheck_run_not_found', 'selfcheck_state_conflict'])
def test_force_stop_non_cancel_errors_are_queue_errors(retry, monkeypatch, code):
    row, calls = retry
    monkeypatch.setattr(service, '_controls', {})
    monkeypatch.setattr(runs, 'request_cancel', lambda rid: None)
    def refuse(*args):
        monkeypatch.setattr(runs, 'get_run', lambda rid: None)
        raise service.SelfCheckError(404 if code == 'selfcheck_run_not_found' else 409, code)
    monkeypatch.setattr(service, '_cancel', refuse)
    monkeypatch.setattr(runs, 'record_stop_reason', lambda *args: pytest.fail('unrelated error recorded'))
    with pytest.raises(queue.QueueError) as caught:
        queue.force_stop('retry', wait_sec=0)
    assert caught.value.code == code


def test_force_stop_unconfirmed_deleted_row_is_not_found(retry, monkeypatch):
    monkeypatch.setattr(service, '_controls', {})
    monkeypatch.setattr(runs, 'request_cancel', lambda rid: None)
    def refuse(*args):
        monkeypatch.setattr(runs, 'get_run', lambda rid: None)
        raise service.SelfCheckError(409, 'selfcheck_cancel_unconfirmed')
    monkeypatch.setattr(service, '_cancel', refuse)
    with pytest.raises(queue.QueueError) as caught:
        queue.force_stop('retry', wait_sec=0)
    assert caught.value.code == 'selfcheck_run_not_found'


@pytest.mark.parametrize('admitted,age,overdue', [(600, 200, False), (60, 200, True)])
def test_card_and_reaper_share_admitted_deadline(monkeypatch, admitted, age, overdue):
    from modules.flow_gate.services import tr_self_check_card as card
    from flowgate.card_settings import verify_limits
    monkeypatch.setattr(verify_limits, 'resolve_verify_limits',
                        lambda *args: pytest.fail('current settings must not be consulted'))
    row = run(verify_timeout_sec=admitted,
              started_at=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat())
    assert card._deadline(row) == service.stop_deadline(row)
    assert service.stop_overdue(row) is overdue
    assert (card._now() >= card._deadline(row)) is overdue


def test_unknown_marker_scan_never_signals(monkeypatch):
    monkeypatch.setattr(proof, 'marker_scan', lambda rid: (proof.UNKNOWN, (1234,)))
    monkeypatch.setattr(proof, 'os', SimpleNamespace(
        pidfd_open=lambda pid: pytest.fail('unknown ownership must not signal')))
    proof._terminate_marked(run(owner_marker=json.dumps({'kind':'posix_marker'})))
