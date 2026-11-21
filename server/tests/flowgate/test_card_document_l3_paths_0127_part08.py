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
