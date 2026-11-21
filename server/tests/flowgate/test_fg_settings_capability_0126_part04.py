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
