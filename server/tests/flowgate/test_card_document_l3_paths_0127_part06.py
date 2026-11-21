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
    user_chat_source_access.upsert(USER, PROJECT, FLOW,'edit_once','l3verify-t0')
    barrier=threading.Barrier(2)
    def claim(token):
        barrier.wait(10)
        return token,user_chat_source_access.claim(USER, PROJECT, FLOW,token,'l3verify-t1','l3verify-t1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(claim,TOKEN)
        b=pool.submit(claim,l3.issued['token_id'])
        results=[a.result(10),b.result(10)]
    assert sorted(row['source_access'] for _,row in results)==['read','read_write']
    winner=next(token for token,row in results if row['one_shot_claimed'])
    assert user_chat_source_access.commit(USER, PROJECT, FLOW,winner,'l3verify-t2')=='COMMITTED'
    before=l3.observe()
    for _,claim_result in results:
        l3.db._execute('UPDATE tokens SET source_access=? WHERE token_id=?',[claim_result['source_access'],TOKEN])
        response=l3.request(via)
        assert response[0]==(200 if claim_result['one_shot_claimed'] else 403)
    assert l3.observe()['O7']==before['O7']
def test_CM08_upsert_clears_pending_handoff_without_rearming(l3):
    user_chat_source_access.upsert(USER, PROJECT, FLOW,'edit_once','l3verify-t0')
    assert user_chat_source_access.claim(USER, PROJECT, FLOW,TOKEN,'l3verify-t1','l3verify-t1')['one_shot_claimed']
    user_chat_source_access.upsert(USER, PROJECT, FLOW,'read_only','l3verify-t2')
    before=l3.observe()
    assert user_chat_source_access.commit(USER, PROJECT, FLOW,TOKEN,'l3verify-t3')=='ALREADY_CLEARED'
    assert user_chat_source_access.rollback(TOKEN,'l3verify-t4') is False
    unchanged(before,l3.observe())
