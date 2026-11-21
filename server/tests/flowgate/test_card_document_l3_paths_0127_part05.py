"""Independently selectable scenarios; shared fixtures remain in test_card_document_l3_paths_0127.py."""
from __future__ import annotations

from test_card_document_l3_paths_0127 import (
    FLOW,
    GraphApiError,
    NO_WRITE,
    PAIRS,
    PATHS,
    PROJECT,
    ROUTES,
    RUN,
    TOKEN,
    ThreadPoolExecutor,
    USER,
    boundary,
    call_ledger,
    chat_adapter,
    chat_authz,
    chat_gate,
    code,
    contacts,
    document_revisions,
    documents,
    emit,
    flow_guard,
    guards,
    idempotency,
    importlib,
    json,
    kind,
    l3,
    paths,
    pytest,
    replace,
    repository,
    service,
    store,
    target,
    threading,
    time,
    token_service,
    tokens,
    unchanged,
    user_chat_source_access,
)

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
