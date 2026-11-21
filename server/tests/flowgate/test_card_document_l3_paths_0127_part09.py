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
def test_CM03_same_winning_run_edits_two_documents(l3,monkeypatch,via):
    from l3verify_support_0127 import Harness
    user_chat_source_access.upsert(USER, PROJECT, FLOW,'edit_once','l3verify-t0')
    assert user_chat_source_access.claim(USER, PROJECT, FLOW,TOKEN,'l3verify-t1','l3verify-t1')['one_shot_claimed']
    assert user_chat_source_access.commit(USER, PROJECT, FLOW,TOKEN,'l3verify-t2')=='COMMITTED'
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
