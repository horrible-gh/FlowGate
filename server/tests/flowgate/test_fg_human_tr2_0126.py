"""0126 T#2 B12: real TR2 source lock/apply/validation/Git/finalize + human cascade.

The Graph context lookup/editability is injected for this isolated sequence-backed
TR2 harness; source effects, Flow guard, authority, CAS, receipts and audit are real.
Graph dispatch and completion are exercised separately in test_fg_human_completion_0126.
"""
import json
import pytest
from test_fg_card_settings_tr2_approval import (
    PROJECT, USER, store, repo, env, token_scratch_in_storage,
    make_group, auto_approved_t2, submit_tr2, git, git_head, BASE,
)

pytestmark=pytest.mark.usefixtures('token_scratch_in_storage')


@pytest.fixture(autouse=True)
def isolated_repo_root(monkeypatch,tmp_path):
    import tests.e2e.tr2.test_tr2_connected_e2e_0565 as harness
    monkeypatch.setattr(harness,'_SCRATCH',tmp_path)


def card(env,monkeypatch):
    from modules.flow_gate.db import documents,groups
    from modules.flow_gate.storage.paths import get_storage_root
    from flowgate.card_documents import decision_context as context
    from flowgate.card_settings.report_baseline import fingerprint
    from flowgate.card_completion import outputs
    g=make_group('0262'); t2=auto_approved_t2(g); tr2=submit_tr2(g,t2)
    group=groups.get_by_id(g['group_id'])
    # T2 instructions are fileless in the harness. Give the decision a saved body.
    path=get_storage_root(PROJECT,create=True)/'instruction.md'
    path.write_text('---\ntitle: instruction\n---\nApply the attached edit specification.\n',encoding='utf-8')
    documents.update(t2,{'file_path':'instruction.md'})
    def current(flow,key):
        docs={'instruction':documents.get_by_id(t2),'report':documents.get_by_id(tr2)}
        ctx={'run_id':None,'attempt_id':None,'graph_revision_no':None,'draft_version':1,'settings_version':0,
             'documents':{s:{**fingerprint(d),'doc_review_status':d.get('doc_review_status')} for s,d in docs.items()}}
        return {'node_key':key,'node_type':'document','doc_type':'T2'},None,ctx,docs
    monkeypatch.setattr(context,'current',current)
    monkeypatch.setattr(context,'require_editable',lambda *a:None)
    monkeypatch.setattr(outputs,'instruction_type',lambda *a:'T2')
    from flowgate.card_documents import decisions
    # This harness begins SQLite transactions eagerly; reuse its snapshot.
    with env['store'].transaction():
        view=decisions.read(group,'isolated',USER)
    body={'action':'approve','doc_id':tr2,'expected_revision':view['documents']['report']['revision_no'],'expected_decision_version':0,
          'expected_context':view['context'],'request_key':'strong-human'}
    return group,t2,tr2,body


def test_human_tr2_finalizes_cascade_once_in_real_source_transaction(env,monkeypatch):
    from flowgate.card_documents import decisions
    from modules.flow_gate.db import documents,tr2_approval_attempts
    from modules.flow_gate.db.connection import get_store
    group,t2,tr2,body=card(env,monkeypatch)
    (env['repo']/'gate.ok').write_bytes(b'ok'); before=git_head(env['repo'])
    result=decisions.write(group,'isolated','report',body,USER)
    assert result['changed_slots']==['instruction','report']
    assert git(env['repo'],'rev-parse','HEAD^').strip()==before
    assert (env['repo']/'src/app.txt').read_bytes()==b"greeting = 'hi'\ncount = 1\n"
    rows=get_store()._fetch_all('SELECT * FROM graph_document_decisions')
    assert len(rows)==2 and {r['actor_id'] for r in rows}=={USER}
    assert rows[0]['request_id']==rows[1]['request_id']
    assert tr2_approval_attempts.latest_success(tr2)['actor_user_id']==USER
    head=git_head(env['repo'])
    assert decisions.write(group,'isolated','report',body,USER)['replayed']
    assert git_head(env['repo'])==head and len(tr2_approval_attempts.list_by_doc(tr2))==1
    assert documents.get_by_id(tr2)['doc_review_status']=='approved'


@pytest.mark.parametrize('failure',['gate','finalize','stale_context'])
def test_human_tr2_failure_rolls_back_source_cascade_and_receipt(env,monkeypatch,failure):
    from flowgate.card_documents import decisions
    from modules.flow_gate.db import documents
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents import tr2_approval_service as strong
    group,t2,tr2,body=card(env,monkeypatch)
    before=git_head(env['repo']); original=decisions._record
    before_states=[documents.get_by_id(d)['doc_review_status'] for d in (t2,tr2)]
    if failure!='gate': (env['repo']/'gate.ok').write_bytes(b'ok')
    if failure=='finalize':
        def fail(*args,**kw):
            original(*args,**kw)
            if args[2]=='report': raise RuntimeError('injected finalize fault')
        monkeypatch.setattr(decisions,'_record',fail)
    if failure=='stale_context':
        commit=strong._commit_exact
        def changed(*args):
            result=commit(*args)
            from flowgate.card_documents import decision_context
            current=decision_context.current
            def stale(*a):
                node,row,ctx,docs=current(*a)
                return node,row,{**ctx,'settings_version':1},docs
            monkeypatch.setattr(decision_context,'current',stale)
            return result
        monkeypatch.setattr(strong,'_commit_exact',changed)
    with pytest.raises(Exception): decisions.write(group,'isolated','report',body,USER)
    from modules.flow_gate.db import tr2_approval_attempts
    attempts=tr2_approval_attempts.list_by_doc(tr2)
    assert len(attempts)==1 and attempts[0]['state']=='failed', attempts
    assert git_head(env['repo'])==before and (env['repo']/'src/app.txt').read_bytes()==BASE
    assert [documents.get_by_id(d)['doc_review_status'] for d in (t2,tr2)]==before_states
    assert get_store()._fetch_all('SELECT * FROM graph_document_decisions')==[]
    assert get_store()._fetch_all('SELECT * FROM graph_document_decision_requests')==[]
