"""0126 T#2: real migrated SQLite/HTTP/Graph; only AI and SSE transports are recorded."""
import json
import pytest

from test_fg_document_decisions_0126 import (
    FA, FB, _h, _sql, db_path, decided, edge, env, legacy_cards, node, put_draft,
    decisions_env, _body, _get, _request, _post, _rows,
)
from test_fg_card_completion_0119 import completions, _command, _current
from test_fg_card_intervention_assigned_0119 import fork_join

pytestmark = pytest.mark.usefixtures('legacy_cards', 'completions')


def graph(env, names=('a', 'b'), links=None, ai=False):
    links = links or list(zip(('root', *names), names))
    assignee = {'kind': 'ai', 'provider_id': 'aip_card', 'user_id': None} if ai else None
    data = put_draft(env, FA, 0, [node('tmp_root', 'R')] + [node('tmp_'+n, 'T', assignee=assignee) for n in names],
                     [edge('tmp_e'+str(i), 'tmp_'+a, 'tmp_'+b) for i, (a,b) in enumerate(links)])
    keys = {name: data['key_map']['tmp_'+name] for name in names}
    for key in keys.values():
        for line in data['card_documents'][key]:
            if line['slot'] in ('instruction','report'):
                _body(line['doc_id'])
    return keys


def confirm(env):
    r=env['client'].post(f'/api/v1/flows/{FA}/graph/revisions', json={'draft_version':1}, headers=_h())
    assert r.status_code == 201, r.text


def approve(env, key, slot='report', request_key='approval'):
    return _post(env,key,_request(env,key,slot,request_key=request_key),slot)


def state(env,key):
    return _sql(env['db'],'SELECT * FROM graph_node_runs WHERE node_key = ?', (key,))[0]


def proof(env,key):
    records = _sql(env['db'],'SELECT * FROM graph_card_completions WHERE node_key = ?', (key,))
    assert len(records)==1
    rec=records[0]
    assert (rec['status'],rec['cause'],rec['actor_kind'],rec['actor_id'],rec['verdict_id']) == (
        'applied','human_approved','user','u_kim',None)
    evidence = _sql(env['db'],'SELECT * FROM graph_card_completion_decisions WHERE completion_id = ?', (rec['completion_id'],))
    assert len(evidence)==1
    decision = _sql(env['db'],'SELECT * FROM graph_document_decisions WHERE decision_id = ?', (evidence[0]['decision_id'],))[0]
    assert decision['doc_id']==rec['result_doc_id'] and decision['revision_no']==rec['revision_no']
    events=_sql(env['db'],"SELECT * FROM graph_run_events WHERE node_key = ? AND to_status = 'done'",(key,))
    assert len(events)==1 and json.loads(events[0]['payload_json'])['completion_id']==rec['completion_id']
    assert events[0]['event_seq']==rec['applied_event_seq']
    propagations=_sql(env['db'],'SELECT * FROM graph_propagations WHERE run_id = ? AND node_key = ?', (rec['run_id'],key))
    assert len(propagations)==1 and propagations[0]['source_event_seq']==rec['applied_event_seq']
    attempt=_sql(env['db'],'SELECT * FROM graph_node_attempts WHERE attempt_id = ?', (rec['attempt_id'],))[0]
    assert attempt['status']=='ended'
    from pathlib import Path
    evidence_path = Path(env['db']).with_name('human-evidence.jsonl')
    with evidence_path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({k: rec[k] for k in (
            'run_id','node_key','attempt_id','result_doc_id','revision_no','completion_id','cause','actor_id',
            'applied_event_seq','status')} | {'decision_id': evidence[0]['decision_id'],
                                            'propagation_status': propagations[0]['status']}) + '\n')
    return rec


@pytest.mark.parametrize('mode',['step','full'])
def test_b4_preapproved_zero_ai_and_one_completion_propagation(decisions_env,decided,mode):
    e=decisions_env; keys=graph(e,ai=True)
    for key in keys.values(): approve(e,key)
    assert _rows(e,'graph_runs')==[]
    confirm(e); _command(e,mode)
    assert state(e,keys['a'])['status']=='done'
    assert state(e,keys['b'])['status']==('done' if mode=='full' else 'pending')
    assert e['jobs']==[] and _rows(e,'graph_attempt_runs')==[] and _rows(e,'document_reviews')==[]
    proof(e,keys['a'])
    if mode=='step': _command(e,'step')
    for _ in range(3): _current(e)
    proof(e,keys['b']); proof(e,keys['a'])


def test_b2_instruction_only_starts_report_without_instruction_review(decisions_env,decided):
    e=decisions_env; keys=graph(e,names=('a',),ai=True); key=keys['a']
    report=_get(e,key)['documents']['report']['doc_id']; _body(report,'')
    approve(e,key,'instruction'); confirm(e); _command(e)
    assert [job.get('stage') for job in e['jobs']]==['result']
    assert not _sql(e['db'],"SELECT * FROM graph_stage_assignments WHERE stage IN ('instruction','instruction_review')")
    assert _rows(e,'graph_card_completions')==[]


def test_b6_pending_join_and_reference_do_not_complete_early(decisions_env,decided,fork_join):
    e=decisions_env; k=graph(e,names=('a','b','c','d'),links=[('root','a'),('a','b'),('a','c'),('b','d'),('c','d')])
    # A visible C -> B reference must not hold B behind C's unfinished work.
    bdoc=_get(e,k['b'])['documents']['report']['doc_id']
    cdoc=_get(e,k['c'])['documents']['report']['doc_id']
    _sql(e['db'],'UPDATE documents SET target_id = ? WHERE doc_id = ?', (cdoc,bdoc))
    for name in ('a','b','d'): approve(e,k[name])
    _body(_get(e,k['c'])['documents']['report']['doc_id'],'')
    confirm(e); _command(e)
    assert [state(e,k[n])['status'] for n in ('a','b','c','d')]==['done','done','open','pending']
    assert not _sql(e['db'],'SELECT * FROM graph_card_completions WHERE node_key = ?', (k['d'],))
    canvas=e['client'].get(f'/api/v1/flows/{FA}/canvas',headers=_h()).json()
    assert any(edge['edge_class']=='reference' and edge['edge_id']==f'ref:target:{cdoc}>{bdoc}'
               for edge in canvas['edges'])
    _body(_get(e,k['c'])['documents']['report']['doc_id'])
    approve(e,k['c']); _current(e)
    assert all(state(e,key)['status']=='done' for key in k.values())
    for key in k.values(): proof(e,key)


@pytest.mark.parametrize('control',['pause','stop'])
def test_b7_pause_stop_keep_intent_after_approval(decisions_env,decided,control):
    e=decisions_env; k=graph(e)
    _body(_get(e,k['a'])['documents']['report']['doc_id'],'')
    confirm(e); run=_command(e)
    # Inject a running interval before exercising the real pause/stop command.
    _sql(e['db'], "UPDATE graph_runs SET status = 'running', idle_reason = NULL WHERE run_id = ?", (run['run_id'],))
    r=e['client'].post(f"/api/v1/runs/{run['run_id']}/{control}",json={},headers=_h())
    assert r.status_code==200,r.text
    before=_current(e); jobs=len(e['jobs'])
    _body(_get(e,k['a'])['documents']['report']['doc_id'])
    approve(e,k['a']); after=_current(e)
    assert state(e,k['b'])['status']=='pending' and len(e['jobs'])==jobs
    assert after['status']==before['status']
    if control=='stop': assert after['idle_reason']=='stopped'


def test_b10_lost_postcommit_repaired_on_read_without_duplicates(decisions_env,decided,monkeypatch):
    from flowgate.card_completion import human
    e=decisions_env; k=graph(e)
    report=_get(e,k['a'])['documents']['report']['doc_id']; _body(report,'')
    confirm(e); _command(e)
    _body(report)
    real=human.refresh; monkeypatch.setattr(human,'refresh',lambda _flow:None)
    body=_request(e,k['a']); _post(e,k['a'],body)
    assert state(e,k['a'])['status']!='done'
    monkeypatch.setattr(human,'refresh',real)
    from concurrent.futures import ThreadPoolExecutor
    # Competing reads and receipt replay serialize through the real Flow guard.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(_current,e),pool.submit(_current,e),pool.submit(_post,e,k['a'],body)]
        for future in futures: future.result(timeout=30)
    proof(e,k['a'])
    _post(e,k['a'],body); _current(e); proof(e,k['a'])


def test_b12_changed_approved_body_never_skips_ai(decisions_env,decided):
    e=decisions_env; k=graph(e,names=('a',),ai=True); key=k['a']
    approve(e,key)
    _body(_get(e,key)['documents']['report']['doc_id'],'changed without revision increment')
    confirm(e); _command(e)
    assert not [r for r in _rows(e,'graph_card_completions') if r['cause']=='human_approved']
    assert _sql(e['db'], "SELECT * FROM graph_stage_assignments WHERE stage = 'result_review'")


def test_b10_precommit_binding_and_completion_fault_roll_back_together(decisions_env,decided,monkeypatch):
    from flowgate.card_completion import human,repository
    e=decisions_env; k=graph(e,names=('a',)); key=k['a']
    report=_get(e,key)['documents']['report']['doc_id']; _body(report,'')
    confirm(e); _command(e); _body(report)
    insert=repository.insert
    def fail(*a,**kw): raise RuntimeError('completion insert fault')
    monkeypatch.setattr(repository,'insert',fail)
    approve(e,key)
    assert state(e,key)['status']=='open' and _rows(e,'graph_card_completions')==[]
    assert not _sql(e['db'],"SELECT * FROM graph_run_events WHERE node_key = ? AND to_status = 'review'",(key,))
    monkeypatch.setattr(repository,'insert',insert)
    _current(e); proof(e,key)


def test_b9_new_approval_key_of_same_body_keeps_original_completion(decisions_env,decided):
    from flowgate.card_completion import completion
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.db import documents
    e=decisions_env; k=graph(e,names=('a',)); key=k['a']; approve(e,key); confirm(e); run=_command(e)
    rec=proof(e,key)
    approve(e,key,request_key='same-body-new-decision')
    assert proof(e,key)['completion_id']==rec['completion_id']
    from flowgate.card_completion import human,repository
    assert human.record_valid(repository.by_id(rec['completion_id']))


def test_b7_no_live_command_cannot_open_successor(decisions_env,decided):
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.store import run_store
    e=decisions_env; k=graph(e); report=_get(e,k['a'])['documents']['report']['doc_id']; _body(report,'')
    confirm(e); run=_command(e)
    def expire():
        ctx=run_apply.load_context(run_store.run_by_id(run['run_id']),'flowgate',None)
        run_store.set_run_status(ctx.run_id,'idle','paused')
        run_apply.emit_run_status(ctx,'idle','paused','user_pause')
        run_store.set_run_status(ctx.run_id,'paused','idle','no_targets'); ctx.run['idle_reason']='no_targets'
        run_apply.emit_run_status(ctx,'paused','idle','fixture_expired')
    with_flow_guard(FA,expire)
    _body(report); approve(e,k['a']); _current(e)
    assert state(e,k['a'])['status']=='done' and state(e,k['b'])['status']=='pending'
    assert e['jobs']==[]; proof(e,k['a'])


@pytest.mark.parametrize('paused',[False,True])
def test_b2_instruction_approval_of_open_attempt_skips_its_review(decisions_env,decided,paused):
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    e=decisions_env; k=graph(e,names=('a',),ai=True); key=k['a']
    docs=_get(e,key)['documents']; _body(docs['instruction']['doc_id'],''); _body(docs['report']['doc_id'],'')
    # A person's instruction wait, then an AI report. No instruction worker exists.
    from flowgate.ai_roles import role_store
    from test_fg_ai_roles_l2_assignment import roles
    roles(key,instruction='not_used',execution='aip_card',review='not_used')
    confirm(e); run=_command(e)
    if paused:
        _sql(e['db'],"UPDATE graph_runs SET status='running', idle_reason=NULL WHERE run_id=?",(run['run_id'],))
        r=e['client'].post(f"/api/v1/runs/{run['run_id']}/pause",json={},headers=_h()); assert r.status_code==200
    _body(docs['instruction']['doc_id']); approve(e,key,'instruction')
    if paused:
        _current(e); assert e['jobs']==[]
        _command(e)
    _current(e)
    assert [job.get('stage') for job in e['jobs']]==['result']
    assert not _sql(e['db'],"SELECT * FROM graph_stage_assignments WHERE stage='instruction_review'")
