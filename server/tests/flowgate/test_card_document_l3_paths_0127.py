"""route matrix, rejection observations and synchronized races."""
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from l3verify_support_0127 import (l3, kind, PROJECT, FLOW, USER, RUN, TOKEN, PATHS, NO_WRITE,
    unchanged, code, emit, service, documents, tokens, contacts, chat_adapter, chat_authz, chat_gate,
    call_ledger, target, repository, guards, token_service, flow_guard)
from modules.flow_gate.db import document_revisions, user_chat_source_access
from modules.flow_gate.storage import paths
from flowgate.card_document_edit import boundary, store, idempotency
from flowgate.card_document_edit.errors import GraphApiError

ROUTES=[(p,k) for k in ('N','NR','WP','TR2') for p in PATHS if p not in ('WP','TR2') or p==k]


@pytest.mark.parametrize('via,kind',ROUTES)
def test_normal_and_reconnect_O1_to_O14(l3,via,kind):
    before=l3.observe()
    response=l3.request(via)
    assert response[0]==200,response
    after=l3.observe()
    assert after['O2']['doc_id']==l3.doc_id and after['O2']['revision_no']==8
    assert len(after['O1'])==len(before['O1'])==1
    for key in ('status','doc_review_status','owner_id','group_id'):
        assert after['O2'][key]==before['O2'][key],key
    assert len(after['O4'])==len(after['O5'])==1
    assert after['O4'][0]['created_by']==USER
    assert after['O5'][0]['actor_user_id']==USER
    assert after['O7']==before['O7'] and after['O6']==before['O6']
    if not via.startswith('A-'):
        assert after['O8']==before['O8']
    assert after['O9'].count('document')==after['O9'].count('canvas')==after['O9'].count('graph')==1
    assert not any('.keep' in name for name in after['O3'])
    assert l3.request('D-v2',editing=False)[1]['revision_no']==8
    assert l3.request('C-CLI',editing=False)[1]['content']==l3.path.read_text(encoding='utf-8')
    if kind not in ('WP','TR2'):
        assert paths.resolve_storage_path(after['O4'][0]['backup_path'],PROJECT).read_text(encoding='utf-8')==l3.header+'l3verify original\n'
    emit(l3,'normal',{'path':via,'kind':kind,'before':before,'after':after})


@pytest.mark.parametrize('via',['C-API','C-CLI','A-CLI','A-API','D-v2'])
@pytest.mark.parametrize('permission',['create_only','neither','foreign_project','update_only'])
def test_permission_matrix_issued_to_not_create(l3,monkeypatch,via,permission):
    calls=[]
    def allowed(user,project,perm):
        calls.append((user,project,perm))
        return perm=='project.document.read' or (permission=='update_only' and perm=='project.document.update') or (
            permission=='create_only' and perm=='project.document.create') or project=='l3verify-foreign'
    monkeypatch.setattr(guards,'has_permission',allowed)
    before=l3.observe()
    result=l3.request(via)
    assert result[0]==(200 if permission=='update_only' else 403),result
    assert (USER,PROJECT,'project.document.update') in calls
    if permission!='update_only':
        assert code(result)=='permission_denied'
        unchanged(before,l3.observe())


@pytest.mark.parametrize('via',['C-API','C-CLI','A-CLI','A-API','D-v2'])
@pytest.mark.parametrize('failure',['stale','unlinked','history','event','unreadable','binding_unavailable'])
def test_negative_observations(l3,monkeypatch,via,failure):
    body=l3.body()
    status,expected=500,'internal_error'
    def injected(*a,**k):
        raise RuntimeError('l3verify injected write failure')
    if failure=='stale':
        body['expected_revision']=6
        status,expected=409,'revision_conflict'
    elif failure=='unlinked':
        # Binding remains recorded; its current-document read returns no document.
        monkeypatch.setattr(target,'resolve_current',lambda *a:None)
        # A known coordinate remains known after its document was removed.
        status,expected=404,'no_document'
    elif failure=='history':
        monkeypatch.setattr(document_revisions,'create_once',injected)
    elif failure=='event':
        monkeypatch.setattr(store.workflow_events,'create',injected)
    elif failure=='unreadable':
        l3.path.unlink()
        status,expected=422,'not_editable'
    else:
        monkeypatch.setattr(target.repository,'available',lambda:False)
        status,expected=422,'card_binding_missing'
    before=l3.observe()
    result=l3.request(via,body=body)
    assert (result[0],code(result))==(status,expected),result
    unchanged(before,l3.observe())
    assert chat_gate.state(RUN)[1]==0




















PAIRS=[('C-API','C-CLI','N'),('C-API','D-v2','N'),('C-CLI','D-v2','N'),
    ('C-API','A-CLI','N'),('C-CLI','A-API','N'),('C-API','A-API','N'),('C-CLI','A-CLI','N'),
    ('C-API','WP','WP'),('C-CLI','WP','WP'),
    ('C-API','TR2','TR2'),('C-CLI','TR2','TR2')]
