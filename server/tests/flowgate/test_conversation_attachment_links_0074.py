"""Real attachment namespace + append/read adapter regressions for fg-attachment."""
import io
import os
import pytest
from starlette.datastructures import UploadFile
from fastapi import FastAPI
from starlette.testclient import TestClient
from test_attachment_upload_requests_0017 import (
    env, fg_store, clean_runs, GROUP, PROJECT, DOC, DOC2, CHAT_DOC, USER,
    ADMIN_USER, make_request, approve,
)
from modules.flow_gate.documents.attachments import flow
from modules.flow_gate.services import conversation_attachment_links as links
from modules.flow_gate.services import conversation_turn_service as turns
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import token_service
from modules.flow_gate.db import documents, conversation_turns as store

ACTOR = {'kind': 'session', 'user_id': USER, 'user_name': 'User'}


@pytest.fixture(autouse=True)
def restore_token_environment(monkeypatch):
    # Token inspection may initialize this process setting; restore the prior value.
    monkeypatch.setenv('FLOWGATE_AGENT_PEPPER_ID', os.environ.get('FLOWGATE_AGENT_PEPPER_ID', ''))


def upload(data=b'hello'):
    flow.upload(GROUP, UploadFile(filename='report.txt', file=io.BytesIO(data)), ADMIN_USER)
    return flow.rows(GROUP)[0]


def append(body, key='link-test-0001'):
    return turns.append_turn(doc_id=CHAT_DOC, actor=ACTOR, body_raw=body, idempotency_key=key)


@pytest.mark.parametrize('body,expected', [
    ('[report](fg-attachment:a) [again](fg-attachment:a) [b](fg-attachment:b)', ['a','b']),
    ('`[x](fg-attachment:a)` [ok](fg-attachment:b)', ['b']),
    ('``[x](fg-attachment:a) ` literal``', []),
    ('```md\n[x](fg-attachment:a)\n```\n[ok](fg-attachment:b)', ['b']),
    ('~~~\n[x](fg-attachment:a)\n~~~', []),
    ('````\n```\n[x](fg-attachment:a)\n````', []),
    ('```\n[x](fg-attachment:a)', []),
    ('[`name`](fg-attachment:a)', ['a']),
    (r'\[x](fg-attachment:a) ![image](fg-attachment:b) fg-attachment:c', []),
    ('[x](<fg-attachment:a> "title")', ['a']),
    ('[bad](fg-attachment:)', ['']),
    ('[report [draft]](fg-attachment:a)', ['a']),
    (r'[escaped \] label](fg-attachment:a)', ['a']),
])
def test_markdown_extraction(body, expected):
    assert links.link_keys(body) == expected


def test_append_read_delete_and_replay_leave_stored_body_intact(env):
    row = upload()
    body = f"[report](fg-attachment:{row['key']})"
    result = append(body)
    assert result['turn']['attachment_links'] == [{'key': row['key'], 'status':'available',
        'name':'report.txt', 'size':5, 'replacement_key':None}]
    flow.remove(GROUP, row['key'], ADMIN_USER, row['content_sha256'])
    page = query.list_turns(doc_id=CHAT_DOC, actor=ACTOR, after_seq=0)
    assert page['turns'][0]['attachment_links'][0]['status'] == 'deleted'
    assert page['turns'][0]['body'] == body
    assert append(body)['replayed'] is True
    assert store.current_head_seq(CHAT_DOC) == 1


@pytest.mark.parametrize('dry_run', [False, True])
def test_unknown_and_other_flow_keys_reject_without_turn_or_token_consumption(env, monkeypatch, dry_run):
    foreign = flow.key({'doc_id':'another.flow.0001-R','filename':'secret.txt'})
    body = f'[missing](fg-attachment:missing) [foreign](fg-attachment:{foreign})'
    consumed=[]
    monkeypatch.setattr(token_service, 'consume', lambda *a, **kw: consumed.append(a))
    with pytest.raises(turns.ConversationTurnError) as error:
        if dry_run:
            turns.dry_run_append(doc_id=CHAT_DOC, actor=ACTOR, body_raw=body,
                idempotency_key='link-test-0001', token_rec={'token_id':'test-token'})
        else:
            append(body)
    assert error.value.status_code == 422
    assert error.value.code == 'attachment_link_unknown'
    assert error.value.unknown_keys == ['missing',foreign]
    assert store.current_head_seq(CHAT_DOC) == 0
    assert not consumed


def test_valid_dry_run_and_code_examples_do_not_write(env, monkeypatch):
    row=upload()
    monkeypatch.setattr(token_service, 'increment_dry_run', lambda *a: None)
    result=turns.dry_run_append(doc_id=CHAT_DOC, actor=ACTOR,
        body_raw=f"[report](fg-attachment:{row['key']})", idempotency_key='link-test-0001',
        token_rec={'token_id':'test-token'})
    assert result['dry_run'] and not result['corrupted']
    assert store.current_head_seq(CHAT_DOC) == 0
    assert append('`[x](fg-attachment:missing)`\n```\n[x](fg-attachment:missing)\n```')['turn']['attachment_links'] == []
    assert append('ordinary text', 'link-test-0002')['turn']['attachment_links'] == []


def test_same_name_new_key_is_reported_without_rewriting_body(env):
    row=upload()
    body=f"[report](fg-attachment:{row['key']})"
    append(body)
    flow.remove(GROUP,row['key'],ADMIN_USER,row['content_sha256'])
    env['state']['doc_id']=DOC2
    approve(make_request(b'replacement',name='report.txt'),doc_id=DOC2)
    replacement=flow.rows(GROUP)[0]
    status=links.statuses(CHAT_DOC,body)[0]
    assert status == {'key':row['key'],'status':'replaced','name':'report.txt','size':11,'replacement_key':replacement['key']}
    assert store.get_turn(CHAT_DOC,1)['body'] == body


def test_existing_key_overwrite_reports_current_metadata(env):
    row=upload()
    body=f"[report](fg-attachment:{row['key']})"
    append(body)
    flow.upload(GROUP,UploadFile(filename='report.txt',file=io.BytesIO(b'changed')),ADMIN_USER,
                'overwrite',row['content_sha256'],row['key'])
    status=links.statuses(CHAT_DOC,body)[0]
    assert status['status']=='available' and status['size']==7 and status['replacement_key'] is None


def test_status_never_resolves_cross_flow_name(env):
    upload()
    foreign=flow.key({'doc_id':'other.group.0001-R','filename':'report.txt'})
    assert links.statuses(CHAT_DOC,f'[secret](fg-attachment:{foreign})') == [
        {'key':foreign,'status':'deleted','name':None,'size':None,'replacement_key':None}]


def test_session_and_worker_error_envelopes(env, monkeypatch):
    from modules.flow_gate.documents.routers import conversation_turns as session
    from modules.flow_gate.api.v1 import conversation_routes as worker
    from modules.flow_gate.auth.middleware import get_current_user
    app=FastAPI();app.include_router(session.router);app.include_router(worker.router)
    app.dependency_overrides[get_current_user]=lambda: ADMIN_USER
    client=TestClient(app)
    response=client.post(f'/documents/{CHAT_DOC}/conversation/turn',json={
        'body':'[x](fg-attachment:missing)','idempotency_key':'link-test-http'})
    assert response.status_code==422
    assert response.json()['detail']['unknown_keys']==['missing']
    token={'token_id':'test-token','project':PROJECT,'group_id':GROUP,'doc_ref':CHAT_DOC,
           'action_scope':'chat','issued_to':USER,'consumed_at':None}
    monkeypatch.setattr(worker.token_service,'inspect_for_replay',lambda raw: token)
    monkeypatch.setattr(worker,'_commit_message_refusal',lambda *args: None)
    for dry in (False,True):
        response=client.post(f'/api/v1/conversation/{CHAT_DOC}/turn',headers={'Authorization':'Bearer test'},json={
            'body':'[x](fg-attachment:missing)','idempotency_key':'test-token','dry_run':dry})
        assert response.status_code==422, response.text
        assert response.json()['code']=='attachment_link_unknown'
        assert response.json()['unknown_keys']==['missing']


def test_worker_listing_exposes_key_and_prompt_uses_same_address(env, monkeypatch):
    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.services.prompt_blocks.blocks import conversation
    row=upload()
    monkeypatch.setattr(document_routes,'verify_bearer',lambda request: {'group_id':GROUP,'project':PROJECT})
    monkeypatch.setattr(document_routes,'_attachment_read_kind',lambda auth: ('read',None))
    result=document_routes.get_flow_attachments(None,GROUP)
    assert result['attachments'][0]['key']==row['key']
    for renderer in (conversation.conversation_turn_brief, conversation.conversation_turn_submission_lines):
        text='\n'.join(renderer(api_base='/api/v1',doc_id=CHAT_DOC,raw_token='test',token_id='test',
            payload_lines=[],can_write=False,provider=None))
        assert f'/flow/{GROUP}/attachments' in text
        assert '[name](fg-attachment:key)' in text
