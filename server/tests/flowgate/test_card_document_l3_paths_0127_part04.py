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
