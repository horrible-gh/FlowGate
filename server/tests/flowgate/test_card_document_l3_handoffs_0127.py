"""0010-L 5.7/5.8: chat reads stay valid only until another authorized save.

These use the same isolated DB observer as the route matrix. Legacy entrypoints
are invoked directly where their HTTP authentication is already covered by the
composition regression suite.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from l3verify_support_0127 import (l3,kind,PROJECT,FLOW,USER,unchanged,code,documents)
from modules.flow_gate.db import document_revisions
from modules.flow_gate.documents import body_recovery
from modules.flow_gate.documents.routers import documents as routes
from modules.flow_gate.storage import paths
from flowgate.card_settings import legacy_adapter
from flowgate.card_document_edit import deployment_bridge
from flowgate.work_plan_deploy import service as deploy, protection
from modules.flow_gate.graph_workflow.services import flow_guard


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_L2W01_chat_read_two_PATCH_aliases_refused_then_chat_saves(l3,monkeypatch,via):
    assert l3.request(via,editing=False)[1]['revision_no']==7
    monkeypatch.setattr(routes,'_reject_if_group_disposed',lambda _:None)
    monkeypatch.setattr(routes,'_reject_if_group_ai_running',lambda _:None)
    monkeypatch.setattr(routes,'_save_target',lambda _:pytest.fail('PATCH reached file preparation'))
    unwrap=lambda fn:getattr(fn,'__wrapped__',fn)
    calls=[lambda:unwrap(routes.update_document_content)(l3.doc_id,routes.DocumentContentUpdate(content='bad'),{'user_id':USER}),
           lambda:unwrap(routes.update_document_content_rpc)(routes.DocumentContentUpdateRpc(doc_id=l3.doc_id,content='bad'),{'user_id':USER})]
    for call in calls:
        before=l3.observe()
        with pytest.raises(HTTPException) as caught:call()
        assert caught.value.status_code==409 and caught.value.detail=='card_document_edit_required'
        unchanged(before,l3.observe())
    assert l3.request(via)[1]['revision_no']==8


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CA05_three_chat_revisions_restore_actual_latest_backup(l3,monkeypatch,via):
    contents=[l3.header+'l3verify '+str(n) for n in range(3)]
    for i,content in enumerate(contents):
        assert l3.request(via,body=l3.body(expected_revision=7+i,content=content,idempotency_key='l3verify-history-'+str(i)))[0]==200
    rows=document_revisions.list_by_doc(l3.doc_id)
    assert len(rows)==3 and {r['revision_no'] for r in rows}=={7,8,9}
    assert all(r['created_by']==USER and r['edit_reason']=='user_comment' for r in rows)
    backups={r['revision_no']:paths.resolve_storage_path(r['backup_path'],PROJECT).read_text(encoding='utf-8') for r in rows}
    assert backups=={7:l3.header+'l3verify original\n',8:contents[0],9:contents[1]}
    l3.path.unlink()
    monkeypatch.setattr(body_recovery,'recover_write_checks',lambda _:None)
    monkeypatch.setattr(body_recovery,'recorded_or_standard',lambda _:l3.path)
    # Discovery is isolated from configured storage roots; revision_source still
    # reads the real history row/file and verifies identity under the write guard.
    monkeypatch.setattr(body_recovery,'diagnose_doc',lambda *a:{'method':body_recovery.RESTORE_REVISION,
        'body_lost':'false','revision':backups[9]})
    recovered=body_recovery.recover_one(l3.doc_id,body_recovery.RESTORE_REVISION,USER)
    assert recovered['data']['revision_no']==11 and recovered['content']==contents[1]
    assert len(document_revisions.list_by_doc(l3.doc_id))==3
    assert l3.request(via,editing=False)[1]['content']==contents[1]
    before=l3.observe()
    result=l3.request(via,body=l3.body(expected_revision=10,idempotency_key='l3verify-stale-restore'))
    assert (result[0],code(result))==(409,'revision_conflict')
    unchanged(before,l3.observe())
    assert l3.request(via,body=l3.body(expected_revision=11,idempotency_key='l3verify-after-restore'))[1]['revision_no']==12


@pytest.mark.parametrize('chat',['C-API','C-CLI'])
@pytest.mark.parametrize('via',['A-CLI','A-API','A-CLI-ai','A-API-ai'])
def test_CA06_L2N03_designer_chat_agent_author_sequence(l3,chat,via):
    assert l3.request('D-v2')[0]==200
    assert legacy_adapter.written_by(l3.doc_id)['author']=='person'
    assert l3.request(chat,body=l3.body(expected_revision=8,idempotency_key='l3verify-chat-second'))[0]==200
    assert legacy_adapter.written_by(l3.doc_id)['author']=='unknown'
    assert l3.request(via,body=l3.body(expected_revision=9))[0]==200
    assert legacy_adapter.written_by(l3.doc_id)['author']==('ai' if via.endswith('-ai') else 'person')
    after=l3.observe()
    assert after['O2']['revision_no']==10 and len(after['O1'])==1 and len(after['O4'])==len(after['O5'])==3
    assert all(r['created_by']==USER for r in after['O4'])
    assert all(r['actor_user_id']==USER for r in after['O5'])
    assert [json.loads(r['metadata'])['origin'] for r in after['O5']]==['designer','chat','agent']


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('winner',['deployment','chat'])
@pytest.mark.parametrize('newline',['\n','\r\n','\r'],ids=['LF','CRLF','CR'])
def test_L2W09_chat_read_basis_and_actual_deployment_fill(l3,monkeypatch,via,winner,newline):
    # An existing untouched r0 instruction is deployable; it is not CREATE.
    l3.db._execute('UPDATE documents SET revision_no=0 WHERE doc_id=?',[l3.doc_id])
    original=l3.header.replace('\n',newline).encode('utf-8')
    l3.path.write_bytes(original)
    key=l3.node['node_key']
    state=SimpleNamespace(flow_id=FLOW,doc={'doc_id':FLOW+'.0001-WP','revision_no':1})
    instruction=SimpleNamespace(card_id='l3verify-instruction')
    settings={}
    monkeypatch.setattr(deployment_bridge.settings,'values',lambda *a:settings)
    monkeypatch.setattr(deployment_bridge.settings,'put',lambda f,k,field,value,user:settings.update({field:value}))
    monkeypatch.setattr(deployment_bridge.legacy,'node_started',lambda *a:False)
    monkeypatch.setattr(deploy,'_instruction_file',lambda *a:l3.header+'l3verify deployed')
    def mark(state,key,target,revision,doc_id,data,user):
        settings[deploy.SOURCE_FIELD]={'wp_doc_id':state.doc['doc_id'],'instruction_doc_id':doc_id,
            'instruction_digest':deployment_bridge.legacy.digest(data)}
    monkeypatch.setattr(deploy,'_mark_instruction',mark)
    row=deployment_bridge.repository.rows(FLOW,[key])[0]
    def verdict():
        doc=documents.get_by_id(l3.doc_id)
        return protection.judge(FLOW,row,{l3.doc_id:doc},started=False,deploy_mark=settings.get(deploy.SOURCE_FIELD))
    initial=verdict()
    assert initial.state==protection.UNTOUCHED,initial
    assert l3.request(via,editing=False)[1]['revision_no']==0
    def fill(v):
        run=SimpleNamespace(pending=[],restore_failed=[])
        flow_guard.with_flow_guard(FLOW,lambda:deployment_bridge.fill(state,key,instruction,v.doc,
            v.path,v.identity,v.text,{'l3verify-instruction':{}},USER,'en',run))
        deployment_bridge.finish(run)
    if winner=='deployment':
        fill(initial)
        # Deployment-only lineage allows another fill of the same existing row.
        second=verdict()
        assert second.state==protection.UNTOUCHED,second
        fill(second)
        before=l3.observe()
        result=l3.request(via,body=l3.body(expected_revision=0))
        assert (result[0],code(result))==(409,'revision_conflict')
        unchanged(before,l3.observe())
        assert before['O2']['revision_no']==2 and len(before['O4'])==len(before['O5'])==2
        first_backup=next(r for r in before['O4'] if r['revision_no']==0)
        assert paths.resolve_storage_path(first_backup['backup_path'],PROJECT).read_bytes()==original
    else:
        assert l3.request(via,body=l3.body(expected_revision=0))[0]==200
        before=l3.observe()
        with pytest.raises(deploy.DeployError) as caught:fill(initial)
        assert caught.value.code=='instruction_target_changed'
        unchanged(before,l3.observe())
        assert before['O2']['revision_no']==1 and len(before['O5'])==1


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('kind',['R'])
def test_L2W14_root_conversion_invalidates_chat_document_basis(l3,via,kind):
    from modules.flow_gate.documents import root_conversion
    from flowgate.card_documents import integration as line_integration
    line_integration.register()
    l3.slot='requirement'
    l3.db._execute("UPDATE graph_node_documents SET slot='requirement' WHERE doc_id=?",[l3.doc_id])
    # Number-swap files carry the document code, as real generated root files do.
    renamed=l3.path.with_name('l3verify-0002-R.md')
    l3.path.rename(renamed)
    l3.path=renamed
    l3.db._execute('UPDATE documents SET file_path=?,filename=? WHERE doc_id=?',
        [paths.to_storage_relative(renamed,PROJECT),renamed.name,l3.doc_id])
    old_id=l3.doc_id
    read=l3.request(via,editing=False)
    assert read[0]==200 and read[1]['revision_no']==7
    converted=root_conversion.execute_root_conversion(old_id,'B',USER)
    new_id=FLOW+'.0002-B'
    assert converted['data']['doc_id']==new_id and converted['data']['revision_no']==8
    assert documents.get_by_id(old_id) is None
    l3.doc_id=new_id
    l3.path=paths.resolve_storage_path(converted['data']['file_path'],PROJECT)
    before=l3.observe()
    stale=l3.request(via,body=l3.body(expected_doc_id=old_id,expected_revision=7))
    assert (stale[0],code(stale))==(409,'slot_document_changed')
    unchanged(before,l3.observe())
    assert len(before['O1'])==1 and len(before['O4'])==len(before['O5'])==1
