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
