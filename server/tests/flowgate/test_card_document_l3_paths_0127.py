"""0010-L section 5: route matrix, rejection observations and synchronized races."""
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from l3verify_support_0127 import (l3, kind, PROJECT, FLOW, USER, RUN, TOKEN, PATHS, NO_WRITE,
    unchanged, code, emit, service, documents, tokens, contacts, chat_adapter, chat_authz, chat_gate,
    call_ledger, target, repository, guards, token_service, flow_guard)
from modules.flow_gate.db import document_revisions, user_chat_source_access
from modules.flow_gate.storage import paths
from flowgate.card_document_edit import boundary, store, idempotency
from flowgate.card_document_edit.errors import GraphApiError

ROUTES=[(p,k) for k in ('N','NR','WP','TR2') for p in PATHS if p not in ('WP','TR2') or p==k]


@pytest.mark.parametrize('via,kind',ROUTES)
def test_normal_and_reconnect_O1_to_O14(l3,via,kind):
    before=l3.observe()
    response=l3.request(via)
    assert response[0]==200,response
    after=l3.observe()
    assert after['O2']['doc_id']==l3.doc_id and after['O2']['revision_no']==8
    assert len(after['O1'])==len(before['O1'])==1
    for key in ('status','doc_review_status','owner_id','group_id'):
        assert after['O2'][key]==before['O2'][key],key
    assert len(after['O4'])==len(after['O5'])==1
    assert after['O4'][0]['created_by']==USER
    assert after['O5'][0]['actor_user_id']==USER
    assert after['O7']==before['O7'] and after['O6']==before['O6']
    if not via.startswith('A-'):
        assert after['O8']==before['O8']
    assert after['O9'].count('document')==after['O9'].count('canvas')==after['O9'].count('graph')==1
    assert not any('.keep' in name for name in after['O3'])
    assert l3.request('D-v2',editing=False)[1]['revision_no']==8
    assert l3.request('C-CLI',editing=False)[1]['content']==l3.path.read_text(encoding='utf-8')
    if kind not in ('WP','TR2'):
        assert paths.resolve_storage_path(after['O4'][0]['backup_path'],PROJECT).read_text(encoding='utf-8')==l3.header+'l3verify original\n'
    emit(l3,'normal',{'path':via,'kind':kind,'before':before,'after':after})


@pytest.mark.parametrize('via',['C-API','C-CLI','A-CLI','A-API','D-v2'])
@pytest.mark.parametrize('permission',['create_only','neither','foreign_project','update_only'])
def test_permission_matrix_issued_to_not_create(l3,monkeypatch,via,permission):
    calls=[]
    def allowed(user,project,perm):
        calls.append((user,project,perm))
        return perm=='perm_document_read' or (permission=='update_only' and perm=='perm_document_update') or (
            permission=='create_only' and perm=='perm_document_create') or project=='l3verify-foreign'
    monkeypatch.setattr(guards,'has_permission',allowed)
    before=l3.observe()
    result=l3.request(via)
    assert result[0]==(200 if permission=='update_only' else 403),result
    assert (USER,PROJECT,'perm_document_update') in calls
    if permission!='update_only':
        assert code(result)=='permission_denied'
        unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI','A-CLI','A-API','D-v2'])
@pytest.mark.parametrize('failure',['stale','unlinked','history','event','unreadable','binding_unavailable'])
def test_negative_observations(l3,monkeypatch,via,failure):
    body=l3.body()
    status,expected=500,'internal_error'
    def injected(*a,**k):
        raise RuntimeError('l3verify injected write failure')
    if failure=='stale':
        body['expected_revision']=6
        status,expected=409,'revision_conflict'
    elif failure=='unlinked':
        # Binding remains recorded; its current-document read returns no document.
        monkeypatch.setattr(target,'resolve_current',lambda *a:None)
        # A known coordinate remains known after its document was removed.
        status,expected=404,'no_document'
    elif failure=='history':
        monkeypatch.setattr(document_revisions,'create_once',injected)
    elif failure=='event':
        monkeypatch.setattr(store.workflow_events,'create',injected)
    elif failure=='unreadable':
        l3.path.unlink()
        status,expected=422,'not_editable'
    else:
        monkeypatch.setattr(target.repository,'available',lambda:False)
        status,expected=422,'card_binding_missing'
    before=l3.observe()
    result=l3.request(via,body=body)
    assert (result[0],code(result))==(status,expected),result
    unchanged(before,l3.observe())
    assert chat_gate.state(RUN)[1]==0


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('mode',[None,'read','read_write'])
def test_CM01_CM02_CM10_CM11_CM13_CM15(l3,monkeypatch,via,mode):
    l3.db._execute('UPDATE tokens SET source_access=? WHERE token_id=?',[mode,TOKEN])
    assert l3.request(via,editing=False)[0]==200
    monkeypatch.setattr(guards,'has_permission',lambda *a:False)
    before=l3.observe()
    result=l3.request(via)
    assert result[0]==403
    assert code(result)==('permission_denied' if mode=='read_write' else 'chat_document_edit_mode_denied')
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CM03_CM09_three_edits_do_not_touch_committed_claim(l3,via):
    before=l3.observe()
    for revision in (7,8,9):
        result=l3.request(via,body=l3.body(expected_revision=revision,idempotency_key=f'l3verify-{revision}',content=l3.header+str(revision)))
        assert result[0]==200,result
    after=l3.observe()
    assert after['O2']['revision_no']==10 and len(after['O4'])==3
    assert before['O7']==after['O7'] and before['O8']==after['O8']
    assert all(r['created_by']==USER and r['edit_reason']=='user_comment' for r in after['O4'])
    # A subsequent read-only token cannot inherit this run's edit kind.
    l3.db._execute("UPDATE tokens SET source_access='read' WHERE token_id=?",[TOKEN])
    before=l3.observe()
    assert code(l3.request(via,body=l3.body(expected_revision=10)))=='chat_document_edit_mode_denied'
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('case', ['cancelled','finished','consumed','revoked','expired','missing_id','missing_row','binding'])
@pytest.mark.parametrize('editing',[False,True])
def test_CB_CG_CO_authentication_before_input(l3,via,case,editing):
    status,expected=401,'unauthorized'
    if case in ('cancelled','finished'):
        chat_gate.close(RUN,case,0)
        status,expected=409,'chat_document_run_'+case
    elif case in ('consumed','revoked'):
        l3.db._execute('UPDATE tokens SET '+case+"_at='t' WHERE token_id=?",[TOKEN])
    elif case=='expired':
        l3.db._execute("UPDATE tokens SET expires_at='2000-01-01' WHERE token_id=?",[TOKEN])
    elif case=='missing_id':
        l3.run.update(current_token_id=None,token_id=None)
        status,expected=403,'chat_document_forbidden'
    elif case=='missing_row':
        l3.db._execute('DELETE FROM tokens WHERE token_id=?',[TOKEN])
    elif case=='binding':
        l3.run['doc_ref']='l3verify-foreign'
        status,expected=403,'chat_document_forbidden'
    else:
        l3.db._execute("UPDATE user_chat_source_access SET one_shot_token_id=?,one_shot_claimed_at='t' WHERE user_id=?",[TOKEN,USER])
        status,expected=403,'chat_document_forbidden'
    before=l3.observe()
    result=l3.request(via,editing=editing,body=l3.body(expected_revision=False,actor='forged'))
    assert (result[0],code(result))==(status,expected),result
    assert result[1]['error']['params']==({'run_id':RUN} if status==409 else {})
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('field',['actor','user_id','origin','role','validate_only'])
def test_CA02_unknown_field_no_write(l3,via,field):
    before=l3.observe()
    result=l3.request(via,body=l3.body(**{field:'forged'}))
    assert (result[0],code(result))==(422,'invalid_request')
    assert result[1]['error']['params']['errors']==[{'field':field,'reason':'unknown_field'}]
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CM12_pending_claim_refuses_valid_edit(l3,via):
    l3.db._execute("UPDATE user_chat_source_access SET one_shot_token_id=?,one_shot_claimed_at='t' WHERE user_id=?",[TOKEN,USER])
    before=l3.observe()
    result=l3.request(via)
    assert (result[0],code(result))==(403,'chat_document_forbidden')
    assert result[1]['error']['params']=={}
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CI_replay_and_repeated_provider_call_zero(l3,via):
    assert l3.request(via)[0]==200
    before=l3.observe()
    result=l3.request(via,context=l3.contexts[-1] if via=='C-API' else None)
    assert result[0]==200 and result[1]['replayed'] is True
    unchanged(before,l3.observe(),('O1','O2','O3','O4','O5','O7','O8','O12'))
    assert [k for k,_ in l3.events]==['document','canvas','graph']*2
    second=l3.request(via,body=l3.body(expected_revision=8,idempotency_key='l3verify-second'))
    assert second[0]==200 and second[1]['replayed'] is False
    if via=='C-API':
        assert len(set(c.server_call_id for c in l3.contexts))==2


@pytest.mark.parametrize('change',['content','expected_revision','next_run','other_user'])
def test_CI10_CI11_no_cross_replay(l3,change):
    assert l3.request('C-CLI')[0]==200
    body=l3.body()
    expected='idempotency_key_reused'
    if change in ('next_run','other_user'):
        # Same raw key in a different run produces an independent effective key.
        new_run='l3verify-next-run'
        l3.run['run_id']=new_run
        l3.db._execute('UPDATE tokens SET ai_run_id=? WHERE token_id=?',[new_run,TOKEN])
        if change=='other_user':
            l3.run['issued_to']='l3verify-other'
            l3.db._execute('UPDATE tokens SET issued_to=? WHERE token_id=?',['l3verify-other',TOKEN])
        expected='revision_conflict'
    else:
        body[change]=l3.header+'different' if change=='content' else 8
    before=l3.observe()
    # Rebind fixture run resolver for the deliberately new run.
    if change in ('next_run','other_user'):
        from unittest.mock import patch
        with patch.object(chat_authz,'_get_run_record',lambda _:l3.run):
            result=l3.request('C-CLI',body=body)
    else:
        result=l3.request('C-CLI',body=body)
    assert (result[0],code(result))==(409,expected),result
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['A-CLI','A-API'])
def test_agent_commit_before_consume_failure_replays_once(l3,monkeypatch,via):
    consume=token_service.consume
    monkeypatch.setattr(token_service,'consume',lambda **kw:False)
    assert l3.request(via)[0]==500
    before=l3.observe()
    monkeypatch.setattr(token_service,'consume',consume)
    response=l3.request(via)
    assert response[0]==200 and response[1]['replayed'] is True,response
    unchanged(before,l3.observe(),('O1','O2','O3','O4','O5','O7','O12'))
    assert l3.request(via)[0]==401


@pytest.mark.parametrize('size',[15999,16000,16001])
def test_CR07_exact_serialized_boundary_all_observations(l3,size):
    l3.path.write_text(l3.header+'x',encoding='utf-8')
    response=l3.request('C-CLI',editing=False)[1]
    extra=size-len(json.dumps(response,ensure_ascii=False))
    l3.path.write_text(l3.header+'x'*(extra+1),encoding='utf-8')
    raw=l3.request('C-CLI',editing=False)[1]
    assert len(json.dumps(raw,ensure_ascii=False))==size
    before=l3.observe()
    response=l3.request('C-API',editing=False)
    assert response[0]==(200 if size<=16000 else 413),response
    if size>16000:
        assert code(response)=='result_too_large'
        assert response[1]['error']['params']['size_chars']==16001
        assert l3.request('C-API')[0]==413
    unchanged(before,l3.observe())


PAIRS=[('C-API','C-CLI','N'),('C-API','D-v2','N'),('C-CLI','D-v2','N'),
    ('C-API','A-CLI','N'),('C-CLI','A-API','N'),('C-API','A-API','N'),('C-CLI','A-CLI','N'),
    ('C-API','WP','WP'),('C-CLI','WP','WP'),
    ('C-API','TR2','TR2'),('C-CLI','TR2','TR2')]


@pytest.mark.parametrize('left,right,kind',PAIRS)
def test_XC_same_revision_exactly_one_winner(l3,monkeypatch,left,right,kind):
    original=boundary.enter
    barrier=threading.Barrier(2)
    def together(*a,**kw):
        barrier.wait(10)
        return original(*a,**kw)
    monkeypatch.setattr(boundary,'enter',together)
    before=l3.observe()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(l3.request,left)
        b=pool.submit(l3.request,right)
        responses=[a.result(30),b.result(30)]
    assert sorted(r[0] for r in responses)==[200,409],responses
    losing=next(r for r in responses if r[0]==409)
    assert code(losing) in ('revision_conflict','wp_revision_conflict','tr2_spec_changed'),losing
    after=l3.observe()
    assert after['O2']['revision_no']==8 and len(after['O4'])==len(after['O5'])==1
    assert len(after['O1'])==1 and not any('.keep' in name for name in after['O3'])
    assert after['O9'].count('graph')==1
    if right.startswith('A-') and responses[1][0]==409:
        assert tokens.get_by_id(l3.issued['token_id'])['consumed_at'] is None
    emit(l3,'race',{'paths':[left,right],'kind':kind,'responses':responses,'before':before,'after':after})


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('rollback',[False,True])
def test_CG02_drain_new_requests_and_rollback(l3,monkeypatch,via,rollback):
    entered,release,closed=(threading.Event() for _ in range(3))
    original=store.commit_revision
    def delayed(*a,**kw):
        entered.set()
        assert release.wait(10)
        if rollback:
            raise RuntimeError('l3verify rollback after gate entry')
        return original(*a,**kw)
    monkeypatch.setattr(store,'commit_revision',delayed)
    def close():
        result=chat_gate.close(RUN,'cancelled',10)
        closed.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        save=pool.submit(l3.request,via)
        assert entered.wait(10)
        cancel=pool.submit(close)
        deadline=time.monotonic()+5
        while chat_gate.state(RUN)[0]==chat_gate.OPEN and time.monotonic()<deadline:
            threading.Event().wait(.001)
        try:
            assert chat_gate.state(RUN)[0]=='CLOSED(cancelled)' and not closed.is_set()
            assert l3.run['status']=='running'
            # The transaction is held: do not read SQLite until releasing the writer.
            for path in ('C-API','C-CLI'):
                for editing in (False,True):
                    result=l3.request(path,editing=editing)
                    assert (result[0],code(result))==(409,'chat_document_run_cancelled'),result
                    assert result[1]['error']['params']=={'run_id':RUN}
        finally:
            release.set()
        response=save.result(15)
        cancel.result(15)
    assert response[0]==(500 if rollback else 200),response
    assert chat_gate.state(RUN)[1]==0
    assert documents.get_by_id(l3.doc_id)['revision_no']==(7 if rollback else 8)
    assert len(l3.observe()['O5'])==(0 if rollback else 1)
    for status in ('cancelling','finished'):
        l3.run['status']=status
        assert code(l3.request(via))=='chat_document_run_cancelled'


def test_CG08_thousand_synchronized_real_commits(l3,monkeypatch):
    """1000 actual revisions; every iteration overlaps close and commit via Events."""
    original=store.commit_revision
    entered,release=(threading.Event() for _ in range(2))
    starts=[]
    durations=[]
    announcements=[]
    original_check,original_release=chat_authz.chat_recheck,chat_gate.release
    def checked(*a,**kw):
        result=original_check(*a,**kw)
        starts.append(time.perf_counter())
        return result
    def released(ticket):
        if ticket is not None and not ticket.released:
            durations.append((time.perf_counter()-starts[-1])*1000)
        return original_release(ticket)
    monkeypatch.setattr(chat_authz,'chat_recheck',checked)
    monkeypatch.setattr(chat_gate,'release',released)
    def delayed(*a,**kw):
        entered.set()
        assert release.wait(10)
        return original(*a,**kw)
    monkeypatch.setattr(store,'commit_revision',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i in range(1000):
            chat_gate.reset()
            l3.run['status']='running'
            entered.clear();release.clear()
            save=pool.submit(l3.request,'C-CLI',body=l3.body(expected_revision=7+i,idempotency_key=f'l3verify-race-{i}',content=l3.header+str(i)))
            assert entered.wait(10)
            closing=pool.submit(chat_gate.close,RUN,'cancelled',10)
            deadline=time.monotonic()+5
            while chat_gate.state(RUN)[0]==chat_gate.OPEN and time.monotonic()<deadline:
                threading.Event().wait(.001)
            assert chat_gate.state(RUN)[0]=='CLOSED(cancelled)'
            release.set()
            result=save.result(15)
            closing.result(15)
            announcements.append(time.perf_counter())
            l3.run['status']='cancelling'
            assert result[0]==200,result
            assert code(l3.request('C-CLI'))=='chat_document_run_cancelled'
            assert chat_gate.state(RUN)[1]==0
    after=l3.observe()
    assert after['O2']['revision_no']==1007
    assert len(after['O4'])==len(after['O5'])==1000
    assert len(after['O1'])==1 and not any('.keep' in name for name in after['O3'])
    values=sorted(durations)
    assert len(values)==len(starts)==1000
    after_announce=sum(start>announced for start,announced in zip(starts,announcements))
    assert after_announce==0
    emit(l3,'CG08',{'iterations':1000,'successes':len(after['O5']),'post_announce_recheck_successes':after_announce,
                   'p99_ms':values[990],'maximum_ms':values[-1],
                   'drain_review_required':values[990]>chat_gate.CANCEL_DRAIN_SEC*500})


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_X6_first_commit_failure_releases_gate_and_second_wins(l3,monkeypatch,via):
    original_enter,original_store=boundary.enter,store.commit_revision
    ready=threading.Barrier(2)
    def together(*a,**kw):
        ready.wait(10)
        return original_enter(*a,**kw)
    calls=[]
    def fail_first(*a,**kw):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('l3verify first committer fails')
        return original_store(*a,**kw)
    monkeypatch.setattr(boundary,'enter',together)
    monkeypatch.setattr(store,'commit_revision',fail_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(l3.request,via)
        b=pool.submit(l3.request,'D-v2')
        results=[a.result(20),b.result(20)]
    assert sorted(r[0] for r in results)==[200,500]
    assert documents.get_by_id(l3.doc_id)['revision_no']==8
    assert len(l3.observe()['O4'])==len(l3.observe()['O5'])==1
    assert chat_gate.state(RUN)[1]==0


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CM05_simultaneous_claim_only_winner_can_save(l3,via):
    user_chat_source_access.upsert(USER,'edit_once','l3verify-t0')
    barrier=threading.Barrier(2)
    def claim(token):
        barrier.wait(10)
        return token,user_chat_source_access.claim(USER,token,'l3verify-t1','l3verify-t1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(claim,TOKEN)
        b=pool.submit(claim,l3.issued['token_id'])
        results=[a.result(10),b.result(10)]
    assert sorted(row['source_access'] for _,row in results)==['read','read_write']
    winner=next(token for token,row in results if row['one_shot_claimed'])
    assert user_chat_source_access.commit(USER,winner,'l3verify-t2')=='COMMITTED'
    before=l3.observe()
    for _,claim_result in results:
        l3.db._execute('UPDATE tokens SET source_access=? WHERE token_id=?',[claim_result['source_access'],TOKEN])
        response=l3.request(via)
        assert response[0]==(200 if claim_result['one_shot_claimed'] else 403)
    assert l3.observe()['O7']==before['O7']


def test_CM08_upsert_clears_pending_handoff_without_rearming(l3):
    user_chat_source_access.upsert(USER,'edit_once','l3verify-t0')
    assert user_chat_source_access.claim(USER,TOKEN,'l3verify-t1','l3verify-t1')['one_shot_claimed']
    user_chat_source_access.upsert(USER,'read_only','l3verify-t2')
    before=l3.observe()
    assert user_chat_source_access.commit(USER,TOKEN,'l3verify-t3')=='ALREADY_CLEARED'
    assert user_chat_source_access.rollback(TOKEN,'l3verify-t4') is False
    unchanged(before,l3.observe())


def test_CR11_other_path_grows_current_body_size_precedes_revision(l3):
    assert l3.request('C-API',editing=False)[0]==200
    assert l3.request('D-v2',body=l3.body(content=l3.header+'x'*16001))[0]==200
    before=l3.observe()
    response=l3.request('C-API')
    assert (response[0],code(response))==(413,'result_too_large')
    unchanged(before,l3.observe())
    assert l3.request('C-CLI',body=l3.body(expected_revision=8,idempotency_key='l3verify-large-cli'))[0]==200


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_F6_notification_exception_keeps_commit_and_reconnect(l3,monkeypatch,via):
    def broken(*a,**kw):raise RuntimeError('l3verify notification failure')
    monkeypatch.setattr(contacts,'broadcast_event_threadsafe',broken)
    monkeypatch.setattr(contacts.canvas_events,'publish_canvas_changed',broken)
    monkeypatch.setattr(contacts.v01_contact,'after_inbox_edited',broken)
    response=l3.request(via)
    assert response[0]==200 and response[1]['revision_no']==8
    assert l3.request('D-v2',editing=False)[1]['revision_no']==8
    assert len(l3.observe()['O5'])==1
    assert len([m for m in l3.observe()['O11'] if 'notification failed' in m])==3


def test_X5_independent_processes_share_database_CAS(l3):
    import multiprocessing
    from l3verify_support_0127 import race_process
    ctx=multiprocessing.get_context('spawn')
    barrier,queue=ctx.Barrier(2),ctx.Queue()
    config={k:getattr(l3,k) for k in ('node','slot','doc_id','content','run')}
    config.update(db_path=str(l3.db_path),root=str(l3.root))
    children=[ctx.Process(target=race_process,args=(config,barrier,queue,via)) for via in ('C-API','D-v2')]
    try:
        for child in children:child.start()
        results=[queue.get(timeout=60),queue.get(timeout=60)]
        for child in children:child.join(30)
        assert all(child.exitcode==0 for child in children)
        assert all('error' not in row for row in results),results
        assert len({row['pid'] for row in results})==2
        assert sorted(row['result'][0] for row in results)==[200,409],results
        after=l3.observe()
        assert after['O2']['revision_no']==8 and len(after['O4'])==len(after['O5'])==1
        assert not any('.keep' in name for name in after['O3'])
        emit(l3,'X5',results)
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(10)
        queue.close()


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('kind',['N','WP','TR2'])
@pytest.mark.parametrize('failure',['history','event','replace','commit','restore'])
def test_F1_F5_chat_atomicity_all_body_types(l3,monkeypatch,via,kind,failure):
    from contextlib import contextmanager
    from modules.flow_gate.storage import files
    before=l3.observe()
    def boom(*a,**kw):raise RuntimeError('l3verify '+failure)
    if failure=='history':monkeypatch.setattr(document_revisions,'create_once',boom)
    elif failure=='event':monkeypatch.setattr(store.workflow_events,'create',boom)
    elif failure=='replace':monkeypatch.setattr(files,'replace_restorably',boom)
    else:
        original=l3.db._transaction_frame
        failed=[False]
        @contextmanager
        def commit_failure():
            with original():
                yield
                if not failed[0]:
                    failed[0]=True
                    boom()
        monkeypatch.setattr(l3.db,'_transaction_frame',commit_failure)
        if failure=='restore':monkeypatch.setattr(files.ReplacedFile,'restore',lambda s:False)
    response=l3.request(via)
    assert (response[0],code(response))==(500,'internal_error'),response
    after=l3.observe()
    unchanged(before,after,tuple(k for k in NO_WRITE if k!='O3') if failure=='restore' else NO_WRITE)
    assert chat_gate.state(RUN)[1]==0
    if failure=='restore':
        assert any('.keep' in name for name in after['O3'])
        assert any('document_restore_incomplete' in message for message in after['O11'])
        assert l3.request(via)[0]==500


@pytest.mark.parametrize('case',['missing','deleted','fingerprint','full','empty_provider_id','old_handler'])
def test_CI04_to_CI08_ledger_refusal_observations(l3,case):
    from flowgate.card_document_edit import chat_tools
    inp={**l3.coords,**l3.body()}
    inp.pop('idempotency_key')
    ctx=call_ledger.accept_call(l3.run,{'id':'' if case=='empty_provider_id' else 'call_0',
        'name':chat_tools.REVISE_TOOL,'input':inp})
    ledger=l3.run[call_ledger.LEDGER_KEY]
    if case=='deleted':ledger.records.clear()
    if case=='fingerprint':
        ledger.records[ctx.server_call_id]=replace(ledger.records[ctx.server_call_id],input_sha256='0'*64)
    if case=='full':
        for _ in range(499):call_ledger.accept_call(l3.run,{'id':'call_0','name':chat_tools.READ_TOOL,'input':l3.coords})
        assert len(ledger.records)==500
        ctx=call_ledger.accept_call(l3.run,{'id':'overflow','name':chat_tools.REVISE_TOOL,'input':inp})
        assert ctx is None
    if case=='missing':ctx=None
    before=l3.observe()
    if case=='old_handler':
        result=chat_tools.stub_handler(l3.run,inp)
    else:
        result=chat_adapter.revise_card_document_call_handler(l3.run,ctx)
    # Section 5 uses the reason shorthand; 0010-L 2.5 and P#2 5.2 give
    # the normative full envelope, including invalid_request as the code.
    expected_errors=([{'field':'tool_call_id','reason':'required'}] if case=='empty_provider_id' else
        [{'field':'call_context','reason':'invalid_format','validation':{'reason':'call_identity_unavailable'}}])
    assert (result[0],code(result))==(422,'invalid_request'),result
    assert result[1]['error']['params']=={'errors':expected_errors}
    unchanged(before,l3.observe())
    assert len(l3.observe()['O13'])==len(before['O13'])


@pytest.mark.parametrize('via',['C-API','C-CLI'])
@pytest.mark.parametrize('body_state',['empty','frontmatter','missing','invalid_utf8'])
def test_R1_R4_read_never_invents_or_truncates_body(l3,via,body_state):
    content=b'' if body_state=='empty' else l3.header.encode() if body_state=='frontmatter' else b'\xff'
    if body_state=='missing':l3.path.unlink()
    else:l3.path.write_bytes(content)
    before=l3.observe()
    response=l3.request(via,editing=False)
    assert response[0]==200,response
    if body_state in ('empty','frontmatter'):
        assert response[1]['body_state']=='readable' and response[1]['content']==content.decode()
    else:
        assert response[1]['body_state'] in ('file_not_found','read_error') and response[1]['content'] is None
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_CM03_same_winning_run_edits_two_documents(l3,monkeypatch,via):
    from l3verify_support_0127 import Harness
    user_chat_source_access.upsert(USER,'edit_once','l3verify-t0')
    assert user_chat_source_access.claim(USER,TOKEN,'l3verify-t1','l3verify-t1')['one_shot_claimed']
    assert user_chat_source_access.commit(USER,TOKEN,'l3verify-t2')=='COMMITTED'
    second_doc=FLOW+'.0003-N'
    second_path=l3.root/'l3verify-second.md'
    second_header=l3.header.replace('0002-N','0003-N')
    second_path.write_text(second_header+'original',encoding='utf-8')
    documents.create({'doc_id':second_doc,'project_id':PROJECT,'module':'v02','group_id':FLOW,'type_code':'N',
        'seq':3,'title':'l3verify second','status':'draft','owner_id':USER,
        'file_path':paths.to_storage_relative(second_path,PROJECT)})
    l3.db._execute('UPDATE documents SET revision_no=7 WHERE doc_id=?',[second_doc])
    second_node={**l3.node,'node_key':'l3verify-second-card'}
    repository.insert(FLOW,second_node['node_key'],'instruction','N',second_doc,True,0,USER)
    monkeypatch.setattr(target.settings,'card_node',lambda f,k:second_node if k==second_node['node_key'] else l3.node)
    second=Harness(**{**vars(l3),'doc_id':second_doc,'path':second_path,'header':second_header,
        'content':second_header+'second saved','node':second_node})
    before=l3.observe()
    assert l3.request(via)[0]==200
    assert second.request(via,body=second.body(idempotency_key='l3verify-second-doc'))[0]==200
    assert l3.request(via,body=l3.body(expected_revision=8,idempotency_key='l3verify-first-again'))[0]==200
    after=l3.observe()
    assert documents.get_by_id(l3.doc_id)['revision_no']==9 and documents.get_by_id(second_doc)['revision_no']==8
    assert len(after['O1'])==2 and len(after['O4'])==len(after['O5'])==3
    assert before['O7']==after['O7'] and before['O8']==after['O8']


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_I1_I4_chat_effective_key_exact_match_past_120_candidates(l3,via):
    import itertools
    assert l3.request(via)[0]==200
    template=l3.db._fetch_one("SELECT * FROM workflow_events WHERE event_type='doc_edited'")
    metadata=json.loads(template['metadata'])
    key=metadata['idem_key']
    positions=[i for i,c in enumerate(key) if c.isalpha()][:8]
    variants=[]
    for switches in itertools.product((False,True),repeat=len(positions)):
        chars=list(key)
        for i,swap in zip(positions,switches):
            if swap:chars[i]=chars[i].swapcase()
        variant=''.join(chars)
        if variant!=key:variants.append(variant)
        if len(variants)==120:break
    assert len(variants)==120
    l3.db._execute("DELETE FROM workflow_events WHERE event_type='doc_edited'")
    for variant in [*variants,key]:
        store.workflow_events.create({**template,'metadata':idempotency.dumps({**metadata,'idem_key':variant})})
    assert len(idempotency.lookup(PROJECT,key))==1
    replay=l3.request(via,context=l3.contexts[0] if via=='C-API' else None)
    assert replay[0]==200 and replay[1]['replayed'] is True
    store.workflow_events.create({**template,'metadata':idempotency.dumps(metadata)})
    before=l3.observe()
    refused=l3.request(via,context=l3.contexts[0] if via=='C-API' else None)
    assert (refused[0],code(refused))==(409,'idempotency_key_reused')
    unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI'])
def test_X_recovery_finishes_before_waiting_chat_CAS(l3,monkeypatch,via):
    from modules.flow_gate.documents import body_recovery as recovery
    assert l3.request(via,editing=False)[1]['revision_no']==7
    l3.path.unlink()
    entered,release=threading.Event(),threading.Event()
    monkeypatch.setattr(recovery,'recover_write_checks',lambda doc:None)
    monkeypatch.setattr(recovery,'diagnose_doc',lambda *a:{'method':recovery.REGENERATE_STUB,'body_lost':'true','candidate':None})
    monkeypatch.setattr(recovery,'recorded_or_standard',lambda doc:l3.path)
    def build(doc):
        entered.set()
        assert release.wait(10)
        return (l3.header+'l3verify recovered').encode()
    monkeypatch.setattr(recovery,'stub_bytes',build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restoring=pool.submit(recovery.recover_one,l3.doc_id,recovery.REGENERATE_STUB,USER)
        assert entered.wait(10)
        editing=pool.submit(l3.request,via)
        release.set()
        assert restoring.result(20)['data']['revision_no']==8
        result=editing.result(20)
    assert (result[0],code(result))==(409,'revision_conflict')
    after=l3.observe()
    assert len(after['O1'])==len(after['O5'])==1 and len(after['O4'])==0
    assert l3.path.read_text(encoding='utf-8')==l3.header+'l3verify recovered'
    assert chat_gate.state(RUN)[1]==0
