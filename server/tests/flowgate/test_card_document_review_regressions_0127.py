"""TR0019 review regressions: real guards, transactions, path races and token evidence."""
import asyncio
import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData, UploadFile

from test_card_document_edit_0127 import env, F, D, HEADER
from test_card_document_edit_write_gate_0127 import denied
from modules.flow_gate.db import documents, connection, document_revisions, tokens, projects
from modules.flow_gate.graph_workflow.services import flow_guard, card_document_ports as ports
from modules.flow_gate.storage import files, paths
from flowgate.card_document_edit import write_gate as gate, target, legacy_snapshots, inventory_gate, boundary, dedicated_windows
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.documents.routers import documents as routes
from flowgate.card_settings import legacy_adapter as settings


@pytest.mark.parametrize('alias', ['content', 'rpc'])
@pytest.mark.parametrize('guard_case', ['no_group', 'missing_group', 'definition_unavailable'])
def test_non_card_patch_without_flow_returns_200_and_saves(env, monkeypatch, alias, guard_case):
    target.repository.delete(F, 'research', 'instruction', 'N')
    if guard_case == 'no_group':
        env.db._execute('UPDATE documents SET group_id=NULL WHERE doc_id=?', [D])
    elif guard_case == 'missing_group':
        monkeypatch.setattr(flow_guard.db_groups, 'get_by_id', lambda _: None)
    else:
        monkeypatch.setattr(flow_guard.definition_store, 'available', lambda: False)
    for name in ('_reject_if_group_disposed', '_reject_if_group_ai_running', '_broadcast_document_refresh'):
        monkeypatch.setattr(routes, name, lambda *a: None)
    monkeypatch.setattr(routes.document_service, 'is_final_approved', lambda _: False)
    monkeypatch.setattr(routes.document_service, 'is_document_editable', lambda *a, **k: True)
    if alias == 'content':
        body, url = {'content':HEADER+'without Flow\n'}, '/'+D+'/content'
    else:
        body, url = {'doc_id':D,'content':HEADER+'without Flow\n'}, '/content'
    original = routes.update_document_content if alias == 'content' else routes.update_document_content_rpc
    fn = getattr(original, '__wrapped__', original)
    app = FastAPI()
    if alias == 'content':
        @app.patch('/{doc_id}/content')
        def patch(doc_id: str, body: routes.DocumentContentUpdate):
            return fn(doc_id, body, {'user_id':'user'})
    else:
        @app.patch('/content')
        def patch(body: routes.DocumentContentUpdateRpc):
            return fn(body, {'user_id':'user'})
    response = TestClient(app).patch(url, json=body)
    assert response.status_code == 200, response.text
    assert 'without Flow' in env.path.read_text(encoding='utf-8')
    assert documents.get_by_id(D)['revision_no'] == 0
    assert flow_guard.held_guard() is None


def test_reference_index_has_linear_filesystem_call_bound(env, tmp_path, monkeypatch):
    root = tmp_path / 'large-tree'; root.mkdir()
    count = 1200
    for i in range(count):
        (root / str(i)).write_bytes(b'x')
    rows = [{'doc_id':str(i), 'project_id':'flowgate', 'file_path':str(tmp_path/'outside'/str(i))} for i in range(count)]
    calls = {'candidates':0, 'identity':0}
    monkeypatch.setattr(env.db, '_fetch_all', lambda *a: rows)
    def candidates(doc):
        calls['candidates'] += 1
        return [(Path(doc['file_path']), True)]
    original = gate.file_key
    def identity(p):
        calls['identity'] += 1
        return original(p)
    monkeypatch.setattr(gate, 'candidates', candidates)
    monkeypatch.setattr(gate, 'file_key', identity)
    monkeypatch.setattr(gate, 'same', lambda *a: pytest.fail('pairwise filesystem comparison'))
    assert gate.references(trees=[root]) == []
    assert calls['candidates'] == count
    assert calls['identity'] <= count * 2


def test_batch_scope_does_not_rescan_database_for_each_file(env, tmp_path, monkeypatch):
    targets = [tmp_path / f'copy-{i}' for i in range(1000)]
    original, calls = gate.references, []
    def references(*a, **k):
        calls.append(1)
        return original(*a, **k)
    monkeypatch.setattr(gate, 'references', references)
    with gate.paths_scope(targets):
        for target_path in targets:
            gate.paths([target_path], lambda: None)
    assert len(calls) == 2


@pytest.mark.parametrize('attachment', ['create', 'row'])
def test_unreferenced_path_write_waits_for_new_card_attachment(env, monkeypatch, attachment):
    target_path = env.path.with_name('race.md')
    new_id = F + '.0095-M'
    if attachment == 'row':
        documents.create({'doc_id':new_id,'project_id':'flowgate','module':'v02','group_id':F,'type_code':'M','title':'racer','seq':95})
    entered, release, attempted, reached_namespace = (threading.Event() for _ in range(4))
    original = gate.lock_path_namespace
    def namespace():
        if threading.current_thread().name.startswith('raw'):
            reached_namespace.set()
        return original()
    monkeypatch.setattr(gate, 'lock_path_namespace', namespace)
    def attach():
        def body():
            if attachment == 'create':
                documents.create({'doc_id':new_id,'project_id':'flowgate','module':'v02','group_id':F,'type_code':'M','title':'racer','seq':95,
                                  'file_path':paths.to_storage_relative(target_path,'flowgate')})
                files.write_new_file(target_path,b'owned',exclude_doc_ids=[new_id])
            else:
                documents.update(new_id, {'file_path':paths.to_storage_relative(target_path,'flowgate')})
                files.overwrite_file(target_path,b'owned')
            target.repository.insert(F,'new-card','report','M',new_id,True,0,'user')
            entered.set()
            assert release.wait(10)
        flow_guard.with_flow_guard(F,body)
    def raw():
        attempted.set()
        denied(lambda: files.overwrite_file(target_path,b'overwritten'))
    with ThreadPoolExecutor(max_workers=1) as creator, ThreadPoolExecutor(max_workers=1,thread_name_prefix='raw') as writer:
        first = creator.submit(attach)
        if not entered.wait(10):
            release.set()
            first.result(1)
            pytest.fail('creator did not reach guarded staging')
        second = writer.submit(raw)
        assert attempted.wait(5) and reached_namespace.wait(5)
        try:
            assert not second.done()
        finally:
            release.set()
        first.result(10); second.result(10)
    assert target_path.read_bytes() == b'owned'


def prepare_backfill(env, monkeypatch, revision=1):
    env.db._execute('UPDATE documents SET type_code=?, revision_no=? WHERE doc_id=?', ['TR2',revision,D])
    monkeypatch.setattr(tr2, 'canonical_path_for_doc', lambda _:env.path)
    monkeypatch.setattr(tr2, '_decode_canonical', lambda *a, **k:{})
    destination = env.path.parent/'revisions'/'backfill.json'
    monkeypatch.setattr(tr2, 'snapshot_path', lambda *a:destination)
    return documents.get_by_id(D), destination


@pytest.mark.parametrize('existing', [False, True])
def test_backfill_fast_paths_acquire_no_boundary_lock(env, monkeypatch, existing):
    doc, destination = prepare_backfill(env,monkeypatch,1 if existing else 0)
    if existing:
        document_revisions.create_once({'doc_id':D,'revision_no':1,'backup_path':'existing.json','edit_reason':'user_comment',
                                       'created_by':'user','created_at':connection.now_iso()})
    monkeypatch.setattr(boundary, 'enter', lambda *a:pytest.fail('read acquired write boundary'))
    legacy_snapshots.tr2_backfill(doc)
    assert not destination.exists()


def test_backfill_rechecks_fresh_revision_and_changes_nothing(env,monkeypatch):
    doc, destination = prepare_backfill(env,monkeypatch)
    original = boundary.enter
    def enter(doc, mode, body):
        env.db._execute('UPDATE documents SET revision_no=2 WHERE doc_id=?',[D])
        return original(doc,mode,body)
    monkeypatch.setattr(boundary,'enter',enter)
    legacy_snapshots.tr2_backfill(doc)
    assert not destination.exists() and document_revisions.list_by_doc(D) == []


@pytest.mark.parametrize('failure', ['none','orphan','database','replacement'])
def test_backfill_exclusive_create_and_owned_rollback(env,monkeypatch,failure):
    doc, destination = prepare_backfill(env,monkeypatch)
    before = env.path.read_bytes()
    if failure == 'orphan':
        destination.parent.mkdir(); destination.write_bytes(b'orphan')
    original = document_revisions.create_once
    if failure in {'database','replacement'}:
        def fail(data):
            if failure == 'replacement':
                replacement = destination.with_suffix('.new'); replacement.write_bytes(b'foreign')
                replacement.replace(destination)
            raise RuntimeError('injected row failure')
        monkeypatch.setattr(document_revisions,'create_once',fail)
    if failure == 'none':
        legacy_snapshots.tr2_backfill(doc)
        assert destination.read_bytes() == before
        assert len(document_revisions.list_by_doc(D)) == 1
    else:
        with pytest.raises((RuntimeError,FileExistsError)):
            legacy_snapshots.tr2_backfill(doc)
        assert document_revisions.list_by_doc(D) == []
        if failure == 'database': assert not destination.exists()
        else: assert destination.read_bytes() == (b'orphan' if failure == 'orphan' else b'foreign')
    assert env.path.read_bytes() == before and documents.get_by_id(D) == doc


@pytest.mark.parametrize('group', [None,F])
def test_upload_preflights_late_card_before_first_overwrite(env,monkeypatch,group):
    from modules.flow_gate.api.v1 import file_transfer_routes as transfer
    from modules.flow_gate.services import tr2_file_policy
    first = env.path.with_name('first.txt');first.write_bytes(b'first')
    form = FormData([('group_id',group or ''),('files[]',UploadFile(io.BytesIO(b'changed'),filename=first.name)),
                     ('files[]',UploadFile(io.BytesIO(b'bad'),filename=env.path.name))])
    class Request:
        headers = {}
        async def form(self):return form
    monkeypatch.setattr(transfer,'_check_project_auth',lambda *a:{})
    monkeypatch.setattr(transfer,'_get_src_root',lambda *a:env.path.parent)
    @contextmanager
    def mutation(*a,**k):
        yield SimpleNamespace(exact_targets=[(first.name,first),(env.path.name,env.path)])
    monkeypatch.setattr(tr2_file_policy,'general_source_mutation',mutation)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(transfer.upload_files(Request(),'flowgate'))
    assert caught.value.status_code == 409
    assert first.read_bytes() == b'first' and env.path.read_text() == HEADER+'original\n'


@pytest.mark.parametrize('op', ['write','patch','move','remove'])
@pytest.mark.parametrize('cause', ['card','internal'])
def test_remote_tool_preserves_card_gate_and_other_error_mapping(env,monkeypatch,op,cause):
    from modules.flow_gate.services import remote_tool_service as remote
    grant = {'grant_id':'g','project':'flowgate'}
    monkeypatch.setattr(remote,'_authenticate',lambda _:grant)
    monkeypatch.setattr(remote,'_locale_for_grant',lambda _:'en')
    monkeypatch.setattr(remote,'_attempt_source_root',lambda *a:env.path.parent)
    monkeypatch.setattr(remote.db_grants,'get_scopes',lambda _:set(remote.OP_SCOPE.values()))
    for name in ('_validate_paths','_validate_allowed_fields','_validate_required','_log'):
        monkeypatch.setattr(remote,name,lambda *a,**k:None)
    monkeypatch.setattr(remote,'_worker_token_for_grant',lambda _:None)
    monkeypatch.setattr(remote.graph_source_hooks,'binding',lambda _:None)
    def execute(*a):
        if cause == 'internal':raise HTTPException(500,'internal failure')
        files.overwrite_file(env.path,b'bad')
    monkeypatch.setattr(remote,'_execute',execute)
    status,result = remote.handle(op,'verified',{})
    assert status == (409 if cause=='card' else 503)
    assert result['error']['code'] == ('card_document_edit_required' if cause=='card' else 'unavailable')
    assert documents.get_by_id(D)['revision_no'] == 0


@pytest.mark.parametrize('kind', ['batch','dc'])
def test_rollback_move_failure_is_collected_and_status_always_restored(env,tmp_path,monkeypatch,kind):
    from modules.flow_gate import process_service as old
    source=tmp_path/'source';source.mkdir()
    first,second = source/'one',source/'two'; first.write_bytes(b'one');second.write_bytes(b'two')
    destination=tmp_path/'destination'
    doc={'id':1,'doc_id':'legacy','type':'M','status':'open'}
    plan=[({'filename':'one','doc':doc},first,destination/'one'),({'filename':'two','doc':{**doc,'id':2}},second,destination/'two')]
    monkeypatch.setattr(inventory_gate,'migration_plan',lambda *a:plan)
    monkeypatch.setattr(old.db,'get_documents_by_group_id',lambda _ :[])
    states=[];monkeypatch.setattr(old.db,'update_document_status_by_pk',lambda pk,state:states.append((pk,state)))
    monkeypatch.setattr(old.db,'insert_event',lambda *a,**k:None)
    original=inventory_gate.shutil.move
    def move(src,dst):
        if Path(src)==second or Path(src)==destination/'one':raise OSError('move/restore fails')
        return original(src,dst)
    monkeypatch.setattr(inventory_gate.shutil,'move',move)
    result = (inventory_gate.batch_migrate(F,[],str(destination),'accepted') if kind=='batch' else
              inventory_gate.dc_approve(F,'dc',[]))
    assert not result['success'] and len(result['rollback_errors']) == 1
    if kind=='dc': assert states == [(1,'accepted'),(1,'open')]
    assert second.read_bytes() == b'two'


@pytest.mark.parametrize('blocked',[True,False])
def test_cancel_reserves_duplicate_basenames_and_checks_actual_second_destination(env,tmp_path,monkeypatch,blocked):
    from modules.flow_gate import process_service as old
    first,second=tmp_path/'t'/'same.md',tmp_path/'tr'/'same.md'
    first.parent.mkdir();second.parent.mkdir();first.write_bytes(b't');second.write_bytes(b'tr')
    cancelled=tmp_path/'cancelled';cancelled.mkdir()
    if blocked:
        # A missing file is still an existing card body reference. It must be
        # checked as the second reserved destination before the first move.
        env.db._execute('UPDATE documents SET file_path=? WHERE doc_id=?',
                       [paths.to_storage_relative(cancelled/'same_2.md','flowgate'),D])
    doc={'id':1,'doc_id':'t','type':'T','status':'accepted','group_id':F}
    child={'id':2,'doc_id':'tr','type':'TR','status':'accepted'}
    monkeypatch.setattr(old.db,'get_document_by_pk',lambda _:doc)
    monkeypatch.setattr(old.db,'get_created_memo_file',lambda d:d)
    monkeypatch.setattr(old,'_find_file_in_accept',lambda name:str(first if name=='t' else second))
    monkeypatch.setattr(old.db,'get_documents_by_target_id',lambda *a,**k:[child])
    monkeypatch.setattr(old.db,'CANCELLED_DIR',str(cancelled))
    states=[]
    monkeypatch.setattr(old.db,'update_document_status_by_pk',lambda *a:states.append(a))
    monkeypatch.setattr(old.db,'insert_event',lambda *a,**k:None)
    monkeypatch.setattr(old.db,'update_group_updated_at',lambda *a:None)
    if blocked:
        denied(lambda:old.cancel_document(1,'reason','discard'))
        assert first.read_bytes()==b't' and second.read_bytes()==b'tr'
        assert not (cancelled/'same.md').exists() and states==[]
    else:
        result=old.cancel_document(1,'reason','discard')
        assert result['status']=='success' and states==[(1,'cancelled'),(2,'cancelled')]
        assert (cancelled/'same.md').read_bytes()==b't'
        assert (cancelled/'same_2.md').read_bytes()==b'tr'


def test_numbering_preflight_includes_card_references_from_other_projects(env,monkeypatch):
    from modules.flow_gate.numbering import migration_service as migration
    projects.create({'project_id':'other','project_name':'other'})
    monkeypatch.setattr(paths,'project_root',lambda project:env.path.parent)
    before=env.db._fetch_all('SELECT * FROM groups')
    denied(lambda:migration._apply_reformat('other','group',4,5))
    assert env.db._fetch_all('SELECT * FROM groups')==before


@pytest.mark.parametrize('port_available',[True,False])
def test_legacy_malformed_events_keep_real_token_author_and_console_fallback(env,monkeypatch,port_available):
    token = issue_token(env)
    doc=documents.get_by_id(D)
    monkeypatch.setattr(settings,'document_row',lambda _:documents.get_by_id(D))
    if not port_available:ports.unregister(ports.DOCUMENT_AUTHOR_EVIDENCE)
    for meta in ['broken',json.dumps({'doc_id':D,'revision_no':99})]:
        env.db._execute('INSERT INTO workflow_events (event_type,project_id,group_id,document_id,actor_user_id,metadata,created_at) VALUES (?,?,?,?,?,?,?)',
                        ['doc_edited','flowgate',F,doc['id'],'user',meta,'2026-01-01'])
    env.db._execute('INSERT INTO workflow_events (event_type,project_id,group_id,document_id,actor_user_id,metadata,created_at) VALUES (?,?,?,?,?,?,?)',
                   ['token_consumed','flowgate',F,doc['id'],'user',json.dumps({'doc_id':D,'token_id':token['token_id']}),'2026-01-02'])
    assert settings._written_by(D,None)=={'author':'ai','provider_id':'provider-test'}
    from modules.flow_gate.db.events import insert_event
    insert_event(D,'doc_body_saved',note=json.dumps({'source':'console_save'}))
    assert settings._written_by(D,None)=={'author':'person','provider_id':None}


def issue_token(env,action='edit',doc_id=D):
    env.db._execute("INSERT INTO ai_providers(provider_id,name,exec_type,kind,enabled,created_at,updated_at) "
                    "VALUES ('provider-test','Test','api','openai',1,'t','t')")
    return tokens.create({'token_id':'tok-review-'+action,'hash':'hash-'+action,'pepper_id':'test','project':'flowgate',
                          'group_id':F,'doc_ref':doc_id,'action_scope':action,'issued_to':'user','created_at':connection.now_iso(),
                          'expires_at':'2099-01-01','ai_run_id':'run-review','provider_id':'provider-test'})


@pytest.mark.parametrize('operation',['save','create'])
def test_tr2_verified_ai_token_is_recorded_as_agent_and_resolved_by_real_token_reader(env,monkeypatch,operation):
    from test_card_document_edit_dedicated_0127 import tr2_env
    raw = tr2_env(env, monkeypatch)
    token=issue_token(env,'new' if operation=='create' else 'edit')
    if operation=='save':
        tr2.save(D,raw,actor='user',expected_revision=0,origin=tr2.ORIGIN_AI,token_id=token['token_id'])
    else:
        env.path.unlink()
        initial = documents.get_by_id(D)
        env.db._execute('DELETE FROM documents WHERE doc_id=?',[D])
        with env.db.transaction():
            documents.create(initial)
            tr2.create(D,raw,actor='user',origin=tr2.ORIGIN_AI,token_id=token['token_id'])
    metadata=json.loads(env.db._fetch_one("SELECT metadata FROM workflow_events WHERE event_type='doc_edited'")['metadata'])
    assert metadata['origin']=='agent' and metadata['actor_kind']=='worker'
    assert metadata['token_id']==token['token_id'] and metadata['revision_no']==1
    monkeypatch.setattr(settings,'document_row',lambda _:documents.get_by_id(D))
    assert settings._written_by(D,None)=={'author':'ai','provider_id':'provider-test'}


@pytest.mark.parametrize('field,value',[('project_id','other'),('branch','other')])
def test_body_resolution_columns_are_sensitive(env,field,value):
    before=documents.get_by_id(D)
    denied(lambda:documents.update(D,{field:value}))
    assert documents.get_by_id(D)==before


def test_create_stage_rejects_initialized_body_and_mismatched_target_without_evidence(env):
    new_id=F+'.0094-M'; destination=env.path.with_name('first.md')
    def body():
        doc=documents.create({'doc_id':new_id,'project_id':'flowgate','module':'v02','group_id':F,'type_code':'M','title':'staged','seq':94,
                              'file_path':paths.to_storage_relative(destination,'flowgate')})
        denied(lambda:routes.stage_body_save(doc,relative='different.md',size=1,actor_user_id='user',source='test',canvas_event=False))
        files.write_new_file(destination,b'first',exclude_doc_ids=[new_id])
        assert gate.creation(new_id).initialized and not gate.creation(new_id).staged
        denied(lambda:routes.stage_body_save(doc,relative=doc['file_path'],size=5,actor_user_id='user',source='test',canvas_event=False))
    flow_guard.with_flow_guard(F,body)
    assert env.db._fetch_all('SELECT * FROM events WHERE doc_id=?',[new_id])==[]


def test_concurrent_backfills_create_one_exclusive_snapshot(env,monkeypatch):
    doc, destination = prepare_backfill(env,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(legacy_snapshots.tr2_backfill,doc) for _ in range(2)]
        for future in futures:future.result(10)
    assert len(document_revisions.list_by_doc(D))==1
    assert destination.read_bytes()==env.path.read_bytes()


@pytest.mark.parametrize('operation',['remote_file','remote_tree','managed_tree','asset','journal','marker','storage_delete'])
def test_expanded_inventory_delete_and_replace_entries_refuse_card_before_mutation(env,monkeypatch,operation):
    from modules.flow_gate.services import remote_tool_service as remote, test_asset_service
    from modules.flow_gate.storage import filesystem, migration
    from flowgate.graph_time_machine import git_revert
    before=env.path.read_bytes()
    if operation=='storage_delete':
        inbox=env.root/'storage'/'inbox'; inbox.mkdir()
        os.link(env.path,inbox/'alias.md')
    monkeypatch.setattr(git_revert,'_marker_path',lambda *a:env.path)
    calls={
      'remote_file':lambda:remote._delete_file(env.path),
      'remote_tree':lambda:remote._delete_tree(env.path.parent),
      'managed_tree':lambda:filesystem.remove_managed_tree(env.path.parent,env.root),
      'asset':lambda:test_asset_service._replace_bytes(env.path,b'bad'),
      'journal':lambda:filesystem._lock_file(env.path),
      'marker':lambda:git_revert._unmark(env.root,'owned'),
      'storage_delete':lambda:migration.delete_legacy(str(env.root/'storage'),str(env.root/'old.db')),
    }
    denied(calls[operation])
    assert env.path.read_bytes()==before
    assert not list(env.path.parent.glob('.*.fg-asset-*'))


def test_store_path_attachment_serializes_with_unreferenced_raw_write(env,monkeypatch):
    from flowgate.card_document_edit import dedicated_bridge
    from flowgate.card_document_edit.contracts import DedicatedRequest, Actor, Origin, Built
    destination=env.path.with_name('common-store-race.md')
    cas_entered, release, namespace_reached = (threading.Event() for _ in range(3))
    execute, namespace = env.db._execute_affected, gate.lock_path_namespace
    def sql(statement,params=None):
        result=execute(statement,params)
        if statement.startswith('UPDATE documents SET revision_no = revision_no + 1'):
            cas_entered.set()
            assert release.wait(10)
        return result
    def lock():
        if threading.current_thread().name.startswith('raw'):namespace_reached.set()
        return namespace()
    monkeypatch.setattr(env.db,'_execute_affected',sql)
    monkeypatch.setattr(gate,'lock_path_namespace',lock)
    class Rule:
        needs_current=True
        def precheck(self,doc):pass
        def conflict(self,doc):return HTTPException(409,'revision conflict')
        def build(self,doc,body):return Built(b'common store',target_path=destination)
    request=DedicatedRequest(D,0,Actor('user'),Rule(),Origin('designer'))
    with ThreadPoolExecutor(max_workers=1) as editor, ThreadPoolExecutor(max_workers=1,thread_name_prefix='raw') as raw:
        saved=editor.submit(dedicated_bridge.dedicated_save,request)
        if not cas_entered.wait(10):
            release.set();saved.result(1);pytest.fail('CAS not reached')
        writer=raw.submit(lambda:denied(lambda:files.overwrite_file(destination,b'bad')))
        try:
            assert namespace_reached.wait(5) and not writer.done()
        finally:release.set()
        saved.result(10);writer.result(10)
    assert destination.read_bytes()==b'common store'
    assert documents.get_by_id(D)['revision_no']==1
