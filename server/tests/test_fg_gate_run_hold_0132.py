"""0132 T#3: NR §5.3-8(a-f), §5.3-9(e-j), activity matrix and migration safety.

Real SQLite, intake/routes/Flow guards and resolver; job states model parked and live writers.
Actual Git/Runner/Sweeper mixed-wait paths are in test_fg_gate_run_resume_0132.py.
"""
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / 'tests' / 'flowgate'))
sys.path.insert(0, str(SERVER))

from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node, setting
from test_fg_gate_intake_0085 import (
    LEE, _confirmed, _current, _decide, _linear, _nodes, _start, _states, product, slot)


def accepted(e):
    g, s = _linear(e)
    rid = _start(e, 'full')['run_id']
    setting(e, FA, g, 'merge_mode', 'commit')
    body = _decide(e, rid, g, {'decision': 'confirm', 'request_id': 'hold-0132'}, 202)
    return g, s, rid, body['decision']['decision_id'], body['job']['job_id']


def park(e, did, jid, *, state='recovery_required', note='gate_recovery', effect=True):
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.db.connection import get_store
    with get_store().transaction():
        d = repo.get(did)
        assert repo.move(did, d['decision_version'], state, last_error_code='push_rejected')
        if effect:
            repo.record_effect(did, 'merge', status=repo.DONE, sha_after='a'*40, evidence={'changed': True})
        get_store()._execute("UPDATE operation_job SET status='recovery_required', phase_note=?, "
                             "lease_token=NULL,lease_owner=NULL,lease_until=NULL,retryable=1 WHERE job_id=?",
                             [note,jid])
    from flowgate.gate_decisions import legacy_adapter as legacy
    legacy.guarded_run(d['run_id'], lambda run,jobs: legacy.settle_run(
        legacy.load_context(run,'flowgate','u_lee',jobs)))


@pytest.mark.parametrize('decision,status,lease,expected', [
    ('accepted','pending',False,'LIVE'), ('accepted','blocked',False,'LIVE'),
    ('accepted','retry_wait',False,'LIVE'), ('accepted','claimed',False,'LIVE'),
    ('accepted','running',False,'LIVE'), ('failed','running',False,'LIVE'),
    ('recovery_required','recovery_required',True,'LIVE'),
    ('failed','pending',False,'PARKED'), ('recovery_required','pending',False,'PARKED'),
    ('accepted','recovery_required',False,'PARKED'), ('accepted',None,False,'PARKED'),
])
def test_activity_uses_writer_before_decision_and_queued_state(monkeypatch, decision, status, lease, expected):
    from flowgate.gate_decisions import run_hold as h
    monkeypatch.setattr(h.repo,'reservations_of_run',lambda rid: {'g':{'status':decision,'job_id':'j'}})
    job = None if status is None else {'status':status,'lease_token':'t' if lease else None,
                                      'lease_until':'2999-01-01T00:00:00+00:00'}
    monkeypatch.setattr(h.legacy,'get_job',lambda jid: job)
    assert h.gate_activity('r') == {'g':expected}
    assert h.gate_live('r') == (expected == 'LIVE')


@pytest.mark.parametrize('state,note,effect',[
    ('recovery_required','gate_recovery',True), ('failed','gate_failed',False),
    ('recovery_required','gate_conflict',True)])
def test_park_settles_idle_and_keeps_only_confirm_settings_reserved(product, slot, state, note, effect):
    from flowgate.gate_decisions import legacy_adapter as legacy
    from flowgate.graph_run import editing, hold
    g,s,rid,did,jid=accepted(product)
    park(product,did,jid,state=state,note=note,effect=effect)
    wire=_current(product)
    assert (wire['status'],wire['idle_reason']) == ('idle','gate_publish')
    gate=_nodes(wire)[g]['gate']
    assert gate['parked'] and not gate['git_active'] and not gate['can_decide']
    assert gate['settings_locked'] and gate['settings_lock_reason']=='decision_reserved'
    def check(run,jobs):
        editing.require_editable(FA)
        hold.require_settable(FA,s,True)
        editing.require_settings_editable(FA)
    legacy.guarded_run(rid,check)
    assert setting(product, FA, s, 'restart', 2)['settings_version'] >= 1


@pytest.mark.parametrize('effect',[False,True])
def test_stop_finishes_with_only_parked_gate_attempts(product,slot,monkeypatch,effect):
    from flowgate.gate_decisions import publish,repository as repo
    g,s,rid,did,jid=accepted(product)
    park(product,did,jid,state='recovery_required' if effect else 'failed',
         note='gate_recovery' if effect else 'gate_failed',effect=effect)
    # Local Git observation is outside this SQLite fixture; use its recorded external effect.
    monkeypatch.setattr(publish,'effects_now',lambda row:repo.effects_state(row['decision_id']))
    res=product['client'].post(f'/api/v1/runs/{rid}/stop',json={},headers=_h())
    assert res.status_code==200,res.text
    wire=_current(product)
    assert (wire['status'],wire['idle_reason'])==('idle','stopped')
    d=repo.get(did)
    assert d['status']==('recovery_required' if effect else 'canceled')
    assert _states(product)[g]==('open' if effect else 'pending')


@pytest.mark.parametrize('idle_reason',['waiting_confirm','needs_intervention','error','merge_conflict','gate_publish'])
def test_resume_uses_acceptance_intent_independent_of_idle_reason(product,slot,idle_reason):
    from flowgate.gate_decisions import legacy_adapter as legacy,repository as repo,run_hold
    g,s,rid,did,jid=accepted(product)
    park(product,did,jid)
    _current(product)
    before=_states(product)
    _sql(product['db'],"UPDATE graph_runs SET idle_reason=? WHERE run_id=?",(idle_reason,rid))
    def wake(run,jobs):
        ctx=legacy.load_context(run,'flowgate','u_lee',jobs)
        assert run_hold.gate_resume_wake(ctx,repo.get(did))
    legacy.guarded_run(rid,wake)
    row=_sql(product['db'],'SELECT status,idle_reason FROM graph_runs WHERE run_id=?',(rid,))[0]
    assert row=={'status':'running','idle_reason':None}
    assert _states(product)==before
    event=_sql(product['db'],"SELECT cause,reason FROM graph_run_events WHERE run_id=? ORDER BY event_seq DESC LIMIT 1",(rid,))[0]
    assert event=={'cause':'work_resumed','reason':'gate_resume'}


@pytest.mark.parametrize('intent',['stopped','paused','stopping','user_stop','user_pause','user_command',
                                  'graph_time_machine','tm_busy','step_outside','inactive','missing_intent'])
def test_resume_respects_user_intent_and_tm_fence(product,slot,monkeypatch,intent):
    from flowgate.gate_decisions import legacy_adapter as legacy,repository as repo,run_hold
    from flowgate.graph_time_machine import operation
    g,s,rid,did,jid=accepted(product)
    park(product,did,jid)
    _current(product)
    if intent in ('stopped','paused','stopping'):
        status='idle' if intent=='stopped' else intent
        _sql(product['db'],"UPDATE graph_runs SET status=?,idle_reason=? WHERE run_id=?",(status,'stopped' if intent=='stopped' else None,rid))
    elif intent=='tm_busy': monkeypatch.setattr(operation,'busy',lambda flow:True)
    elif intent=='step_outside':
        _sql(product['db'],"UPDATE graph_runs SET run_mode='step',step_node_keys=? WHERE run_id=?",(json.dumps([s]),rid))
    elif intent=='inactive':
        _sql(product['db'],"UPDATE graph_runs SET active_group_id=NULL WHERE run_id=?",(rid,))
    elif intent=='missing_intent':
        _sql(product['db'],"UPDATE gate_decisions SET accept_wake=0 WHERE decision_id=?",(did,))
    else:
        def event(run,jobs):
            legacy.emit_run_status(legacy.load_context(run,'flowgate','u_lee',jobs),'idle','idle',intent)
        legacy.guarded_run(rid,event)
    def check(run,jobs):
        assert not run_hold.gate_resume_wake(legacy.load_context(run,'flowgate','u_lee',jobs),repo.get(did))
    legacy.guarded_run(rid,check)
    assert _sql(product['db'],'SELECT status FROM graph_runs WHERE run_id=?',(rid,))[0]['status']!='running'


@pytest.mark.parametrize('status',['idle','running','paused'])
def test_live_writer_locks_edit_hold_and_time_machine_even_without_wake(product,slot,status):
    from flowgate.gate_decisions import legacy_adapter as legacy
    from flowgate.graph_run import editing,hold
    from flowgate.graph_time_machine import service
    g,s,rid,did,jid=accepted(product)
    _sql(product['db'],"UPDATE operation_job SET status='running' WHERE job_id=?",(jid,))
    _sql(product['db'],"UPDATE graph_runs SET status=?,idle_reason=? WHERE run_id=?",(status,'stopped' if status=='idle' else None,rid))
    def check(run,jobs):
        for call in (lambda:editing.require_editable(FA),lambda:hold.require_settable(FA,s,True),lambda:service.require_safe(FA,g)):
            with pytest.raises(legacy.GraphApiError) as exc: call()
            assert exc.value.code=='graph_run_locked'
            assert exc.value.params['reason']=='gate_git_active'
    legacy.guarded_run(rid,check)
    assert _nodes(_current(product))[g]['gate']['git_active']


def test_park_never_settles_a_different_ai_execution_idle(product,slot,monkeypatch):
    from flowgate.graph_run import execution_store
    g,s,rid,did,jid=accepted(product)
    monkeypatch.setattr(execution_store,'working',lambda run:True)
    park(product,did,jid)
    assert _sql(product['db'],'SELECT status FROM graph_runs WHERE run_id=?',(rid,))[0]['status']=='running'
    assert _current(product)['status']=='running'


def test_legacy_acceptance_backfill_enables_mixed_wait_retry(product,slot):
    from flowgate.gate_decisions import repository as repo
    g,s,rid,did,jid=accepted(product)
    park(product,did,jid)
    _current(product)
    # Pre-174 decisions have neither durable intent fields nor the new command snapshot.
    old_settings = dict(repo.get(did)['settings'])
    old_settings.pop('accept_command', None)
    for event in _sql(product['db'], "SELECT event_seq,payload_json FROM graph_run_events WHERE run_id=? "
                      "AND cause='work_resumed' AND reason='gate_publish'", (rid,)):
        old_payload = json.loads(event['payload_json'])
        old_payload.pop('step_node_keys', None)
        old_payload.pop('active_group_id', None)
        _sql(product['db'], 'UPDATE graph_run_events SET payload_json=? WHERE run_id=? AND event_seq=?',
             (json.dumps(old_payload), rid, event['event_seq']))
    _sql(product['db'], 'UPDATE gate_decisions SET accept_wake=0,accept_event_seq=NULL,settings_json=? '
         'WHERE decision_id=?', (json.dumps(old_settings), did))
    sql=(SERVER/'sql/migrations/sqlite'/'174_gate_accept_wake.sql').read_text(encoding='utf-8')
    with sqlite3.connect(product['db']) as conn:
        conn.executescript('WITH wake AS'+sql.split('WITH wake AS',1)[1])
    d=repo.get(did)
    assert d['accept_wake']==1 and d['accept_event_seq'] is not None
    _sql(product['db'],"UPDATE graph_runs SET idle_reason='waiting_confirm' WHERE run_id=?",(rid,))
    res=product['client'].post(f'/api/v1/runs/{rid}/nodes/{g}/gate-decisions/{did}/retry',
        json={'request_id':'legacy-retry','expected_decision_version':d['decision_version']},headers=_h())
    assert res.status_code==202,res.text
    assert _sql(product['db'],'SELECT status FROM graph_runs WHERE run_id=?',(rid,))[0]['status']=='running'


MIG='174_gate_accept_wake.sql'
def seed_migration(conn):
    conn.execute('CREATE TABLE gate_decisions(decision_id TEXT PRIMARY KEY,run_id TEXT,status TEXT,accepted_at TEXT)')
    conn.execute('CREATE TABLE graph_run_events(run_id TEXT,event_seq BIGINT,event_type TEXT,cause TEXT,reason TEXT,created_at TEXT)')
    for i,kind in enumerate(('valid','stop','pause','tm','command','no_wake','not_first','terminal')):
        conn.execute('INSERT INTO gate_decisions VALUES (?,?,?,?)',(kind,kind,'succeeded' if kind=='terminal' else 'recovery_required','2026-01-01T00:00:01'))
        conn.execute('INSERT INTO graph_run_events VALUES (?,1,?,?,?,?)',(kind,'graph_run_status_changed','work_resumed',
                      'other' if kind=='no_wake' else 'gate_publish','2026-01-01T00:00:02'))
        cause={'stop':'user_stop','pause':'user_pause','tm':'graph_time_machine','command':'user_command'}.get(kind)
        if cause:
            conn.execute('INSERT INTO graph_run_events VALUES (?,2,?,?,NULL,?)',(kind,'graph_run_status_changed',cause,'2026-01-01T00:00:03'))
        if kind=='not_first':
            conn.execute('INSERT INTO graph_run_events VALUES (?,0,?,?,NULL,?)',(kind,'graph_run_status_changed','run_idle','2026-01-01T00:00:01'))


def test_sqlite_migration_backfills_only_proven_wakes_and_rolls_back(tmp_path):
    with sqlite3.connect(tmp_path/'migration.db') as conn:
        seed_migration(conn)
        before=conn.execute('SELECT * FROM gate_decisions ORDER BY decision_id').fetchall()
        conn.executescript((SERVER/'sql/migrations/sqlite'/MIG).read_text(encoding='utf-8'))
        rows=conn.execute('SELECT decision_id,accept_wake,accept_event_seq FROM gate_decisions ORDER BY decision_id').fetchall()
        assert [r for r in rows if r[1]]==[('valid',1,1)]
        assert all(r[2] is None for r in rows if not r[1])
        conn.executescript((SERVER/'sql/rollback/sqlite'/MIG).read_text(encoding='utf-8'))
        assert conn.execute('SELECT * FROM gate_decisions ORDER BY decision_id').fetchall()==before
        conn.executescript((SERVER/'sql/migrations/sqlite'/MIG).read_text(encoding='utf-8'))
        assert conn.execute("SELECT accept_wake,accept_event_seq FROM gate_decisions WHERE decision_id='valid'").fetchone()==(1,1)


@pytest.mark.postgres
def test_postgres_migration_apply_reapply_rollback_on_isolated_schema():
    dsn=os.environ.get('FLOWGATE_PG_TEST_DSN') or os.environ.get('FLOWGATE_POSTGRES_TEST_DSN')
    if not dsn: pytest.skip('PostgreSQL test DSN is not configured')
    psycopg=pytest.importorskip('psycopg')
    schema='fg_gate_hold_0132_'+uuid.uuid4().hex[:10]
    with psycopg.connect(dsn,autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            # Seed through SQLite-compatible parameter spelling, translated only by this fixture adapter.
            class Seed:
                def execute(self,sql,args=()): return conn.execute(sql.replace('?', '%s'),args)
            seed_migration(Seed())
            migration=(SERVER/'sql/migrations/postgres'/MIG).read_text(encoding='utf-8')
            conn.execute(migration)
            conn.execute(migration)
            assert conn.execute('SELECT decision_id FROM gate_decisions WHERE accept_wake=1').fetchall()==[('valid',)]
            conn.execute((SERVER/'sql/rollback/postgres'/MIG).read_text(encoding='utf-8'))
            assert len(conn.execute('SELECT * FROM gate_decisions').fetchone())==4
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


# The current persisted job CHECK has no 'claimed'; the activity matrix above covers that adapter state.
@pytest.mark.parametrize('job_status', ['pending', 'blocked', 'retry_wait'])
def test_queued_acceptance_locks_an_idle_run_without_overriding_stop(product, slot, job_status):
    """LIVE includes queued accepted work even when command intent intentionally leaves the run idle."""
    from flowgate.gate_decisions import legacy_adapter as legacy
    from flowgate.graph_run import editing, hold
    from flowgate.graph_time_machine import service
    g, s, rid, did, jid = accepted(product)
    _sql(product['db'], "UPDATE operation_job SET status=?,lease_token=NULL,lease_owner=NULL,lease_until=NULL "
         "WHERE job_id=?", (job_status, jid))
    _sql(product['db'], "UPDATE graph_runs SET status='idle',idle_reason='stopped' WHERE run_id=?", (rid,))
    def check(run, jobs):
        for call in (lambda: editing.require_editable(FA), lambda: hold.require_settable(FA, s, True),
                     lambda: service.require_safe(FA, g)):
            with pytest.raises(legacy.GraphApiError) as exc:
                call()
            assert exc.value.code == 'graph_run_locked'
            assert exc.value.params['reason'] == 'gate_git_active'
    legacy.guarded_run(rid, check)
    wire = _current(product)
    assert (wire['status'], wire['idle_reason']) == ('idle', 'stopped')
    assert _nodes(wire)[g]['gate']['git_active']
    assert not _nodes(wire)[g]['gate']['parked']



def test_resume_rejects_replaced_command_even_if_later_event_matches_current(product, slot):
    """Running intake has no wake event; a later matching payload cannot prove the original command."""
    from flowgate.gate_decisions import legacy_adapter as legacy, repository as repo, run_hold
    g, s = _linear(product)
    rid = _start(product, 'full')['run_id']
    setting(product, FA, g, 'merge_mode', 'commit')
    _sql(product['db'], "UPDATE graph_runs SET status='running',idle_reason=NULL WHERE run_id=?", (rid,))
    body = _decide(product, rid, g, {'decision': 'confirm', 'request_id': 'running-intake'}, 202)
    did, jid = body['decision']['decision_id'], body['job']['job_id']
    def replace(run, jobs):
        ctx = legacy.load_context(run, 'flowgate', 'u_lee', jobs)
        assert legacy.run_store.set_run_command(rid, 'step', [g]) == 1
        ctx.run['run_mode'], ctx.run['step_node_keys'] = 'step', [g]
        # Payload changes are independently fenced even when no explicit user-command cause was recorded.
        legacy.emit_run_status(ctx, 'running', 'running', 'work_resumed', 'gate_resume')
    legacy.guarded_run(rid, replace)
    park(product, did, jid)
    def check(run, jobs):
        ctx = legacy.load_context(run, 'flowgate', 'u_lee', jobs)
        assert not run_hold.gate_resume_wake(ctx, repo.get(did))
    legacy.guarded_run(rid, check)
    assert _sql(product['db'], 'SELECT status FROM graph_runs WHERE run_id=?', (rid,))[0]['status'] == 'idle'


def test_legacy_resume_compares_command_with_the_acceptance_wake_event(product, slot):
    """A migrated decision's original mode lives in its gate_publish wake, even without a settings snapshot."""
    from flowgate.gate_decisions import legacy_adapter as legacy, repository as repo, run_hold
    g, s, rid, did, jid = accepted(product)
    old_settings = dict(repo.get(did)['settings'])
    old_settings.pop('accept_command', None)
    seq = _sql(product['db'], "SELECT event_seq FROM graph_run_events WHERE run_id=? "
               "AND cause='work_resumed' AND reason='gate_publish' ORDER BY event_seq DESC LIMIT 1", (rid,))[0]['event_seq']
    _sql(product['db'], 'UPDATE gate_decisions SET settings_json=?,accept_event_seq=? WHERE decision_id=?',
         (json.dumps(old_settings), seq, did))
    def replace(run, jobs):
        ctx = legacy.load_context(run, 'flowgate', 'u_lee', jobs)
        assert legacy.run_store.set_run_command(rid, 'step', [g]) == 1
        ctx.run['run_mode'], ctx.run['step_node_keys'] = 'step', [g]
        legacy.emit_run_status(ctx, 'running', 'running', 'work_resumed', 'gate_resume')
    legacy.guarded_run(rid, replace)
    park(product, did, jid)
    def check(run, jobs):
        ctx = legacy.load_context(run, 'flowgate', 'u_lee', jobs)
        assert not run_hold.gate_resume_wake(ctx, repo.get(did))
    legacy.guarded_run(rid, check)
