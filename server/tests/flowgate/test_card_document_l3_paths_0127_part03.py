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
