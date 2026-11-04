"""0010-L 5.2/5.3: isolated transport harness and the common O1--O14 observer.

Only external identity/RBAC, card definitions, provider admission and notification
sinks are doubles. SQLite transactions, files, history, token consumption, CAS,
chat admission and the public HTTP/worker adapters execute normally.
"""
import copy
import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from modules.flow_gate.db import connection, documents, groups, projects, tokens, user_chat_source_access
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import flow_guard, card_document_ports
from modules.flow_gate.storage import paths
from modules.flow_gate.api import inbox_routes as inbox
from modules.flow_gate.services import token_service, graph_source_hooks, node_title_hooks
from modules.flow_gate.services.ai_invoke import worker
from flowgate.card_documents import repository, legacy_adapter as card_legacy
from flowgate.card_document_edit import (integration, target, guards, contacts, chat_adapter, chat_authz,
    chat_gate, call_ledger, chat_tools, legacy_edit, dedicated_windows)
from flowgate.card_document_edit.errors import GraphApiError
from flowgate.card_document_edit.routes import router

service = importlib.import_module('flowgate.card_document_edit.revise')
PROJECT, FLOW, USER = 'l3verify-flowgate', 'l3verify-flowgate.v02.0127', 'l3verify-user'
RUN, TOKEN = 'l3verify-chat-run', 'l3verify-chat-token'
PATHS = ('C-API', 'C-CLI', 'A-CLI', 'A-API', 'D-v2', 'WP', 'TR2')
NO_WRITE = ('O1', 'O2', 'O3', 'O4', 'O5', 'O6', 'O7', 'O8', 'O9', 'O12')


def assert_isolated(db_path, storage, project):
    assert Path(db_path).name.startswith('l3verify-'), 'refuse a non-verification DB'
    assert Path(storage).name.startswith('l3verify-'), 'refuse a non-verification repository'
    assert project.startswith('l3verify-'), 'refuse a non-verification project'
    assert Path(db_path).is_file()


@pytest.fixture
def kind():
    return 'N'


@pytest.fixture
def l3(kind, monkeypatch, tmp_path, migrated_sqlite_db, caplog, request):
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.documents import tr2_service as tr2
    from modules.flow_gate.documents.routers import work_plan as wp_route
    from modules.flow_gate.services import work_plan_service as wp, work_plan_apply_service as apply
    root = tmp_path / 'l3verify-storage'
    root.mkdir()
    db_path = migrated_sqlite_db('l3verify-card-document.db')
    assert_isolated(db_path, root, PROJECT)
    db = connection.FlowGateStore.__new__(connection.FlowGateStore)
    db._db, db._sq = _SqliteDbAdapter(db_path), None
    monkeypatch.setattr(connection, 'STORE', db)
    monkeypatch.setattr(_SqliteTxnAdapter, 'cursor', property(lambda s: s._cur), raising=False)
    monkeypatch.setenv('FLOWGATE_STORAGE_DIR', str(root))
    monkeypatch.setenv('FLOWGATE_TOKEN_PEPPER_ACTIVE_ID', 'l3verify')
    monkeypatch.setenv('FLOWGATE_TOKEN_PEPPER_l3verify', 'isolated-test-pepper')
    monkeypatch.setattr(card_document_ports, '_handlers', dict(card_document_ports._handlers))
    integration.register()
    projects.create({'project_id': PROJECT, 'project_name': PROJECT})
    for user in (USER, 'l3verify-other'):
        db._execute("INSERT INTO users (user_id,username,email,password,created_at,updated_at) VALUES (?,?,?,'x','t','t')",
                    [user, user, user+'@test.invalid'])
    groups.create({'group_id': FLOW, 'project_id': PROJECT, 'module': 'v02', 'title': FLOW, 'status': 'OPEN'})
    doc_id = FLOW+'.0002-'+kind
    header = f'---\nproject: {PROJECT}\nmodule: v02\ngroup: 0127\ntype: {kind}\ndoc_number: 0002-{kind}\n---\n'
    slot = 'plan' if kind == 'WP' else 'report' if kind in ('NR', 'TR2') else 'instruction'
    node = {'node_key': 'l3verify-card', 'node_type': 'document',
            'doc_type': {'NR': 'N', 'TR2': 'T2'}.get(kind, kind)}
    path = root / ('l3verify-document.json' if kind in ('WP', 'TR2') else 'l3verify-document.md')
    content = header+'l3verify original\n'
    changed = header+'l3verify revised\n'
    if kind == 'WP':
        content = changed = wp.dumps(wp.empty_recovery_body(PROJECT))
        monkeypatch.setattr(apply, 'instruction_mode_lock_errors', lambda *a: [])
        monkeypatch.setattr(dedicated_windows, 'editable', lambda d: None)
        monkeypatch.setattr(wp_route, '_load_doc', documents.get_by_id)
        monkeypatch.setattr(wp_route, '_plan_path', lambda d: path)
        monkeypatch.setattr(wp_route, '_providers', lambda p: [])
    if kind == 'TR2':
        content = '{}'
        changed = json.dumps({'edit_spec': {'edits': [], 'deferred': [], 'termination': 'done', 'gate': {'commands': []}}})
        # The T2/source repository is a separate subsystem; only its inputs are faked.
        monkeypatch.setattr(tr2, '_assert_mutable', lambda d: None)
        monkeypatch.setattr(tr2, 'validate', lambda d, **kw: d)
        monkeypatch.setattr(tr2, 'verify_pair', lambda *a: None)
        monkeypatch.setattr(tr2, 'canonicalize', lambda d: dict(d))
        monkeypatch.setattr(tr2, 'target_set', lambda *a: [])
        monkeypatch.setattr(tr2, 'resolve_source_root', lambda *a: root)
        monkeypatch.setattr(tr2, 'target_fingerprint', lambda *a: 'sha256:'+'b'*64)
        monkeypatch.setattr(tr2, 'derived_files', lambda *a: [])
        monkeypatch.setattr(tr2, 'spec_fingerprint', lambda *a: 'l3verify-spec')
        monkeypatch.setattr(tr2, 'canonical_path_for_doc', lambda *a: path)
        monkeypatch.setattr(tr2, 'dumps', json.dumps)
        monkeypatch.setattr(tr2, 'parse', json.loads)
    path.write_text(content, encoding='utf-8', newline='')
    doc = documents.create({'doc_id':doc_id, 'project_id':PROJECT, 'module':'v02', 'group_id':FLOW,
        'type_code':kind, 'seq':2, 'title':'l3verify document', 'status':'draft', 'owner_id':USER,
        'file_path':paths.to_storage_relative(path, PROJECT)})
    db._execute('UPDATE documents SET revision_no=7 WHERE doc_id=?', [doc_id])
    repository.insert(FLOW, node['node_key'], slot, kind, doc_id, True, 0, USER)
    monkeypatch.setattr(target.settings, 'card_node', lambda f,k: node if (f,k)==(FLOW,node['node_key']) else None)
    monkeypatch.setattr(card_legacy, 'load_flow', lambda f: groups.get_by_id(f) or fail(404,'flow_not_found',{'flow_id':f}))
    monkeypatch.setattr(card_legacy, 'require_writable', lambda *a: None)
    monkeypatch.setattr(guards, 'has_permission', lambda *a: True)
    monkeypatch.setattr(legacy_edit, 'has_permission', lambda *a: True)
    monkeypatch.setattr(guards.mutation_policy, 'assert_group_mutation_allowed', lambda *a: None)
    monkeypatch.setattr(guards.workflow_rework_service, 'assert_no_revert_pending', lambda *a: None)
    monkeypatch.setattr(guards.document_service, 'is_final_approved', lambda d: False)
    monkeypatch.setattr(graph_source_hooks, '_fence_handler', None)
    monkeypatch.setattr(node_title_hooks, 'after_registration', lambda *a: None)
    events = []
    monkeypatch.setattr(contacts, 'broadcast_event_threadsafe', lambda ev: events.append(
        ('document' if ev.event_type==contacts.EventType.DOCUMENT_EXPLORER_REFRESH else 'group',ev)))
    monkeypatch.setattr(contacts.canvas_events, 'publish_canvas_changed', lambda **kw: events.append(('canvas',kw)))
    monkeypatch.setattr(contacts.v01_contact, 'after_inbox_edited', lambda *a: events.append(('graph',a)))
    monkeypatch.setattr(envelope, 'verify_bearer', lambda r: {'issued_to':USER,'_is_user_jwt':True}
                        if r.headers.get('Authorization')=='Bearer l3verify-user' else JSONResponse(status_code=401,content={}))
    app = FastAPI()
    app.include_router(router, prefix='/flowgate')
    app.include_router(inbox.router, prefix='/flowgate')
    client = TestClient(app)
    expires = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    chat_token = {'token_id':TOKEN, 'project':PROJECT, 'group_id':FLOW, 'action_scope':'chat',
        'doc_ref':FLOW+'.0001-CH', 'issued_to':USER, 'source_access':'read_write', 'ai_run_id':RUN,
        'hash':'l3verify-hash', 'pepper_id':'l3verify', 'created_at':connection.now_iso(), 'expires_at':expires}
    tokens.create(chat_token)
    user_chat_source_access.upsert(USER, 'read_only', connection.now_iso())
    run = {'run_id':RUN,'project_id':PROJECT,'group_id':FLOW,'action_scope':'chat','doc_ref':chat_token['doc_ref'],
        'issued_to':USER,'current_token_id':TOKEN,'token_id':TOKEN,'status':'running'}
    def verify(raw):
        row = tokens.get_by_id(TOKEN)
        if raw!='l3verify-chat' or chat_authz.token_live(row)!=chat_authz.LIVE:
            fail(401,'unauthorized',{})
        return row
    monkeypatch.setattr(chat_authz, '_verify_token', verify)
    monkeypatch.setattr(chat_authz, '_get_run_record', lambda rid: run if rid==RUN else None)
    def transport(run, raw_token, body):
        r = client.post('/flowgate/api/v1/inbox',headers={'Authorization':'Bearer '+raw_token},json=body)
        return r.status_code,r.json()
    monkeypatch.setattr(worker, '_post_inbox_json', transport)
    issued = token_service.issue(PROJECT,FLOW,'edit',doc_id,USER)
    agent_run = {'run_id':'l3verify-agent-run','project_id':PROJECT,'module':'v02','group_id':FLOW,
        'action_scope':'edit','doc_ref':doc_id,'token_id':issued['token_id'],'current_token_id':issued['token_id'],
        'transport_api_base':'http://testserver/flowgate/api/v1'}
    route=getattr(getattr(request.node,'callspec',None),'params',{}).get('via','')
    if route.endswith('-ai'):
        from modules.flow_gate.services import ai_invoke_service
        db._execute("INSERT INTO ai_providers (provider_id,name,exec_type,kind,created_at,updated_at) VALUES ('l3verify-provider','l3verify AI','api','openai','t','t')")
        db._execute('UPDATE tokens SET provider_id=?,ai_run_id=? WHERE token_id=?',
                    ['l3verify-provider',agent_run['run_id'],issued['token_id']])
        agent_run.update(issued_to=USER,provider_id='l3verify-provider',status='running')
        monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda rid:agent_run if rid==agent_run['run_id'] else None)
    flow_guard.layout_store.ensure_guard_row(FLOW)
    chat_gate.reset()
    h = Harness(db=db,db_path=db_path,root=root,path=path,doc_id=doc_id,header=header,kind=kind,slot=slot,
        node=node,content=changed,client=client,run=run,agent_run=agent_run,issued=issued,events=events,caplog=caplog,
        response=None,contexts=[],graph={})
    yield h
    chat_gate.reset()
    client.close()


def fail(status, code, params):
    raise GraphApiError(status, code, params)


class Harness(SimpleNamespace):
    @property
    def coords(self):
        return {'flow_id':FLOW,'node_key':self.node['node_key'],'slot':self.slot}

    def body(self, **over):
        return {'expected_doc_id':self.doc_id,'expected_revision':7,'content':self.content,
                'idempotency_key':'l3verify-save',**over}

    def request(self, via, *, editing=True, body=None, context=None, provider_id='call_0'):
        from modules.flow_gate.documents.routers import work_plan as wp_route
        from modules.flow_gate.documents import tr2_service as tr2
        via=via.removesuffix('-ai')
        body = self.body() if body is None else dict(body)
        base = f'/flowgate/api/v2/flows/{FLOW}/nodes/{self.node["node_key"]}/slots/{self.slot}/document'
        if via=='C-API':
            tool = chat_tools.REVISE_TOOL if editing else chat_tools.READ_TOOL
            inp = {**self.coords, **({k:v for k,v in body.items() if k!='idempotency_key'} if editing else {})}
            if context is None:
                context = call_ledger.accept_call(self.run, {'id':provider_id,'name':tool,'input':inp})
                self.contexts.append(context)
            fn = chat_adapter.revise_card_document_call_handler if editing else chat_adapter.read_card_document_call_handler
            result = fn(self.run,context)
        elif via in ('C-CLI','D-v2'):
            if via=='C-CLI':
                base=base.replace('/api/v2/flows/','/api/v2/chat/flows/')
            headers={'Authorization':'Bearer '+('l3verify-chat' if via=='C-CLI' else 'l3verify-user')}
            response = self.client.post(base+'/revisions',headers=headers,json=body) if editing else self.client.get(base,headers=headers)
            result = response.status_code,response.json()
        elif via in ('A-CLI','A-API'):
            payload={'action':'edit','project':PROJECT,'module':'v02','group_name':FLOW,'doc_id':self.doc_id,
                'expected_revision':body['expected_revision'],'content':body['content'],'edit_reason':body.get('edit_reason','worker_self')}
            payload.update({k:body[k] for k in ('rejection_response','rejection_id','rejection_review_id') if k in body})
            if via=='A-CLI':
                response=self.client.post('/flowgate/api/v1/inbox',headers={'Authorization':'Bearer '+self.issued['raw_token']},json=payload)
                result=response.status_code,response.json()
            else:
                result=worker._inbox_register(self.agent_run,self.issued['raw_token'],{k:v for k,v in payload.items()
                    if k not in ('action','project','module','group_name','doc_id')})
        elif via=='WP':
            value=wp_route.save_work_plan(Request({'type':'http','method':'PUT','path':'/','headers':[]}),self.doc_id,
                wp_route.WorkPlanSave(base_revision_no=body['expected_revision'],body=json.loads(body['content'])),{'user_id':USER})
            result=(value.status_code,json.loads(value.body)) if isinstance(value,JSONResponse) else (200,value)
        elif via=='TR2':
            try:
                value=tr2.save(self.doc_id,json.loads(body['content']),actor=USER,expected_revision=body['expected_revision'])
                result=200,value
            except tr2.Tr2ValidationError as exc:
                result=exc.status_code if hasattr(exc,'status_code') else 409,{'code':exc.code,'params':exc.details}
        else:
            raise AssertionError(via)
        self.response=result
        return result

    def observe(self):
        tables={r['name'] for r in self.db._fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table, where=''):
            return self.db._fetch_all('SELECT * FROM '+table+where) if table in tables else []
        files={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(self.root.rglob('*')) if p.is_file()}
        graph={name:rows(name) for name in sorted(tables) if name.startswith('graph_') and name!='graph_flow_guards'}
        ledger=self.run.get(call_ledger.LEDGER_KEY)
        records=[]
        if ledger is not None:
            with ledger.lock:
                records=[{'server_call_id':r.server_call_id,'tool_call_id':r.tool_call_id,'input_sha256':r.input_sha256,
                          'state':list(ledger.states[r.server_call_id])} for r in ledger.records.values()]
        return copy.deepcopy({
            'O1':rows('documents',' ORDER BY doc_id'), 'O2':documents.get_by_id(self.doc_id),
            'O3':files, 'O4':rows('document_revisions',' ORDER BY id'),
            'O5':rows('workflow_events'," WHERE event_type='doc_edited' ORDER BY id"),
            'O6':{'tables':graph,'model':self.graph}, 'O7':rows('user_chat_source_access'),
            'O8':rows('tokens',' ORDER BY token_id'), 'O9':[k for k,_ in self.events],
            'O10':self.response, 'O11':[r.getMessage() for r in self.caplog.records],
            'O12':rows('graph_flow_guards'),
            'O13':records, 'O14':chat_gate.state(RUN)})


def unchanged(before, after, keys=NO_WRITE):
    for key in keys:
        if key=='O1':
            assert len(before[key])==len(after[key]), key
        else:
            assert before[key]==after[key], key


def code(result):
    return result[1].get('error',{}).get('code') or result[1].get('code')


def emit(h, name, data):
    """Artifacts stay below pytest's scratch basetemp; stdout links them to the run."""
    out=h.root.parent/('l3verify-'+name+'.json')
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print('L3VERIFY',name,json.dumps(data,ensure_ascii=True,default=str))


def race_process(config,barrier,queue,via):
    """X5: independent server interpreter; shared isolated DB/file, no process-local lock sharing."""
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from flowgate.card_document_edit import boundary
    import os
    mp=pytest.MonkeyPatch()
    try:
        assert_isolated(config['db_path'],config['root'],PROJECT)
        db=connection.FlowGateStore.__new__(connection.FlowGateStore)
        db._db,db._sq=_SqliteDbAdapter(config['db_path']),None
        mp.setattr(connection,'STORE',db)
        mp.setattr(_SqliteTxnAdapter,'cursor',property(lambda s:s._cur),raising=False)
        mp.setenv('FLOWGATE_STORAGE_DIR',config['root'])
        integration.register()
        mp.setattr(target.settings,'card_node',lambda *a:config['node'])
        mp.setattr(card_legacy,'load_flow',groups.get_by_id)
        mp.setattr(card_legacy,'require_writable',lambda *a:None)
        mp.setattr(guards,'has_permission',lambda *a:True)
        mp.setattr(guards.mutation_policy,'assert_group_mutation_allowed',lambda *a:None)
        mp.setattr(guards.workflow_rework_service,'assert_no_revert_pending',lambda *a:None)
        mp.setattr(guards.document_service,'is_final_approved',lambda *a:False)
        mp.setattr(contacts,'broadcast_event_threadsafe',lambda *a:None)
        mp.setattr(contacts.canvas_events,'publish_canvas_changed',lambda **kw:None)
        mp.setattr(contacts.v01_contact,'after_inbox_edited',lambda *a:None)
        original=boundary.enter
        def synchronized(*a,**kw):
            barrier.wait(30)
            return original(*a,**kw)
        mp.setattr(boundary,'enter',synchronized)
        if via=='C-API':
            inp={'flow_id':FLOW,'node_key':config['node']['node_key'],'slot':config['slot'],
                 'expected_doc_id':config['doc_id'],'expected_revision':7,'content':config['content']}
            ctx=call_ledger.accept_call(config['run'],{'id':'call_0','name':chat_tools.REVISE_TOOL,'input':inp})
            result=chat_adapter.revise_card_document_call_handler(config['run'],ctx)
        else:
            mp.setattr(envelope,'verify_bearer',lambda r:{'issued_to':USER,'_is_user_jwt':True})
            app=FastAPI()
            app.include_router(router,prefix='/flowgate')
            with TestClient(app) as client:
                response=client.post(f'/flowgate/api/v2/flows/{FLOW}/nodes/{config["node"]["node_key"]}/slots/{config["slot"]}/document/revisions',
                    json={'expected_doc_id':config['doc_id'],'expected_revision':7,
                          'content':config['content'],'idempotency_key':'l3verify-process'})
                result=response.status_code,response.json()
        queue.put({'pid':os.getpid(),'result':result})
    except BaseException as exc:
        import traceback
        queue.put({'error':repr(exc),'trace':traceback.format_exc()})
    finally:
        mp.undo()
