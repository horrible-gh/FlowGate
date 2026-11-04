"""0127 T#4: legacy gates, physical aliases, CREATE authority and current author evidence."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_card_document_edit_0127 import env, F, D, HEADER, business_state, payload
from modules.flow_gate.db import documents
from modules.flow_gate.graph_workflow.services import card_document_ports as ports, flow_guard
from modules.flow_gate.storage import files, filesystem, paths
from modules.flow_gate.documents.routers import documents as routes
from flowgate.card_document_edit import write_gate as gate, author_evidence, integration, target, boundary
from flowgate.card_document_edit.contracts import EVENT_SCHEMA, Built


def denied(call):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 409
    assert caught.value.detail == 'card_document_edit_required'


@pytest.mark.parametrize('updates', [{'status':'open'}, {'doc_review_status':'pending_review'}, {'meta':'{}'}, {'title':'new title'}, {'superseded_by':None}])
def test_state_review_relation_update_is_outside_gate_even_when_unavailable(env, monkeypatch, updates):
    ports.unregister(ports.DOCUMENT_WRITE_GATE)
    monkeypatch.setattr(target, 'classify_card_slot', lambda _: pytest.fail('metadata must not classify'))
    result = documents.update(D, updates)
    assert all(result[k] == v for k, v in updates.items())
    assert result['revision_no'] == 0 and env.path.read_text() == HEADER + 'original\n'


@pytest.mark.parametrize('classification', [target.CARD, target.UNAVAILABLE])
@pytest.mark.parametrize('field,value', [('file_path','new.md'), ('filename','new.md'), ('revision_no',1)])
def test_generic_row_cannot_change_body_columns(env, monkeypatch, classification, field, value):
    before = business_state(env)
    monkeypatch.setattr(target, 'classify_card_slot', lambda _: classification)
    denied(lambda: documents.update(D, {field:value}))
    assert business_state(env) == before


def test_ports_missing_and_classification_exception_fail_closed(env, monkeypatch):
    before = business_state(env)
    ports.unregister(ports.DOCUMENT_WRITE_GATE)
    denied(lambda: files.overwrite_file(env.path, b'forged'))
    denied(lambda: documents.update(D, {'file_path':'new.md'}))
    integration.register()
    monkeypatch.setattr(target.repository, 'available', lambda: False)
    denied(lambda: files.overwrite_file(env.path, b'forged'))
    assert business_state(env) == before


@pytest.mark.parametrize('operation', ['overwrite','replace','new','stage','file_move','tree_move'])
def test_low_level_none_writers_cannot_change_card(env, operation, tmp_path):
    before = business_state(env)
    destination = env.path.with_name('moved.md')
    operations = {
        'overwrite': lambda: files.overwrite_file(env.path, b'new'),
        'replace': lambda: files.replace_restorably(env.path, b'new', files.file_identity(env.path)),
        'new': lambda: files.write_new_file(env.path, b'new', exclude_doc_ids=[D]),
        'stage': lambda: routes.stage_body_save(documents.get_by_id(D), relative=documents.get_by_id(D)['file_path'], size=3,
                                                actor_user_id='user', source='console_save', write_file=lambda: pytest.fail('file callback')),
        'file_move': lambda: filesystem.safe_rename(env.path, destination),
        'tree_move': lambda: filesystem.move_subtree(env.path.parent, tmp_path/'moved'),
    }
    denied(operations[operation])
    assert business_state(env) == before and not destination.exists()


@pytest.mark.parametrize('alias', ['hardlink','symlink','missing'])
def test_aliases_and_missing_references_are_guarded(env, tmp_path, alias):
    other = tmp_path/'unrelated-name.bin'
    if alias == 'hardlink':
        os.link(env.path, other)
    elif alias == 'symlink':
        other.symlink_to(env.path)
    else:
        env.path.unlink()
        other = env.path
    denied(lambda: ports.write_gate('paths', [other], lambda: other.write_bytes(b'bad')))
    assert documents.get_by_id(D)['revision_no'] == 0
    if alias != 'missing':
        assert env.path.read_text() == HEADER + 'original\n'
    else:
        assert not other.exists()


def test_row_path_cannot_be_repointed_onto_another_card_body(env, tmp_path):
    other = 'flowgate.v02.0127.0099-M'
    documents.create({'doc_id':other,'project_id':'flowgate','module':'v02','group_id':F,'type_code':'M','seq':99,'title':'memo'})
    denied(lambda: documents.update(other, {'file_path': documents.get_by_id(D)['file_path']}))
    assert documents.get_by_id(other)['file_path'] is None


def test_fresh_classification_is_checked_under_guard(env, monkeypatch):
    seen = []
    def classify(doc):
        seen.append(flow_guard.held_guard())
        return target.CARD if flow_guard.held_guard() else target.NON_CARD
    monkeypatch.setattr(target, 'classify_card_slot', classify)
    before = env.path.read_bytes()
    denied(lambda: files.overwrite_file(env.path, b'new'))
    assert seen[0] is None and ('flow', F) in seen
    assert env.path.read_bytes() == before


@pytest.mark.parametrize('method', ['content','rpc','brief','spec','spec_non_ts'])
def test_v1_card_routes_refuse_before_build_target_or_stage(env, monkeypatch, method):
    monkeypatch.setattr(routes, '_reject_if_group_disposed', lambda _: None)
    monkeypatch.setattr(routes, '_reject_if_group_ai_running', lambda _: None)
    monkeypatch.setattr(routes, '_save_target', lambda _: pytest.fail('target resolved before gate'))
    # Permission decoration is separately covered by the real composition suite.
    unwrap = lambda fn: getattr(fn, '__wrapped__', fn)
    calls = {
      'content': lambda: unwrap(routes.update_document_content)(D, routes.DocumentContentUpdate(content='bad'), {'user_id':'user'}),
      'rpc': lambda: unwrap(routes.update_document_content_rpc)(routes.DocumentContentUpdateRpc(doc_id=D,content='bad'), {'user_id':'user'}),
      'brief': lambda: unwrap(routes.save_test_brief)(D, routes.TestBriefSaveBody(text='bad', expected_content='stale'), {'user_id':'user'}),
      'spec': lambda: unwrap(routes.save_test_spec)(D, routes.TestSpecSaveBody(cases=[]), {'user_id':'user'}),
      'spec_non_ts': lambda: unwrap(routes.save_test_spec)(D, routes.TestSpecSaveBody(cases=[]), {'user_id':'user'}),
    }
    if method == 'spec':
        env.db._execute('UPDATE documents SET type_code = ? WHERE doc_id = ?', ['TS',D])
    denied(calls[method])
    assert documents.get_by_id(D)['revision_no'] == 0


def test_create_proof_exists_only_for_insert_in_current_transaction(env):
    new_id = 'flowgate.v02.0127.0098-M'
    dest = env.path.with_name('created.md')
    def create_then_write():
        doc = documents.create({'doc_id':new_id,'project_id':'flowgate','module':'v02','group_id':F,'type_code':'M','seq':98,
                                'title':'created','file_path':paths.to_storage_relative(dest,'flowgate')})
        assert gate.creation(new_id) is not None
        routes.stage_body_save(doc, relative=doc['file_path'], size=5, actor_user_id='user', source='test', canvas_event=False,
                               write_file=lambda: files.write_new_file(dest, b'first', exclude_doc_ids=[new_id]))
        assert gate.creation(new_id).initialized
        # A second fill is a revision even during the same outer creation transaction.
        denied(lambda: routes.stage_body_save(doc, relative=doc['file_path'], size=6, actor_user_id='user', source='test'))
    flow_guard.with_flow_guard(F, create_then_write)
    assert gate.creation(new_id) is None and dest.read_bytes() == b'first'
    # Old r0 is never CREATE, including a forged new_document flag.
    fake = SimpleNamespace(current=documents.get_by_id(D), built=Built(b'x',target_path=env.path),body=SimpleNamespace(path=env.path))
    def forge():
        ctx = boundary.Context(D, None, ('flow',F), boundary.CREATE)
        denied(lambda: _enter_revision(ctx, fake))
    flow_guard.with_flow_guard(F, forge)


def _enter_revision(ctx, inp):
    with gate.revision(ctx, inp):
        pytest.fail('forged CREATE accepted')


def test_create_cannot_adopt_missing_referenced_card_path(env):
    doc = documents.get_by_id(D)
    env.path.unlink()
    denied(lambda: documents.create({'doc_id':'flowgate.v02.0127.0097-M','project_id':'flowgate','module':'v02',
                                    'group_id':F,'type_code':'M','seq':97,'file_path':doc['file_path']}))


def test_non_card_legacy_file_save_and_metadata_update_remain_available(env, monkeypatch):
    monkeypatch.setattr(target, 'classify_card_slot', lambda _: target.NON_CARD)
    files.overwrite_file(env.path, b'legacy')
    assert env.path.read_bytes() == b'legacy'
    doc = documents.update(D, {'filename':'kept.md'})
    assert doc['filename'] == 'kept.md' and doc['revision_no'] == 0


def test_batch_rejects_late_card_before_moving_first_source(env, tmp_path, monkeypatch):
    from modules.flow_gate import process_service as old
    from flowgate.card_document_edit import inventory_gate
    source = tmp_path/'source'; source.mkdir()
    first = source/'first.md'; first.write_bytes(b'first')
    dest = tmp_path/'dest'
    plan = [({'filename':'first.md'},first,dest/'first.md'),({'filename':env.path.name},env.path,dest/'second.md')]
    monkeypatch.setattr(inventory_gate,'migration_plan',lambda *a:plan)
    denied(lambda: old._batch_migrate(F, [], str(dest), 'accepted'))
    assert first.read_bytes() == b'first' and not dest.exists()


def test_tree_missing_descendant_and_hardlink_outside_tree_are_protected(env, tmp_path):
    root = tmp_path/'source-tree'; root.mkdir()
    linked = root/'alias'; os.link(env.path,linked)
    denied(lambda: filesystem.move_subtree(root,tmp_path/'target-tree'))
    linked.unlink()
    doc = documents.get_by_id(D)
    env.path.unlink()
    # Its containing tree is checked even though there are no files to enumerate.
    denied(lambda: filesystem.move_subtree(env.path.parent,tmp_path/'target-tree'))
    assert documents.get_by_id(D) == doc


def author_doc():
    return {'doc_id':D, 'id':123, 'revision_no':4}


def event(origin='designer', actor_kind='person', **changes):
    metadata = {'schema':EVENT_SCHEMA, 'doc_id':D,'revision_no':4,'origin':origin,'actor_kind':actor_kind, **changes}
    return {'event_type':'doc_edited','document_id':123,'actor_user_id':'user','metadata':json.dumps(metadata)}


@pytest.mark.parametrize('origin,actor,expected', [
 ('designer','person','person'),('wp_window','person','person'),('wp_restore','person','person'),('tr2_window','person','person'),
 ('chat','person_delegated','unknown'),('wp_deploy','person','unknown'),('recovery','person','unknown'),
 ('test_run','person','unknown'),('root_conversion','person','unknown'),('designer','worker','unknown')])
def test_current_author_schema_does_not_use_actor_user_alone(origin, actor, expected):
    result = author_evidence.current(author_doc(), [event(origin,actor)], lambda _:pytest.fail('unexpected token'))
    assert result == {'author':expected,'provider_id':None}


def test_agent_author_uses_only_event_token_and_corruption_never_falls_back():
    row = event('agent','worker',token_id='verified-token')
    result = author_evidence.current(author_doc(),[row], lambda token:{'author':'ai','provider_id':token})
    assert result['provider_id'] == 'verified-token'
    assert author_evidence.current(author_doc(),[row,row],lambda _:pytest.fail('conflicting evidence'))['author'] == 'unknown'
    assert author_evidence.current(author_doc(),[event(revision_no='4')],lambda _:None)['author'] == 'unknown'
    assert author_evidence.current(author_doc(),[event(doc_id='wrong')],lambda _:None)['author'] == 'unknown'
    assert author_evidence.current(author_doc(),[event(revision_no=3)],lambda _:None) is None
    assert author_evidence.current(author_doc(),[{'event_type':'doc_edited','document_id':123,'metadata':'broken'}],lambda _:None) is None
    assert author_evidence.current(author_doc(),[{'event_type':'doc_edited','document_id':123,'metadata':'{"schema":"card_document_edit/v1",broken'}],lambda _:None)['author'] == 'unknown'


def test_new_author_evidence_wins_over_later_console_and_token_events(env, monkeypatch):
    from flowgate.card_settings import legacy_adapter as settings
    doc = documents.get_by_id(D)
    meta = json.loads(event('chat','person_delegated',revision_no=0)['metadata'])
    env.db._execute('INSERT INTO workflow_events (event_type,project_id,group_id,document_id,actor_user_id,metadata,created_at) VALUES (?,?,?,?,?,?,?)',
                    ['doc_edited','flowgate',F,doc['id'],'user',json.dumps(meta),'2026-01-01T00:00:00+09:00'])
    from modules.flow_gate.db.events import insert_event
    insert_event(D, 'doc_body_saved', note=json.dumps({'source':'console_save'}))
    env.db._execute('INSERT INTO workflow_events (event_type,project_id,group_id,document_id,actor_user_id,metadata,created_at) VALUES (?,?,?,?,?,?,?)',
                    ['token_consumed','flowgate',F,doc['id'],'user',json.dumps({'doc_id':D,'token_id':'stale-author-token'}),'2026-11-04T00:00:00+09:00'])
    monkeypatch.setattr(settings,'_token_author',lambda _: {'author':'person','provider_id':None})
    monkeypatch.setattr(settings,'document_row',lambda _:documents.get_by_id(D))
    assert settings._written_by(D,None)['author'] == 'unknown'
    assert settings._written_by(D,'trusted-contact-token')['author'] == 'person'
    ports.unregister(ports.DOCUMENT_AUTHOR_EVIDENCE)
    assert settings._written_by(D,None)['author'] == 'unknown'

@pytest.mark.parametrize('entry', ['remote_patch','remote_write','transfer','wp_primitive','tr2_primitive','source_apply','git_resolve','git_restore'])
def test_source_and_generated_primitive_entries_cannot_write_card(env, entry, tmp_path, monkeypatch):
    from modules.flow_gate.services import remote_tool_service as remote, work_plan_service as wp
    from modules.flow_gate.api.v1 import file_transfer_routes as transfer
    from modules.flow_gate.documents import tr2_apply_adapter, tr2_service
    from modules.flow_gate.services.git import conflict, rerere_checkpoint
    root, name = env.path.parent, env.path.name
    before = env.path.read_bytes()
    first = root/'first.txt'; first.write_bytes(b'first')
    calls = {
      'remote_patch': lambda: remote._atomic_write_bytes(env.path,b'bad'),
      'remote_write': lambda: remote._exec_write({'path':name,'content':'bad'},root),
      'transfer': lambda: transfer._save_file(str(env.path),b'bad'),
      'wp_primitive': lambda: wp.write_body_atomically(env.path,{}),
      'tr2_primitive': lambda: tr2_service._write_bytes_atomically(env.path,b'bad'),
      'source_apply': lambda: tr2_apply_adapter._atomic_bytes(env.path,b'bad'),
      'git_resolve': lambda: conflict._write_resolved_file(root,name,env.path,'bad'),
      'git_restore': lambda: rerere_checkpoint._restore(root,{'first.txt':b'changed',name:b'bad'},set()),
    }
    denied(calls[entry])
    assert env.path.read_bytes() == before and first.read_bytes() == b'first'


def test_number_reformat_and_snapshot_restore_reject_before_any_sql(env, tmp_path, monkeypatch):
    from modules.flow_gate.numbering import migration_service as migration
    before = business_state(env)
    denied(lambda: migration._apply_reformat('flowgate','document',4,5))
    snapshot = tmp_path/'snapshot.json'
    snapshot.write_text(json.dumps({'groups':[{'group_id':F}], 'documents':[documents.get_by_id(D)]}),encoding='utf-8')
    denied(lambda: migration._restore_from_snapshot(snapshot))
    assert business_state(env) == before


def test_storage_copy_preflights_all_targets_before_first_copy(env, tmp_path):
    from modules.flow_gate.storage import migration
    source = tmp_path/'legacy'; (source/'inbox').mkdir(parents=True)
    (source/'inbox'/'first.txt').write_bytes(b'first')
    (source/'inbox'/env.path.name).write_bytes(b'bad')
    destination = env.root/'storage'/'inbox'; destination.mkdir()
    os.link(env.path, destination/env.path.name)
    before = env.path.read_bytes()
    denied(lambda: migration._copy_storage(str(source),str(env.root/'storage'),False))
    assert not (destination/'first.txt').exists() and env.path.read_bytes() == before



def test_cancel_preflights_source_and_destination_before_header_or_status(env, tmp_path, monkeypatch):
    from modules.flow_gate import process_service as old
    doc = {'id':1,'doc_id':D,'group_id':F,'type':'N','status':'accepted'}
    monkeypatch.setattr(old.db,'get_document_by_pk',lambda _:doc)
    monkeypatch.setattr(old.db,'get_created_memo_file',lambda _:env.path.name)
    monkeypatch.setattr(old,'_find_file_in_accept',lambda _:str(env.path))
    monkeypatch.setattr(old.db,'update_document_status_by_pk',lambda *a:pytest.fail('status changed before gate'))
    monkeypatch.setattr(old.db,'INBOX_DIR',str(tmp_path/'inbox'))
    before = env.path.read_bytes()
    denied(lambda: old.cancel_document(1,'cancel','resubmit'))
    assert env.path.read_bytes() == before


def test_raw_stage_ignores_http_style_authority_flags(env):
    # A caller cannot bypass using a source string, exclude_doc_ids or new_document flag.
    doc = documents.get_by_id(D)
    denied(lambda: routes.stage_body_save({**doc,'new_document':True,'context':'CREATE'},relative=doc['file_path'],
        size=3, actor_user_id='user',source='CREATE',write_file=lambda:pytest.fail('forged authority')))


def test_prepared_legacy_batch_preserves_sequential_filenames(env, tmp_path):
    from flowgate.card_document_edit import inventory_gate
    from modules.flow_gate import process_service as old
    source = tmp_path/'sources'; source.mkdir()
    for name in ('first.md','second.md'):
        (source/name).write_bytes(name.encode())
    entries = [{'filename':name,'doc_type':'M','source_dir':str(source)} for name in ('first.md','second.md')]
    plan = inventory_gate.migration_plan(old,F,entries,str(tmp_path/'target'))
    assert '_M001_' in plan[0][2].name and '_M002_' in plan[1][2].name
    result = old._batch_migrate(F,entries,str(tmp_path/'target'),'accepted')
    assert result['success'] and len(result['results']) == 2
    assert [Path(row['migrated_to']).read_bytes() for row in result['results']] == [b'first.md',b'second.md']


def test_state_only_field_allowlist_ignores_body_fields_without_port(env, monkeypatch):
    ports.unregister(ports.DOCUMENT_WRITE_GATE)
    monkeypatch.setattr(target,'classify_card_slot',lambda _:pytest.fail('metadata should not classify'))
    before = documents.get_by_id(D)
    documents.update_document_fields(D, file_path='bad',filename='bad',revision_no=99,status='open')
    after = documents.get_by_id(D)
    assert after['status'] == 'open'
    assert all(after[k] == before[k] for k in ('file_path','filename','revision_no'))


def test_non_card_project_operation_keeps_its_locked_row_after_group_reassignment(env):
    target.repository.delete(F,'research','instruction','N')
    def reassign():
        env.db._execute('UPDATE documents SET group_id = NULL WHERE doc_id = ?', [D])
        files.overwrite_file(env.path,b'legacy numbering')
    ports.write_gate('project','flowgate',reassign)
    assert env.path.read_bytes() == b'legacy numbering' and documents.get_by_id(D)['revision_no'] == 0
    assert gate._project_rows.get() is None


@pytest.mark.parametrize('new_group',[None,'flowgate.v02.9999'])
def test_group_metadata_cannot_hide_an_existing_card_reference(env,new_group):
    from modules.flow_gate.db import groups
    if new_group:
        groups.create({'group_id':new_group,'project_id':'flowgate','module':'v02','title':'other','status':'OPEN'})
    documents.update(D,{'group_id':new_group})
    assert target.classify_card_slot(documents.get_by_id(D)) == target.UNAVAILABLE
    denied(lambda: files.overwrite_file(env.path,b'group bypass'))
    denied(lambda: documents.update(D,{'revision_no':9}))
    assert documents.get_by_id(D)['revision_no'] == 0


def test_missing_author_interpreter_does_not_trust_mismatched_row_identity(monkeypatch):
    monkeypatch.setattr(ports,'_handlers',{})
    row = {**event(), 'document_id':999}
    assert ports.author_evidence(author_doc(),[row],lambda _:pytest.fail('legacy fallback'))['author'] == 'unknown'


@pytest.mark.parametrize('method',['brief','spec'])
def test_non_card_structured_v1_keeps_legacy_type_error_order(env,method):
    target.repository.delete(F,'research','instruction','N')
    documents.update(D,{'status':'cancelled'})
    unwrap = lambda fn: getattr(fn,'__wrapped__',fn)
    with pytest.raises(HTTPException) as caught:
        if method == 'brief':
            unwrap(routes.save_test_brief)(D,routes.TestBriefSaveBody(text='bad',expected_content='stale'),{'user_id':'user'})
        else:
            unwrap(routes.save_test_spec)(D,routes.TestSpecSaveBody(cases=[]),{'user_id':'user'})
    assert caught.value.status_code == 422 and caught.value.detail['error'] == 'not_ts'


def test_unbound_document_without_group_is_proven_non_card(env):
    doc = documents.create({'doc_id':'flowgate.v02.0127.0096-M','project_id':'flowgate','module':'v02',
                            'group_id':None,'type_code':'M','seq':96,'title':'unbound'})
    assert target.classify_card_slot(doc) == target.NON_CARD
