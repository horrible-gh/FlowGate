"""Independently selectable scenarios; shared fixtures remain in test_fg_settings_capability_0126.py."""
from __future__ import annotations

from test_fg_settings_capability_0126 import (
    B,
    FA,
    X,
    _h,
    _sql,
    assignments,
    command,
    db_path,
    decided,
    edge,
    env,
    flow,
    get_settings,
    json,
    ledger,
    line_doc,
    node,
    node_run,
    patch,
    put_draft,
    pytest,
    roles,
    save,
    snapshot,
    start,
    starts,
    url,
    wired,
)

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
