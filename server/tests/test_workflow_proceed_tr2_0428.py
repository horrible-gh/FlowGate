"""0428 T#2 connected TR2 command: real HTTP, SQLite migrations and Git source."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from test_tr2_connected_e2e_0565 import (
    store, repo, env, api, ready_tr2, view, approve, git, git_head, edit_spec, PROJECT,
)
from modules.flow_gate.db import documents, workflow_sequences as seqdb
from modules.flow_gate.db import tr2_approval_attempts as attempts
from modules.flow_gate.documents import tr2_approval_service as approval
from modules.flow_gate.services import workflow_proceed_service as command


def proceed(client, doc_id, *, request_key='confirmed:tr2'):
    doc=documents.get_by_id(doc_id)
    slot=seqdb.get_item_by_result_doc_id(doc_id)
    return client.post('/api/v1/documents/'+doc_id+'/proceed-unapproved',json={
        'expected_revision':doc['revision_no'],'expected_slot_id':slot['id'],
        'expected_slot_generation':slot['slot_generation'],'request_key':request_key})


def snapshot(env,doc_id):
    repo=env['repo']
    return dict(head=git_head(repo),status=git(repo,'status','--porcelain','--untracked-files=all'),
                files={str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*')
                       if p.is_file() and '.git' not in p.relative_to(repo).parts},
                document=documents.get_by_id(doc_id),attempts=attempts.list_by_doc(doc_id),
                ledger=env['store']._fetch_all('SELECT * FROM tr_commit_ledger',[]))


def forbid_approval(monkeypatch):
    for obj,name in [(approval,'approve'),(approval.adapter,'apply_all'),
                     (approval,'_run_validation'),(approval,'_commit_exact'),
                     (approval.db_ledger,'record_commit'),
                     (approval.test_command_service,'reflect_tr2_validation_success')]:
        monkeypatch.setattr(obj,name,MagicMock(side_effect=AssertionError(name+' invoked')))


@pytest.mark.parametrize('termination',['ready_to_apply','needs_more_work'])
def test_real_tr2_proceed_preserves_source_head_ledger_and_attempts(env,monkeypatch,termination):
    spec=edit_spec(termination=termination)
    group,t2,doc_id=ready_tr2('0428',spec)
    client=api()
    before=snapshot(env,doc_id)
    forbid_approval(monkeypatch)
    response=proceed(client,doc_id)
    assert response.status_code==200,response.text
    out=response.json()
    assert out['slot_completed'] and out['doc_review_status']=='pending_review'
    assert out['continuation_status']=='not_started' and out['run_id'] is None
    assert snapshot(env,doc_id)==before
    assert seqdb.get_effective_head(out['sequence_id'])['item_seq']==3
    assert proceed(client,doc_id).json()==out
    assert snapshot(env,doc_id)==before


def test_fully_rolled_back_validation_failure_can_proceed_without_new_apply(env,monkeypatch):
    group,t2,doc_id=ready_tr2('0429')
    client=api()
    original=(env['repo']/'src'/'app.txt').read_bytes()
    original_head=git_head(env['repo'])
    failure=approve(client,doc_id,1,'normal:validation-fails')
    assert failure.status_code==422 and failure.json()['code']=='tr2_validation_failed',failure.text
    rows=attempts.list_by_doc(doc_id)
    assert len(rows)==1 and rows[0]['state']=='failed' and rows[0]['result_code']=='recovered_rollback'
    assert (env['repo']/'src'/'app.txt').read_bytes()==original
    assert git_head(env['repo'])==original_head
    before=snapshot(env,doc_id)
    forbid_approval(monkeypatch)
    response=proceed(client,doc_id)
    assert response.status_code==200,response.text
    assert snapshot(env,doc_id)==before


def test_source_drift_and_source_lock_block_without_new_history(env):
    from group_lock_stub import hold_group_lock
    group,t2,doc_id=ready_tr2('0430')
    client=api()
    with hold_group_lock(PROJECT,group['group_id']):
        response=proceed(client,doc_id)
        assert response.status_code==409 and response.json()['error']['code']=='completion_source_locked'
    (env['repo']/'src'/'app.txt').write_bytes(b"greeting = 'hello'\ncount = 2\n")
    git(env['repo'],'commit','-am','different baseline')
    before=snapshot(env,doc_id)
    response=proceed(client,doc_id)
    assert response.status_code==409 and response.json()['error']['code']=='tr2_source_drift',response.text
    assert snapshot(env,doc_id)==before
    assert env['store']._fetch_one('SELECT COUNT(*) AS n FROM workflow_completion_decisions',[])['n']==0


def test_actual_apply_recovery_rows_and_revert_pending_are_not_skipped(env):
    group,t2,doc_id=ready_tr2('0431')
    client=api()
    body=view(client,doc_id)['body']
    attempt=attempts.create(doc=documents.get_by_id(doc_id),actor_user_id='usr_tr2e2e',
                            spec_fingerprint='a'*64,baseline_fingerprint=body['baseline_fingerprint'],
                            request_key='unfinished-apply')
    for state,code in [('in_progress','tr2_in_progress'),('recovery_required','tr2_recovery_required')]:
        attempts.update(attempt['attempt_id'],state=state,phase='rollback')
        before=snapshot(env,doc_id)
        response=proceed(client,doc_id)
        assert response.status_code==409 and response.json()['error']['code']==code,response.text
        assert snapshot(env,doc_id)==before
    attempts.finish(attempt['attempt_id'],state='failed',result_code='recovered_no_write')
    # Make a real normal approval commit, then emulate the explicit Time Machine
    # intermediate state (review reopened while its source commit remains live).
    (env['repo']/'gate.ok').write_bytes(b'ok')
    assert approve(client,doc_id,1,'normal:pass').status_code==200
    documents.update(doc_id,{'doc_review_status':'revised'})
    before=snapshot(env,doc_id)
    response=proceed(client,doc_id)
    assert response.status_code==409 and response.json()['error']['code']=='workflow_revert_pending',response.text
    assert snapshot(env,doc_id)==before
    assert env['store']._fetch_one('SELECT COUNT(*) AS n FROM workflow_completion_decisions',[])['n']==0



@pytest.mark.parametrize('validation_passes',[False,True])
def test_real_tr2_apply_invalidates_prior_tsr_even_after_rollback(env, validation_passes):
    from modules.flow_gate.db import workflow_completion as decisions
    from modules.flow_gate.services.workflow_completion_service import slot_completed
    group,t2,doc_id=ready_tr2('0432')
    client=api()
    seq=seqdb.get_sequence_by_doc_id(group['root'])
    ts=group['group_id']+'.0088-TS'
    tsr=group['group_id']+'.0089-TSR'
    for identity,kind,number,review in [(ts,'TS',88,'approved'),(tsr,'TSR',89,'rejected')]:
        documents.create({'doc_id':identity,'project_id':PROJECT,'module':'default',
                          'group_id':group['group_id'],'type_code':kind,'seq':number,
                          'title':'prior test evidence','revision_no':0})
        documents.update(identity,{'doc_review_status':review})
    documents.update(tsr,{'target_id':ts})
    seqdb.insert_sequence_item(seq['id'],-1,'TS','prior TS','doc',-2)
    seqdb.insert_sequence_item(seq['id'],0,'TSR','prior TSR','doc',-1)
    for row in seqdb.get_sequence_items(seq['id']):
        if row['item_seq']==-1: seqdb.set_item_result_doc_id(row['id'],ts)
        if row['item_seq']==0: seqdb.set_item_result_doc_id(row['id'],tsr)
    response=proceed(client,tsr,request_key='confirmed:prior-tsr')
    assert response.status_code==200,response.text
    decision_id=response.json()['decision_id']
    before=(env['repo']/'src'/'app.txt').read_bytes()
    if validation_passes:
        (env['repo']/'gate.ok').write_bytes(b'ok')
    result=approve(client,doc_id,1,'actual-apply-with-prior-tsr')
    assert result.status_code==(200 if validation_passes else 422),result.text
    if not validation_passes:
        assert (env['repo']/'src'/'app.txt').read_bytes()==before
    old=decisions.get_decision(decision_id)
    assert old['invalidated_at'] and old['invalidation_reason']=='tsr_source_basis_changed'
    row=next(r for r in seqdb.get_sequence_items(seq['id']) if r['result_doc_id']==tsr)
    assert row['slot_generation']>old['slot_generation'] and not slot_completed(row)
    assert documents.get_by_id(tsr)['doc_review_status']=='rejected'
