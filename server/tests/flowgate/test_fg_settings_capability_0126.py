"""0126 T#4 A1/A2/A5: settings-only capability, actual HTTP/SQLite and durable history.

AI dispatch/JWT/SSE transport use the existing isolated recorders. No deployed flow is edited.
Display reasons, verdicts and document statuses are deliberately not node statuses.
"""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    FA, X, B, _sql, command, db_path, decided, env, flow, ledger, line_doc, node_run, roles, start,
    starts, wired, assignments,
)
from test_fg_card_settings import _h, get_settings, patch, node, edge, put_draft


def url(key):
    return f"/api/v1/flows/{FA}/nodes/{key}/settings"


def snapshot(env):
    tables = ['documents', 'document_revisions', 'graph_revisions', 'graph_revision_nodes', 'graph_revision_edges',
              'graph_node_attempts', 'graph_stage_assignments', 'document_reviews', 'graph_run_events', 'graph_attempt_runs',
              'graph_card_completions', 'graph_card_completion_decisions', 'graph_propagations', 'graph_document_decisions']
    present = {r['name'] for r in _sql(env['db'], "SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: _sql(env['db'], f'SELECT * FROM {t}') for t in tables if t in present}


def save(env, key, field, value, before=None, expect=200):
    before = before or get_settings(env, FA, key)
    return patch(env, FA, key, {'field': field, 'value': value,
                              'expected_settings_version': before['settings_version'],
                              'expected_context': before['context']}, expect=expect)


@pytest.mark.parametrize('label,status,doc_status', [
    ('needs_intervention', 'review', 'pending_review'),
    ('verdict_hold', 'review', 'pending_review'),
    ('review_count_exhausted', 'review', 'pending_review'),
    ('revised', 'review', 'revised'), ('rejected', 'review', 'rejected'),
    ('failed', 'open', None), ('system_error', 'open', None),
    ('human_wait', 'open', None), ('not_started', 'pending', None),
    ('done_reentry_preparation', 'done', 'approved'),
])
def test_a1_inactive_current_execution_revision_saves_roles_counts_and_allowed_settings(
        env, decided, starts, label, status, doc_status, tmp_path):
    key = flow(env)
    roles(key, 'not_used', 'not_used', 'not_used')
    run = start(env)
    # These are independent presentation/document facts, not status enum extensions.
    _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?', (status, key))
    report = line_doc(env, key, 'report')
    _sql(env['db'], 'UPDATE documents SET doc_review_status = ? WHERE doc_id = ?', (doc_status, report))
    from flowgate.card_settings import legacy_adapter
    if label in ('needs_intervention', 'verdict_hold', 'review_count_exhausted'):
        reason = {'needs_intervention': 'completion_missing', 'verdict_hold': 'review_not_passed:hold',
                  'review_count_exhausted': 'review_count_exhausted'}[label]
        legacy_adapter.record_approval_wait(FA, key, report, reason)
    if label in ('failed', 'system_error'):
        _sql(env['db'], 'UPDATE graph_node_runs SET dispatch_error_code = ? WHERE node_key = ?', (label, key))
    before = get_settings(env, FA, key)
    assert before['context'] == {'run_id': run['run_id'], 'graph_revision_no': 1, 'draft_version': None}
    assert before['locked'] is before['node_running'] is False
    assert before['settings_capability'] == {'editable': True, 'reason': None}
    history = snapshot(env)
    version = before['settings_version']
    for field, value in [('instruction_role', {'kind':'assigned', 'provider_id':X}),
                         ('execution_role', {'kind':'assigned', 'provider_id':B}),
                         ('review_role', {'kind':'assigned', 'provider_id':X}),
                         ('review_count', 3), ('instruction_review_count', 2),
                         ('restart', 1), ('time_limit', 120), ('review_basis', 'last')]:
        after = save(env, key, field, value)
        version += 1
        assert after['settings_version'] == version and after['locked'] is False
    # A new GET represents a new resource/session; it never dispatches or rewrites history.
    reloaded = env['client'].get(url(key), params={'context': json.dumps(before['context'])}, headers=_h()).json()
    assert reloaded['settings_version'] == version
    assert reloaded['fields']['review_count']['value'] == 3
    assert reloaded['fields']['instruction_review_count']['value'] == 2
    assert reloaded['ai_roles']['roles']['execution']['selected']['provider_id'] == B
    assert snapshot(env) == history and ledger(env) == [] and starts == []
    assert node_run(env, key)['status'] == status
    events = [e for e in env['events'] if e.payload.get('reason') == 'settings']
    assert events and events[-1].payload['node_keys'] == [key]
    (tmp_path / 'settings-evidence.json').write_text(json.dumps({
        'case': label, 'before': before, 'after': reloaded, 'history_unchanged': True}, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('status', ['requested', 'running'])
@pytest.mark.parametrize('display_status', ['open', 'review', 'done'])
def test_a2_real_execution_locks_exact_card_even_if_display_status_is_stale(env, decided, starts, status, display_status):
    key = flow(env)
    roles(key, X, 'not_used', 'not_used')
    start(env)
    [execution] = ledger(env)
    _sql(env['db'], 'UPDATE graph_attempt_runs SET status = ? WHERE exec_id = ?', (status, execution['exec_id']))
    _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?', (display_status, key))
    before = get_settings(env, FA, key)
    assert before['locked'] is before['node_running'] is True
    assert before['settings_capability']['editable'] is False
    history = snapshot(env)
    for field,value in [('review_count', 2), ('execution_role', {'kind':'not_used'}), ('instruction', 'omit')]:
        refused = save(env, key, field, value, before, expect=409)
        assert refused['error']['code'] == 'node_settings_locked'
    assert get_settings(env, FA, key)['settings_version'] == before['settings_version']
    assert snapshot(env) == history


@pytest.mark.parametrize('source', ['handoff', 'instruction_review', 'result_review', 'lease'])
def test_a2_valid_handoff_loop_and_card_mapped_lease_lock_only_while_live(env, decided, starts, monkeypatch, source):
    from flowgate.card_settings import legacy_adapter as legacy, review_stage
    key = flow(env)
    roles(key, 'not_used', 'not_used', 'not_used')
    start(env)
    if source == 'handoff':
        import time
        review_stage._handoffs[(FA,key)] = ('t4-handoff', time.monotonic() + 60)
    elif source == 'lease':
        from datetime import datetime, timedelta, timezone
        row = _sql(env['db'], 'SELECT t.ai_run_id FROM graph_run_token_map m JOIN tokens t ON t.token_id=m.token_id WHERE m.node_key=?', (key,))
        _sql(env['db'], 'UPDATE tokens SET ai_run_id=? WHERE token_id IN (SELECT token_id FROM graph_run_token_map WHERE node_key=?)', ('t4-lease',key))
        monkeypatch.setattr(legacy.db_leases, 'get', lambda f: {'run_id':'t4-lease', 'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()})
    else:
        doc = line_doc(env, key, 'instruction' if source == 'instruction_review' else 'report')
        monkeypatch.setattr(legacy, 'review_loop_active', lambda d: d == doc)
    before = get_settings(env, FA, key)
    assert before['node_running'] is before['locked'] is True
    assert save(env, key, 'review_count', 2, before, expect=409)['error']['code'] == 'node_settings_locked'
    if source == 'handoff':
        import time
        review_stage._handoffs[(FA,key)] = ('t4-handoff', time.monotonic() - 1)
    elif source == 'lease':
        monkeypatch.setattr(legacy.db_leases, 'get', lambda f: {'run_id':'t4-lease', 'expires_at':'2000-01-01T00:00:00+00:00'})
    else:
        monkeypatch.setattr(legacy, 'review_loop_active', lambda d: False)
    assert get_settings(env, FA, key)['locked'] is False
    assert save(env, key, 'review_count', 2)['fields']['review_count']['value'] == 2


def test_a5_other_parallel_branch_execution_does_not_lock_inactive_card(env, decided, starts, monkeypatch):
    from flowgate.graph_policies import fork
    from modules.flow_gate.graph_workflow.domain import policies
    validators = dict(policies._registered)
    validators.update({key: impl for key, _value, _version, impl in fork.SLOTS})
    monkeypatch.setattr(policies, '_registered', validators)
    first = put_draft(env, FA, 0, [node('tmp_r','R'),node('tmp_a'),node('tmp_b')],
                      [edge('tmp_ea','tmp_r','tmp_a'),edge('tmp_eb','tmp_r','tmp_b')])
    a,b = first['key_map']['tmp_a'],first['key_map']['tmp_b']
    roles(a, X, 'not_used', 'not_used'); roles(b, 'not_used','not_used','not_used')
    r=env['client'].post(f'/api/v1/flows/{FA}/graph/revisions',json={'draft_version':1},headers=_h())
    assert r.status_code == 201,r.text
    start(env)
    assert get_settings(env,FA,a)['locked'] is True
    before = snapshot(env)
    assert save(env,b,'review_count',2)['locked'] is False
    assert save(env,b,'execution_role',{'kind':'assigned','provider_id':B})['locked'] is False
    assert snapshot(env) == before and len(ledger(env)) == 1


def test_context_version_historical_unconfirmed_and_structure_protection(env, decided, starts):
    key=flow(env); roles(key,'not_used','not_used','not_used'); start(env)
    before=get_settings(env,FA,key)
    history=snapshot(env)
    for context in [dict(before['context'],run_id='old-run'),dict(before['context'],graph_revision_no=0),
                    dict(before['context'],graph_revision_no=2),{'run_id':None,'graph_revision_no':None,'draft_version':None}]:
        r=env['client'].get(url(key),params={'context':json.dumps(context)},headers=_h())
        # flowgate.v02.0130: the screen's view never locks the card; the answer names the server's context.
        assert r.status_code == 200 and r.json()['locked'] is False and r.json()['context'] == before['context']
        assert r.json()['context_matches'] is False
        bad=dict(before,context=context)
        assert save(env,key,'review_count',2,bad,expect=409)['error']['params']['reason']=='settings_context'
    after=save(env,key,'review_count',2)
    assert save(env,key,'review_count',3,before,expect=409)['error']['params']['reason']=='settings_version'
    assert save(env,key,'review_count',2)['settings_version']==after['settings_version']
    # Structure remains independently protected (the open node cannot be removed).
    from modules.flow_gate.graph_workflow.store import definition_store
    version=(definition_store.draft_header(FA) or {}).get('draft_version',0)
    r=env['client'].put(f'/api/v1/flows/{FA}/graph/draft',json={'draft_version':version,'nodes':[],'edges':[]},headers=_h())
    assert r.status_code==409 and r.json()['error']['code'] in ('graph_node_protected','graph_run_locked')
    assert snapshot(env)==history


def test_hold_permission_ownership_disposal_and_time_machine_fences(env, decided, starts, monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.graph_workflow.services import graph_service
    from flowgate.graph_time_machine import operation
    key=flow(env); roles(key,'not_used','not_used','not_used'); start(env)
    # Card Hold is separate from review verdict hold: changing AI never clears it or opens work.
    save(env,key,'hold',True)
    assert save(env,key,'review_count',3)['fields']['hold']['value'] is True
    assert ledger(env)==[]
    before=get_settings(env,FA,key)
    def fenced(*a,**kw):raise legacy.GraphApiError(409,'graph_time_machine_in_progress',{})
    with monkeypatch.context() as m:
        m.setattr(operation,'require_no_operation',fenced)
        assert get_settings(env,FA,key)['locked'] is True
        assert save(env,key,'review_count',2,before,expect=409)['error']['code']=='graph_time_machine_in_progress'
    with monkeypatch.context() as m:
        def deny(*a,**kw):raise legacy.GraphApiError(403,'forbidden',{})
        m.setattr(legacy,'authorize',deny)
        r=env['client'].patch(url(key),json={'field':'review_count','value':2},headers=_h())
        assert r.status_code==403
    with monkeypatch.context() as m:
        def conflict(*a,**kw):raise legacy.GraphApiError(409,'flow_ownership_conflict',{})
        m.setattr(legacy,'require_writable',conflict)
        assert save(env,key,'review_count',2,before,expect=409)['error']['code']=='flow_ownership_conflict'
    assert get_settings(env,FA,key)['settings_version']==before['settings_version']


@pytest.mark.parametrize('context',[None,{},False,{'run_id':None,'graph_revision_no':True,'draft_version':None}])
def test_malformed_explicit_context_is_refused(env,context):
    from test_fg_card_settings import placed,FB
    key=placed(env,FB)
    version=get_settings(env,FB,key)['settings_version']
    r=env['client'].patch(f'/api/v1/flows/{FB}/nodes/{key}/settings',json={
        'field':'review_count','value':2,'expected_context':context},headers=_h())
    assert r.status_code==422 and get_settings(env,FB,key)['settings_version']==version


def test_real_loopback_patch_get_new_client_and_no_automatic_dispatch(env, decided, starts, tmp_path):
    """Actual TCP HTTP against an isolated ASGI app, not a mocked settings transport."""
    import socket
    import threading
    import time
    import urllib.request
    import uvicorn
    key=flow(env); roles(key,'not_used','not_used','not_used'); start(env)
    sock=socket.socket(); sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(env['client'].app,log_level='error',lifespan='off'))
    worker=threading.Thread(target=server.run,kwargs={'sockets':[sock]})
    worker.start()
    evidence=[]
    def request(method,body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+url(key),method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={**_h(),'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as res:
            result=json.load(res)
            evidence.append({'method':method,'status':res.status,'request':body,'response':result})
            return result
    try:
        deadline=time.monotonic()+20
        while not server.started and worker.is_alive() and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        before=request('GET')
        history=snapshot(env)
        for name,value in [('instruction_role',{'kind':'assigned','provider_id':X}),
                           ('execution_role',{'kind':'assigned','provider_id':B}),
                           ('review_role',{'kind':'assigned','provider_id':X}),('review_count',3)]:
            before=request('PATCH',{'field':name,'value':value,'expected_context':before['context'],
                                    'expected_settings_version':before['settings_version']})
        fresh=request('GET')
        assert fresh['settings_version']==before['settings_version']
        assert fresh['fields']['review_count']['value']==3
        assert fresh['ai_roles']['roles']['execution']['selected']['provider_id']==B
        assert snapshot(env)==history and starts==[] and ledger(env)==[]
    finally:
        server.should_exit=True
        worker.join(timeout=30)
        sock.close()
        assert not worker.is_alive()
        (tmp_path/'http-settings-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')


def test_get_capability_and_patch_agree_on_read_only_permission(env, decided, starts, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    key=flow(env);roles(key,'not_used','not_used','not_used');start(env)
    before=get_settings(env,FA,key)
    monkeypatch.setattr(envelope,'has_permission',lambda u,p,perm:perm==envelope.READ_PERMISSION)
    current=get_settings(env,FA,key)
    assert current['locked'] is True and current['node_running'] is False
    assert current['settings_capability']=={'editable':False,'reason':'forbidden'}
    assert save(env,key,'review_count',2,before,expect=403)['error']['code']=='flow_access_denied'
    assert get_settings(env,FA,key)['settings_version']==before['settings_version']


def test_ownership_resolver_conflict_blocks_capability_and_write(env, decided, starts, monkeypatch):
    from modules.flow_gate.graph_workflow.services import engine_router
    key=flow(env);roles(key,'not_used','not_used','not_used');start(env)
    before=get_settings(env,FA,key)
    # Real resolver combines the real Graph assignment with a conflicting sequence read.
    monkeypatch.setattr(engine_router,'read_sequence',lambda f:({'doc_id':f+'.0001-R'},[]))
    assert engine_router.read_ownership(FA).state=='conflict'
    answer=get_settings(env,FA,key)
    assert answer['locked'] and answer['settings_capability']['reason']=='flow_ownership_conflict'
    assert save(env,key,'review_count',2,before,expect=409)['error']['code']=='flow_ownership_conflict'


def test_disposed_group_refuses_read_and_patch(env, decided, starts):
    from test_fg_card_settings import _doc
    key=flow(env);roles(key,'not_used','not_used','not_used');start(env)
    before=get_settings(env,FA,key)
    _doc(FA,99,'DC')
    # Read remains useful to explain the lock; it must not suggest writable capability.
    result=env['client'].get(url(key),headers=_h())
    if result.status_code==200:
        assert result.json()['locked'] and result.json()['settings_capability']['reason']=='flow_disposed'
    else:
        assert result.status_code==409 and result.json()['error']['code']=='flow_disposed'
    assert save(env,key,'review_count',2,before,expect=409)['error']['code']=='flow_disposed'


def test_before_run_draft_settings_context_becomes_stale_on_confirmation(env, decided):
    from test_fg_card_settings import placed,FB
    key=placed(env,FB)
    before=get_settings(env,FB,key)
    assert before['context']['draft_version']==1
    # Bump the draft through the actual graph route, with the same node identity.
    put_draft(env,FB,1,[node(key)])
    result=patch(env,FB,key,{'field':'review_count','value':2,'expected_context':before['context'],
                           'expected_settings_version':before['settings_version']},expect=409)
    assert result['error']['params']['reason']=='settings_context'
    assert get_settings(env,FB,key)['settings_version']==before['settings_version']



def test_nonempty_revision_review_assignment_completion_and_propagation_history_survives_settings(env, decided, starts, tmp_path):
    from test_fg_ai_roles_l2_assignment import save_instruction, write_line
    from modules.flow_gate.db import document_reviews, documents
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    key=flow(env); roles(key,'not_used','not_used','not_used'); start(env)
    save_instruction(env,key)
    write_line(env,key,'report','Saved report body for history preservation')
    assert node_run(env,key)['status']=='done'
    doc=line_doc(env,key,'report')
    with_flow_guard(FA,lambda:document_reviews.insert_review(doc,documents.get_by_id(doc)['revision_no'],X,
        'hold','[]','historical fixture verdict','2026-11-03T00:00:00+00:00',review_run_id='ended-fixture-review',actual_provider_id=X))
    backup=tmp_path/'past-revision.md';backup.write_text('Historical revision body',encoding='utf-8')
    _sql(env['db'],"INSERT INTO document_revisions (doc_id,revision_no,backup_path,edit_reason,created_by) VALUES (?,0,?,'worker_self','u_kim')",(doc,str(backup)))
    before=snapshot(env)
    for table in ['document_revisions','document_reviews','graph_stage_assignments','graph_card_completions','graph_propagations']:
        assert before[table],table
    for field,value in [('instruction_role',{'kind':'assigned','provider_id':X}),
                        ('execution_role',{'kind':'assigned','provider_id':B}),
                        ('review_role',{'kind':'assigned','provider_id':X}),('review_count',3)]:
        assert save(env,key,field,value)['locked'] is False
    assert snapshot(env)==before
    assert backup.read_text(encoding='utf-8')=='Historical revision body'
    assert ledger(env)==[] and starts==[]
    (tmp_path/'history-preserved.json').write_text(json.dumps({'counts':{t:len(rows) for t,rows in before.items()},
        'settings':get_settings(env,FA,key),'history_unchanged':True},ensure_ascii=False,indent=2),encoding='utf-8')



def test_an_existing_historical_revision_cannot_write_the_new_execution_revision(env, decided, starts):
    from modules.flow_gate.graph_workflow.store import definition_store
    key=flow(env)
    original=definition_store.revision(FA,1)
    root=next(n['node_key'] for n in original['nodes'] if n['doc_type']=='R')
    roles(key,'not_used','not_used','not_used')
    put_draft(env,FA,0,[node(root,'R'),node(key)],[edge('tmp_next',root,key)])
    confirmed=env['client'].post(f'/api/v1/flows/{FA}/graph/revisions',json={'draft_version':1,'base_revision_no':1},headers=_h())
    assert confirmed.status_code==201,confirmed.text
    res=env['client'].post(f'/api/v1/flows/{FA}/runs',json={'revision_no':2},headers=_h())
    assert res.status_code==201,res.text
    current=get_settings(env,FA,key)
    assert current['context']['graph_revision_no']==2
    historical=dict(current['context'],graph_revision_no=1)
    view=env['client'].get(url(key),params={'context':json.dumps(historical)},headers=_h())
    # flowgate.v02.0130: viewing a historical revision does not lock the card; writing from it still refuses.
    assert view.status_code==200 and view.json()['locked'] is False and view.json()['context']==current['context']
    refused=save(env,key,'review_count',3,dict(current,context=historical),expect=409)
    assert refused['error']['params']['reason']=='settings_context'
    assert save(env,key,'review_count',3)['fields']['review_count']['value']==3
    assert definition_store.revision(FA,1)==original
    assert ledger(env)==[] and starts==[]
