"""0126 T#5 A3/A4: real settings routes, SQLite assignments/ledger, AI admission recorder.
No external model, deployed server, or existing user data is used.
"""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    A, Y, X, B, FA, _sql, assignments, command, db_path, decided, env, events,
    flow, ledger, node_run, one, roles, starts, stored, wired,
)
from test_fg_command_review_0125 import (  # noqa: F401
    _spent, _pass, _loops, _reviews, _doc, _read, _run, completions, fork_join,
)
from test_fg_command_rerequest_0124 import _reason
from test_fg_settings_capability_0126 import save
from test_fg_card_settings import _h, get_settings

pytestmark = pytest.mark.usefixtures('fork_join', 'completions')


def history(env):
    return {t: _sql(env['db'], 'SELECT * FROM '+t) for t in (
        'document_revisions', 'document_reviews', 'graph_stage_assignments')}


def new_settings(env, key):
    before = get_settings(env, FA, key)
    for field, value in [('execution_role', {'kind':'assigned','provider_id':A}),
                         ('review_role', {'kind':'assigned','provider_id':Y}), ('review_count',1)]:
        after = save(env,key,field,value)
    assert after['settings_version'] == before['settings_version'] + 3
    return before, after


@pytest.mark.parametrize('mode', ['step','full'])
@pytest.mark.parametrize('outcome', ['exhausted','verdict_hold','abnormal','completion_missing','failed_review'])
def test_a3_a4_current_tuple_reaches_actual_loop_admission_and_history_stays_fixed(
        env, decided, monkeypatch, starts, mode, outcome, tmp_path):
    key, follower, result, latest = _spent(env,monkeypatch,starts,mode)
    first = one(env,key,'result_review',1)
    if outcome == 'verdict_hold':
        _reason(env,key,result,first,'review_not_passed:review_verdict_hold')
    elif outcome == 'abnormal':
        _reason(env,key,result,first,'run_not_clean:timeout')
    elif outcome == 'completion_missing':
        _sql(env['db'],"DELETE FROM graph_run_events WHERE cause = 'auto_approval_skipped'")
    elif outcome == 'failed_review':
        _sql(env['db'],"UPDATE graph_stage_assignments SET stage_state = 'failed', fail_cause = 'review_not_started', "
                       "verdict = NULL WHERE attempt_id = ? AND stage = 'result_review'",(first['attempt_id'],))
        _reason(env,key,result,first,'review_not_started')
    frozen = history(env)
    old, new = new_settings(env,key)
    assert history(env) == frozen
    calls = len(_loops(starts))
    for _ in range(2):
        get_settings(env,FA,key)
        _read(env)
    # Explicit setting writes and reads are not commands.
    assert len(_loops(starts)) == calls
    # A failed review's normal reconciliation can retry within its bounded policy;
    # the exhausted-cycle reason in this fixture suppresses it until this command.
    command(env,mode)
    second = one(env,key,'result_review',2)
    assert second and (second['provider_id'],second['rework_provider_id'],second['review_rounds']) == (Y,A,1)
    assert second['attempt_id'] == first['attempt_id']
    assert all(row in history(env)[table] for table,rows in frozen.items() for row in rows)
    [call] = _loops(starts)[calls:]
    loop = call['document_review_loop']
    assert (loop['reviewer_provider_id'],loop['rework_provider_id'],loop['review_count']) == (Y,A,1)
    request = next(r for r in ledger(env) if r['exec_id'] == second['intent_exec_id'])
    raw = json.loads(request['request_json'])
    assert (request['assignment_stage'],request['assignment_seq']) == ('result_review',2)
    assert raw['assignment_ref'] == {'attempt_id':first['attempt_id'],'stage':'result_review','seq':2}
    [ev] = [p for _,p in events(env,'stage_assigned') if p.get('node_key')==key and p.get('stage')=='result_review' and p.get('seq')==2]
    assert (ev['settings_version'],ev['review_rounds'],ev['rework_provider_id']) == (new['settings_version'],1,A)
    assert json.loads(_run(env)['step_node_keys']) == [key] if mode == 'step' else _run(env)['step_node_keys'] is None
    # A running command returns its actual blocking reason and duplicates no assignment/token.
    duplicate=env['client'].post(f'/api/v1/flows/{FA}/runs',json={'revision_no':None,'mode':mode},headers=_h())
    assert duplicate.status_code==409 and duplicate.json()['error']['code']=='run_already_active' 
    assert len(_reviews(env,key)) == 2 and len(_loops(starts)) == calls+1
    _pass(env,key,2,result,'air_0126_new_pass')
    assert node_run(env,key)['status'] == 'done'
    assert node_run(env,follower)['status'] == ('pending' if mode=='step' else 'open')
    if outcome=='exhausted':
        print('RETRY_EVIDENCE '+json.dumps({
            'mode':mode,'run_id':first['run_id'],'node_key':key,
            'old_settings_version':old['settings_version'],'new_settings_version':new['settings_version'],
            'attempt_id':first['attempt_id'],'old_seq':first['seq'],'new_seq':second['seq'],
            'old_tuple':[first['provider_id'],first['rework_provider_id'],first['review_rounds']],
            'actual_tuple':[loop['reviewer_provider_id'],loop['rework_provider_id'],loop['review_count']],
            'exec_id':second['intent_exec_id'],'history_unchanged':True},sort_keys=True))
    (tmp_path/'retry-evidence.json').write_text(json.dumps({
        'mode':mode,'case':outcome,'old_version':old['settings_version'],'new_version':new['settings_version'],
        'old_assignment':first,'new_assignment':second,'actual_loop':loop,'event':ev,
        'old_history_unchanged':True},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.parametrize('mode',['step','full'])
def test_explicit_card_hold_stays_held_until_its_own_release(env,decided,monkeypatch,starts,mode):
    key,_,_,_ = _spent(env,monkeypatch,starts,mode,follow=False)
    new_settings(env,key)
    save(env,key,'hold',True)
    before=history(env)
    command(env,mode)
    assert history(env)==before
    save(env,key,'hold',False)
    command(env,mode)
    assert one(env,key,'result_review',2)['rework_provider_id']==A


def test_same_assignment_automatic_retry_keeps_its_provider_after_settings_change(
        env,decided,monkeypatch,starts):
    from flowgate.card_settings import stage_assignment as sa
    key,_,_,_ = _spent(env,monkeypatch,starts,'step',follow=False)
    writer=one(env,key,'result')
    new_settings(env,key)
    target=sa.retry_target(writer['run_id'],key,writer['attempt_id'],'result')
    assert target['provider_id']==X
    assert target['assignment_ref']=={'attempt_id':writer['attempt_id'],'stage':'result','seq':0}

@pytest.mark.parametrize('mode',['step','full'])
@pytest.mark.parametrize('failure',['provider_failed','timeout','no_result'])
@pytest.mark.parametrize('changed',[False,True])
def test_failed_writer_explicit_command_gets_new_attempt_and_current_provider(
        env,decided,monkeypatch,starts,mode,failure,changed,tmp_path):
    from test_fg_provider_failure_retry_0121 import _finalized
    key=flow(env,extra_settings=(('instruction','omit'),('review_count',3)))
    roles(key,execution=X,review=B)
    command(env,mode)
    [job]=env['jobs']
    first=one(env,key,'result')
    ending={'stop_code':failure}
    if failure=='timeout': ending['end_reason']='timeout'
    if failure=='no_result': ending.update(stop_code='no_output_exhausted',exit_code=0)
    _finalized(env,'air_0126_writer_end',job['graph_exec_id'],**ending)
    _read(env)
    frozen=history(env)
    old,new=new_settings(env,key) if changed else (get_settings(env,FA,key),get_settings(env,FA,key))
    expected_provider=A if changed else X
    before_calls=len(starts)
    command(env,mode)
    writers=[r for r in assignments(env,key) if r['stage']=='result']
    assert len(writers)==2
    second=next(r for r in writers if r['attempt_id']!=first['attempt_id'])
    assert second['provider_id']==expected_provider and first in writers
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    new_job=next(j for j in env['jobs'] if (j.get('assignment_ref') or {}).get('attempt_id')==second['attempt_id'])
    ai_node_starter.start_ai_invoke(new_job)
    [call]=[c for c in starts[before_calls:] if not c.get('document_review_loop')]
    assert call['provider_id']==expected_provider and call['graph_attempt_id']==second['attempt_id']
    assert all(row in history(env)[table] for table,rows in frozen.items() for row in rows)
    old_attempt=_sql(env['db'],'SELECT * FROM graph_node_attempts WHERE attempt_id=?',(first['attempt_id'],))[0]
    assert old_attempt['status']=='invalidated'
    (tmp_path/'writer-evidence.json').write_text(json.dumps({
        'failure':failure,'mode':mode,'old_version':old['settings_version'],'new_version':new['settings_version'],
        'old_assignment':first,'new_assignment':second,'actual_provider_id':call['provider_id']},indent=2),encoding='utf-8')


@pytest.mark.parametrize('mode',['step','full'])
def test_instruction_verdict_hold_resolves_current_instruction_reworker_and_limit(
        env,decided,monkeypatch,starts,mode):
    from test_fg_ai_roles_l2_assignment import record_starts
    from test_fg_ai_roles_l2_review import _ai_writes_instruction,_review_end
    key=flow(env,extra_settings=(('instruction_review_count',3),))
    roles(key,instruction=X,execution=A,review=B)
    command(env,mode)
    instruction=_ai_writes_instruction(env,monkeypatch,provider=X)
    record_starts(monkeypatch,starts)
    first=one(env,key,'instruction_review',1)
    assert first['review_rounds']==3 and first['rework_provider_id']==X
    from test_fg_card_intervention_assigned_0119 import _loop_row,_loop_end
    from test_fg_card_settings_review import REVIEW_RUN
    _loop_row(instruction,REVIEW_RUN,'review')
    _loop_end(env,first['intent_exec_id'],instruction,REVIEW_RUN,'review_verdict_hold')
    _read(env)
    frozen=dict(one(env,key,'instruction_review',1))
    for field,value in [('instruction_role',{'kind':'assigned','provider_id':Y}),
                        ('instruction_review_ai',A),('instruction_review_count',1)]:
        save(env,key,field,value)
    calls=len(_loops(starts))
    command(env,mode)
    second=one(env,key,'instruction_review',2)
    assert second and (second['provider_id'],second['rework_provider_id'],second['review_rounds'])==(A,Y,1)
    assert second['attempt_id']==first['attempt_id']
    assert one(env,key,'instruction_review',1)==frozen
    [call]=_loops(starts)[calls:]
    assert call['doc_ref']==instruction
    assert (call['document_review_loop']['reviewer_provider_id'],call['document_review_loop']['rework_provider_id'])==(A,Y)
    assert node_run(env,key)['stage']=='instruction' and one(env,key,'result') is None


@pytest.mark.parametrize('mode',['step','full'])
def test_changed_review_to_no_review_drives_completion_without_old_ai(env,decided,monkeypatch,starts,mode):
    key,_,result,_=_spent(env,monkeypatch,starts,mode,follow=False)
    save(env,key,'review_role',{'kind':'not_used'})
    count=len(_loops(starts))
    command(env,mode)
    assert one(env,key,'result_review',2)['actor']=='no_review'
    assert len(_loops(starts))==count
    assert node_run(env,key)['status']=='done'

@pytest.mark.parametrize('mode',['step','full'])
def test_command_recovers_lost_loop_end_once_and_fences_old_callback(
        env,decided,monkeypatch,starts,mode):
    from flowgate.card_settings import assigned_review
    from test_fg_card_settings_review import REVIEW_RUN
    key,_,result,_=_spent(env,monkeypatch,starts,mode,follow=False)
    first=one(env,key,'result_review',1)
    # The durable loop stopped; its callback was lost before the assignment verdict committed.
    _sql(env['db'],"UPDATE graph_stage_assignments SET stage_state='active', verdict=NULL "
                   "WHERE attempt_id=? AND stage='result_review' AND seq=1",(first['attempt_id'],))
    new_settings(env,key)
    count=len(_loops(starts))
    command(env,mode)
    second=one(env,key,'result_review',2)
    assert second and (second['provider_id'],second['rework_provider_id'],second['review_rounds'])==(Y,A,1)
    assert len(_loops(starts))==count+1
    if mode=='step': assert json.loads(_run(env)['step_node_keys'])==[key]
    frozen=history(env)
    waits=_sql(env['db'],"SELECT * FROM graph_run_events WHERE cause='auto_approval_skipped'")
    assigned_review.after_loop_end(FA,{'run_id':REVIEW_RUN},(first['attempt_id'],'result_review',1),False)
    assert history(env)==frozen
    assert _sql(env['db'],"SELECT * FROM graph_run_events WHERE cause='auto_approval_skipped'")==waits
    assert len(_loops(starts))==count+1

@pytest.mark.parametrize('mode',['step','full'])
def test_explicit_command_repairs_exhausted_approval_retries_without_repeating_ai(
        env,decided,monkeypatch,starts,mode):
    from flowgate.card_settings import legacy_adapter
    from test_fg_command_rerequest_0124 import _refused,LIMIT
    key,follower,result,_=_spent(env,monkeypatch,starts,mode)
    command(env,mode)
    monkeypatch.setattr(legacy_adapter,'completion_gates',_refused)
    _pass(env,key,2,result,'air_0126_approval_failure')
    for _ in range(LIMIT+2): _read(env)
    assert node_run(env,key)['status']=='review'
    assert one(env,key,'result_review',2)['verdict']=='passed'
    before=history(env)
    loops=len(_loops(starts))
    new_settings(env,key)
    monkeypatch.setattr(legacy_adapter,'completion_gates',lambda doc:None)
    command(env,mode)
    assert node_run(env,key)['status']=='done'
    assert node_run(env,follower)['status']==('pending' if mode=='step' else 'open')
    assert len(_loops(starts))==loops
    after=history(env)
    assert all(row in after[table] for table,rows in before.items() for row in rows)
    assert len(_reviews(env,key))==2  # full may append the successor's person assignment


@pytest.mark.parametrize('mode', ['step', 'full'])
@pytest.mark.parametrize('evidence', ['asked', 'reviewed'])
def test_pre_t5_assignment_without_version_keeps_q544_revision_guard(
        env, decided, monkeypatch, starts, mode, evidence):
    from flowgate.card_settings import stage_assignment as sa
    from test_fg_command_review_0125 import _loop_row, _loop_end, _review_row, _revised

    key, _, result, revision = _spent(env, monkeypatch, starts, mode, follow=False)
    # This existing card saved settings before T#5: its version is already positive.
    _, settings = new_settings(env, key)
    if evidence == 'asked':
        command(env, mode)
        current = one(env, key, 'result_review', 2)
        _loop_row(result, 'air_pre_t5_asked', 'rework')
        _loop_end(env, current['intent_exec_id'], result, 'air_pre_t5_asked', 'review_count_exhausted')
    else:
        current = one(env, key, 'result_review', 1)
        _review_row(result, revision, 'issues', 'air_pre_t5_reviewed')
    # Only the isolated fixture's event loses the newly introduced field; assignment/history stay intact.
    rows = _sql(env['db'], "SELECT event_seq, payload_json FROM graph_run_events WHERE cause='stage_assigned'")
    matched = 0
    for row in rows:
        payload = json.loads(row['payload_json'])
        if (payload.get('attempt_id'), payload.get('stage'), payload.get('seq')) == (
                current['attempt_id'], current['stage'], current['seq']):
            payload.pop('settings_version')
            _sql(env['db'], 'UPDATE graph_run_events SET payload_json=? WHERE event_seq=? AND run_id=?',
                 (json.dumps(payload), row['event_seq'], current['run_id']))
            matched += 1
    assert matched == 1 and sa.assigned_settings_version(current) is None
    assert settings['settings_version'] > 0
    frozen, calls = history(env), len(_loops(starts))
    for _ in range(2):
        _read(env)
        command(env, mode)
        assert history(env) == frozen and len(_loops(starts)) == calls
    # Unknown version preserves the old guard, but does not block a new unreviewed revision.
    _revised(result, revision + 1)
    _read(env)
    command(env, mode)
    assert len(_reviews(env, key)) == len([r for r in frozen['graph_stage_assignments']
                                        if r['stage'] == 'result_review']) + 1
    assert len(_loops(starts)) == calls + 1
    assert _reviews(env, key)[-1]['basis_revision_no'] == revision + 1


@pytest.mark.parametrize('mode', ['step', 'full'])
def test_known_changed_version_can_reask_the_same_revision(env, decided, monkeypatch, starts, mode):
    from test_fg_command_review_0125 import _loop_row, _loop_end
    key, _, result, revision = _spent(env, monkeypatch, starts, mode, follow=False)
    command(env, mode)
    second = one(env, key, 'result_review', 2)
    _loop_row(result, 'air_known_version', 'rework')
    _loop_end(env, second['intent_exec_id'], result, 'air_known_version', 'review_count_exhausted')
    _read(env)
    command(env, mode)
    assert len(_reviews(env, key)) == 2
    new_settings(env, key)
    command(env, mode)
    assert len(_reviews(env, key)) == 3
    assert one(env, key, 'result_review', 3)['basis_revision_no'] == revision


@pytest.mark.parametrize('mode', ['step', 'full'])
@pytest.mark.parametrize('winner', ['automatic', 'command'])
@pytest.mark.parametrize('reason', ['review_not_started', 'review_result_unknown', 'approval_refused', 'stage_not_person'])
def test_system_error_command_and_stale_automatic_retry_open_one_seq(
        env, decided, monkeypatch, starts, mode, winner, reason):
    """Both serialized lock orders, even when the winner's new start fails (no active-work guard)."""
    from flowgate.card_settings import assigned_review as ar, legacy_adapter as legacy
    from flowgate.graph_run import command as commands
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store
    from test_fg_command_rerequest_0124 import _refuse_starts, _spent as same_submission_spent

    key, _, _, result, first = same_submission_spent(env, monkeypatch, starts, stop='total_timeout')
    # A failed start of this submission, with no needs-intervention barrier from the spent fixture.
    _sql(env['db'], "DELETE FROM graph_run_events WHERE cause='auto_approval_skipped'")
    _sql(env['db'], "UPDATE graph_stage_assignments SET stage_state='failed', verdict=NULL, "
                   "fail_cause='review_not_started' WHERE attempt_id=? AND stage='result_review'",
         (first['attempt_id'],))
    _reason(env, key, result, first, reason)
    stale = one(env, key, 'result_review', 1)
    assert ar.review_retry_due(stale)
    frozen = dict(stale)

    def begin_command():
        old = legacy.active_run(FA)
        assert run_store.set_run_status(old['run_id'], 'idle', 'running') == 1
        ctx = run_apply.load_context({**old, 'status': 'running'}, 'flowgate', 'u_kim', [])
        commands.record_command(ctx, mode)
        run_apply.emit_run_status(ctx, 'idle', 'running', 'user_command')
        assert run_store.set_run_command(old['run_id'], mode, [key] if mode == 'step' else None) == 1
        return legacy.run_by_id(old['run_id'])

    run = with_flow_guard(FA, begin_command)
    outside = legacy.node_run(run['run_id'], key)
    assert ar.rerequest_due(FA, run, outside, None) is not None
    refused, _ = _refuse_starts(monkeypatch)

    def automatic():
        jobs = []
        ar.retry_review_stage(FA, stale, ar.HELD, jobs)
        ar._run_jobs(jobs)

    def continuation():
        ar._command_review_row(FA, run, outside)

    if winner == 'automatic':
        automatic()
        continuation()
    else:
        continuation()
        automatic()
    assert one(env, key, 'result_review', 1) == frozen
    rows = _reviews(env, key)
    assert [r['seq'] for r in rows] == [1, 2]
    assert rows[-1]['stage_state'] == 'failed'
    from flowgate.card_settings import stage_assignment as sa
    assert sa.basis_of(rows[0]) == sa.basis_of(rows[1]) == sa.mark(_doc(result))
    requests = [r for r in ledger(env) if r['assignment_stage'] == 'result_review' and r['assignment_seq'] == 2]
    assert len(requests) == 1 and len(refused) == 1
    assert ar.review_start_tries(rows[-1]) <= 2
