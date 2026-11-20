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


from settings_retry_support_0126 import (history, new_settings, _submitted_ai_instruction, _instruction_reviews, _event_seqs)

@pytest.mark.parametrize('mode', ['full'])
@pytest.mark.parametrize('outcome', ['abnormal'])
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


