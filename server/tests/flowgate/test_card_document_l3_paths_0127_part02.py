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
