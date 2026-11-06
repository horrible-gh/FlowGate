"""0116: live settings transactions and deterministic admission/evaluation races.

No provider subprocess or network call is made. Real repositories and SQLite FK
and rollback semantics run behind fake workers, leases and token revocation.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from fastapi import HTTPException

from test_ai_settings_delete_0095 import store, system, cli, db, svc
from modules.flow_gate.db import ai_evaluations as evaluations
from modules.flow_gate.services import ai_evaluation_service as evaluation
from modules.flow_gate.services.ai_invoke import admission, runtime, review
from modules.flow_gate.utils.provider_lifecycle import guard


@pytest.fixture
def world(store, monkeypatch):
    revoked, released, started = [], [], []
    monkeypatch.setattr(admission.token_service, 'revoke', lambda token, **kw: revoked.append(token))
    monkeypatch.setattr(admission.db_group_ai_leases, 'release', lambda *a, **kw: released.append(a))
    monkeypatch.setattr(evaluation, '_RUNS', {})
    monkeypatch.setattr(evaluation, '_emit', lambda *a: None)
    monkeypatch.setattr(evaluation.connection, '_effective', lambda _: {
        'source': 'system', 'providers': [svc._to_view(r) for r in db.list_scope(None) if r['enabled']]})

    class Worker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def start(self):
            started.append(self.kwargs)
    monkeypatch.setattr(evaluation, 'threading', SimpleNamespace(Thread=Worker))

    def insert_loop(run):
        loop = run['document_review_loop']
        store._execute('INSERT INTO ai_invoke_document_review_loops VALUES (?,?,?,?,?)',
                       [run['run_id'], 'review', None, loop['reviewer_provider_id'], loop['rework_provider_id']])
    monkeypatch.setattr(runtime._svc(), '_insert_document_review_loop', insert_loop)
    return SimpleNamespace(store=store, revoked=revoked, released=released, started=started)


def run_record(loop=None, run_id='run'):
    return dict(run_id=run_id, group_id='g', status='running', provider={}, token_id='token',
                handoff_gate=runtime.HandoffGate(), document_review_loop=loop)


def api(name):
    return dict(name=name, exec_type='api', kind='custom', enabled=True, api_model='fake',
                api_base_url='https://fake.invalid', api_key='not-a-real-secret')


@contextmanager
def contender():
    """Start the second operation while the first holds guard, prove contention,
    then let it run after commit. Events, never scheduler sleeps, control ordering.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = []
        def launch(function):
            assert not futures
            attempted = threading.Event()
            def compete():
                acquired = guard.acquire(blocking=False)
                if acquired:
                    guard.release()
                attempted.set()
                assert not acquired, 'primary operation did not hold shared guard'
                return function()
            futures.append(pool.submit(compete))
            assert attempted.wait(3), 'contender did not reach guard'
        yield launch, futures


def test_tombstone_preserves_history_and_cleans_only_current_references(world, monkeypatch):
    items = system([api('past'), cli('keep')])['providers']
    pid = items[0]['id']
    for uid in ['alice', 'bob']:
        world.store._execute('INSERT INTO user_ai_defaults VALUES (?,?)', [uid, pid])
    world.store._execute('INSERT INTO ai_provider_doctype_map VALUES (?,?)', [pid, 'TR'])
    world.store._execute('INSERT INTO ai_invoke_paused_chains VALUES (?,?,?)', ['g', pid, pid])
    world.store._execute('INSERT INTO ai_group_members VALUES (?,?)', ['group', pid])
    world.store._execute('INSERT INTO tokens VALUES (?,?)', ['old-token', pid])
    world.store._execute('INSERT INTO ai_invoke_runs VALUES (?,?)', ['old-run', pid])
    world.store._execute('INSERT INTO document_reviews VALUES (?,?,?)', ['doc', pid, 'original review'])
    world.store._execute('INSERT INTO ai_invoke_document_review_loops VALUES (?,?,?,?,?)',
                         ['old-loop', 'stopped', 'review_passed', pid, pid])
    history_tables = ['tokens', 'ai_invoke_runs', 'document_reviews', 'ai_group_members',
                      'ai_invoke_document_review_loops']
    before = {t: world.store._fetch_all('SELECT * FROM '+t) for t in history_tables}
    assert system(items[1:])['default_provider_id'] == items[1]['id']
    assert before == {t: world.store._fetch_all('SELECT * FROM '+t) for t in history_tables}
    row = world.store._fetch_one('SELECT * FROM ai_providers WHERE provider_id=?', [pid])
    assert row['deleted_at'] and row['name'] == 'past' and row['enabled'] == 0
    for field in ['api_key', 'cli_command', 'api_base_url', 'api_model']:
        assert row[field] is None
    assert db.get_secret(None, pid) is None and db.get_system_row(pid) is None
    assert db.get_name_any(pid) == 'past' and pid in db.all_provider_ids()
    assert all(r['provider_id'] is None for r in world.store._fetch_all('SELECT * FROM user_ai_defaults'))
    assert world.store._fetch_all('SELECT * FROM ai_provider_doctype_map') == []
    paused = world.store._fetch_one('SELECT * FROM ai_invoke_paused_chains')
    assert paused['continuation_base_provider_id'] is paused['continuation_work_executor_provider_id'] is None
    # A stale user default is defensively ignored even if reintroduced by legacy data.
    world.store._execute('UPDATE user_ai_defaults SET provider_id=?', [pid])
    assert svc._user_default_db.get('alice') is None
    monkeypatch.setattr(review, '_enabled_provider_chain', lambda _: [])
    assert review._provider_name_of(None, pid) == 'past'
    from modules.flow_gate.settings import workspace_service
    monkeypatch.setattr(workspace_service, '_store', lambda: world.store)
    assert ('provider', pid) not in workspace_service._resource_names()
    from modules.flow_gate.services import ai_group_service
    assert ai_group_service._connection(pid, {}, {})['status'] == 'broken'
    # Repeated PUT neither changes the tombstone nor revives it. Same name -> fresh identity.
    system(items[1:])
    assert world.store._fetch_one('SELECT * FROM ai_providers WHERE provider_id=?', [pid]) == row
    new = system([items[1], {**api('past'), 'id': pid}])['providers'][1]
    assert new['id'] != pid


@pytest.mark.parametrize('exec_type', ['api', 'cli'])
@pytest.mark.parametrize('delete_index', [0, 1])
def test_registered_chain_protects_head_and_fallback_before_secret_lookup(world, exec_type, delete_index):
    items = system([api('A'), api('B')] if exec_type == 'api' else [cli('A'), cli('B')])['providers']
    run = run_record()
    admission._register_run(run, items)
    assert run['chain_provider_ids'] == [p['id'] for p in items]
    remaining = [p for i,p in enumerate(items) if i != delete_index]
    with pytest.raises(db.ProviderInUseError) as exc:
        system([{**remaining[0], 'name': 'must roll back'}])
    assert exc.value.reason == 'active_run'
    assert svc.get_system_settings()['providers'] == items
    if exec_type == 'api':
        assert db.get_secret(None, items[delete_index]['id']) == 'not-a-real-secret'
    run['status'] = 'finished'
    assert len(system(remaining)['providers']) == 1


@pytest.mark.parametrize('state', ['acquiring','active','releasing'])
def test_live_lease_token_is_a_supporting_marker(world, state):
    item = system([cli()])['providers'][0]
    world.store._execute('INSERT INTO tokens VALUES (?,?)', ['token', item['id']])
    world.store._execute('INSERT INTO group_ai_leases VALUES (?,?,?)', ['run','token',state])
    with pytest.raises(db.ProviderInUseError, match='provider_in_use'):
        system([])
    world.store._execute('DELETE FROM group_ai_leases')
    assert system([])['providers'] == []  # historical token alone does not block


@pytest.mark.parametrize('first', [0,1])
@pytest.mark.parametrize('same_ai', [False,True])
def test_deleted_loop_role_outside_start_chain_refuses_before_loop_insert(world, first, same_ai):
    items = system([cli('A'),cli('B')])['providers']
    a,b = [p['id'] for p in items]
    loop = dict(reviewer_provider_id=a, rework_provider_id=a if same_ai else b)
    chain = [items[first if not same_ai else 0]]
    deleted = a if same_ai else items[1-first]['id']
    system([p for p in items if p['id'] != deleted])
    run = run_record(loop)
    with pytest.raises(HTTPException) as exc:
        admission._register_run(run, chain)
    assert exc.value.status_code == 422
    assert world.revoked == ['token'] and len(world.released) == 1
    assert runtime._svc()._runs == {}
    assert world.store._fetch_all('SELECT * FROM ai_invoke_document_review_loops') == []


@pytest.mark.parametrize('first_operation', ['delete','admit'])
def test_general_admission_and_deletion_are_serialized(world, monkeypatch, first_operation):
    items = system([api('A'),api('tail')])['providers']
    run = run_record()
    with contender() as (launch,futures):
        if first_operation == 'admit':
            original = db.get_system_row
            def recheck(pid):
                if not futures:
                    launch(lambda: system(items[:1]))
                return original(pid)
            monkeypatch.setattr(db, 'get_system_row', recheck)
            admission._register_run(run, items)
            with pytest.raises(db.ProviderInUseError) as exc:
                futures[0].result(timeout=3)
            assert exc.value.reason == 'active_run'
            assert db.get_secret(None,items[1]['id'])
        else:
            original = world.store._execute
            def tombstone(sql, params=None):
                if 'SET deleted_at' in sql:
                    launch(lambda: admission._register_run(run,items))
                return original(sql, params)
            monkeypatch.setattr(world.store, '_execute', tombstone)
            system(items[:1])
            with pytest.raises(HTTPException):
                futures[0].result(timeout=3)
            assert world.revoked == ['token'] and len(world.released) == 1
            assert runtime._svc()._runs == {}


@pytest.mark.parametrize('state', evaluations.ACTIVE)
@pytest.mark.parametrize('role', ['target','judge'])
def test_evaluation_active_states_protect_both_roles_and_allow_unrelated_delete(world,state,role):
    items = system([cli('target'),cli('judge'),cli('unrelated')])['providers']
    row = evaluations.create_pending(target_id=items[0]['id'],target_name='target',
        judge_id=items[1]['id'],judge_name='judge',created_by=None)
    if state != 'pending':
        assert evaluations.advance(row['evaluation_id'],state)
    system(items[:2])
    pid = items[0 if role=='target' else 1]['id']
    with pytest.raises(db.ProviderInUseError) as exc:
        system([p for p in items[:2] if p['id'] != pid])
    assert exc.value.reason == 'active_evaluation'


@pytest.mark.parametrize('state', evaluations.TERMINAL)
def test_terminal_evaluation_history_allows_deletion(world,state):
    items=system([cli('target'),cli('judge')])['providers']
    row=evaluations.create_pending(target_id=items[0]['id'],target_name='target',
        judge_id=items[1]['id'],judge_name='judge',created_by=None)
    eid=row['evaluation_id']
    if state=='completed':
        evaluations.advance(eid,'reviewing')
        evaluations.complete(eid,total_score=80,review_summary='original',item_scores=[])
    else:
        evaluations.finish(eid,lifecycle=state,reason_code='fixture')
    before=evaluations.get(eid)
    assert system([])['providers']==[]
    assert evaluations.get(eid)==before


@pytest.mark.parametrize('role', ['target','judge'])
@pytest.mark.parametrize('first_operation', ['delete','evaluate'])
def test_evaluation_start_delete_race_both_orders(world,monkeypatch,role,first_operation):
    items=system([api('target'),api('judge')])['providers']
    pid=items[0 if role=='target' else 1]['id']
    keep=[p for p in items if p['id']!=pid]
    start=lambda: evaluation.start(items[0]['id'],items[1]['id'],None)
    with contender() as (launch,futures):
        if first_operation=='evaluate':
            original=evaluation.connection.resolve_subject
            def resolve(*a,**kw):
                subject=original(*a,**kw)
                if a[1]==items[1]['id'] and not futures:
                    launch(lambda:system(keep))
                return subject
            monkeypatch.setattr(evaluation.connection,'resolve_subject',resolve)
            row=start()
            assert row['lifecycle']=='pending' and len(world.started)==1
            with pytest.raises(db.ProviderInUseError) as exc:
                futures[0].result(timeout=3)
            assert exc.value.reason=='active_evaluation'
            # Each target/judge stage still resolves the same frozen subject and key.
            monkeypatch.setattr(evaluation.connection,'execute_prompt',lambda *a,**kw:
                                runtime.ProbeOutcome(runtime.PROBE_OK,'fake response'))
            state=evaluation._RUNS[row['evaluation_id']]
            for key in ['judge','target','judge']:
                assert evaluation._call(state[key],'prompt',state['handle'],1,Path('.'),'run')=='fake response'
        else:
            original=world.store._execute
            def tombstone(sql,params=None):
                if 'SET deleted_at' in sql:
                    launch(start)
                return original(sql,params)
            monkeypatch.setattr(world.store,'_execute',tombstone)
            system(keep)
            with pytest.raises(evaluation.EvaluationError) as exc:
                futures[0].result(timeout=3)
            assert exc.value.code=='provider_not_found'
            assert world.started==[] and evaluation._RUNS=={}
            assert world.store._fetch_all('SELECT * FROM ai_evaluations')==[]


@pytest.mark.parametrize('failure', ['handle','emit','thread'])
def test_pending_exception_settles_failed_and_releases_registry(world,monkeypatch,failure):
    items=system([cli('target'),cli('judge')])['providers']
    def fail(*a,**kw): raise RuntimeError('injected startup failure')
    if failure=='handle':monkeypatch.setattr(evaluation.probe,'ProbeHandle',fail)
    elif failure=='emit':monkeypatch.setattr(evaluation,'_emit',fail)
    else:monkeypatch.setattr(evaluation,'threading',SimpleNamespace(Thread=fail))
    with pytest.raises(RuntimeError,match='injected'):
        evaluation.start(items[0]['id'],items[1]['id'],None)
    assert evaluation._RUNS=={}
    row=world.store._fetch_one('SELECT * FROM ai_evaluations')
    assert row['lifecycle']=='failed' and row['active_dedupe_key'] is None
    assert system([])['providers']==[]


def test_preserved_loop_guards_both_stage_transitions(world):
    items=system([cli('reviewer'),cli('reworker')])['providers']
    run=run_record(dict(reviewer_provider_id=items[0]['id'],rework_provider_id=items[1]['id']))
    admission._register_run(run,items[:1])
    for stage in ['review','rework','review']:
        world.store._execute('UPDATE ai_invoke_document_review_loops SET current_stage=?',[stage])
        for pid in [p['id'] for p in items]:
            with pytest.raises(db.ProviderInUseError) as exc:
                system([p for p in items if p['id']!=pid])
            assert exc.value.reason=='active_loop'
            assert db.get_system_row(pid)['cli_command']=='fake-cli --prompt'
    run['status']='finished'
    world.store._execute("UPDATE ai_invoke_document_review_loops SET current_stage='stopped',stop_reason='review_passed'")
    assert system([])['providers']==[]


@pytest.mark.parametrize('first', [0,1])
@pytest.mark.parametrize('deleted_before_registration', [False,True])
def test_real_start_run_checks_other_loop_role_before_launch(world,monkeypatch,tmp_path,first,deleted_before_registration):
    """Drive actual start_run from chain resolution through the worker handoff."""
    from modules.flow_gate.services import provider_capability_service
    items=system([cli('reviewer'),cli('reworker')])['providers']
    service=runtime._svc()
    monkeypatch.setattr(admission.ai_settings_service,'resolve_effective',lambda _: {
        'providers':items,'source':'system','registered_count':2})
    monkeypatch.setattr(provider_capability_service,'capability_finding',lambda *a:None)
    monkeypatch.setattr(admission.git_service,'get_branch_merge_group_claim',lambda _:None)
    monkeypatch.setattr(admission,'_refuse_frozen_group',lambda _:None)
    monkeypatch.setattr(admission.db_docs,'get_by_id',lambda _:dict(type='B',branch='main',revision_no=0))
    monkeypatch.setattr(admission.db_docs,'get_group_max_seq',lambda _:0)
    monkeypatch.setattr(service,'_require_group_worktree',lambda *a,**kw:tmp_path)
    monkeypatch.setattr(service,'_ensure_initial_source_sync',lambda *a,**kw:None)
    monkeypatch.setattr(service,'_next_run_id',lambda:'aiv_20261101_999999')
    monkeypatch.setattr(service,'request_run_scratch_sweep',lambda *a:None)
    monkeypatch.setattr(service,'_create_scratch',lambda *a:tmp_path)
    monkeypatch.setattr(service,'compute_review_baseline',lambda _:dict(
        starts_with_rework=bool(first),review_baseline_id=0,baseline_revision_no=0))
    # flowgate.v02.0136 T0004 T#1: start_run now binds a review loop's first token to the run
    # before registration and refuses the start when it cannot (fail-closed). The tokens here are
    # fakes ('fake'/'token') the real token_service cannot verify, so that binding is stubbed like
    # the rest of this test's token world (revoke is stubbed in `world`). The binding itself is
    # covered with real tokens by tests/flowgate/test_review_loop_rework_binding_0136.py.
    monkeypatch.setattr(service,'_adopt_continuation_token',lambda run,raw,**_kw:True)
    monkeypatch.setattr(admission.db_group_ai_leases,'get_active',lambda _:None)
    monkeypatch.setattr(admission.db_group_ai_leases,'acquire',lambda **kw:dict(run_id=kw['run_id']))
    monkeypatch.setattr(admission.db_group_ai_leases,'activate',lambda *a,**kw:dict(run_id=a[1]))
    monkeypatch.setattr(admission.storage_paths,'resolve_project_src_root',lambda *a,**kw:tmp_path)
    monkeypatch.setattr(admission,'_inject_hop_notes',lambda mention,*a,**kw:mention)
    monkeypatch.setattr(admission.review,'resolve_review_count',lambda *a:0)
    monkeypatch.setattr(admission.chat_run_hooks,'chat_run_start',lambda *a:None)
    monkeypatch.setattr(admission,'threading',SimpleNamespace(Thread=evaluation.threading.Thread, Event=threading.Event))
    other=items[1-first]['id']
    def issue_builder(**kw):
        # Resolution already narrowed the chain to one role, but neither loop nor
        # run exists yet. Deletion therefore commits before the final fence.
        if deleted_before_registration:
            system([p for p in items if p['id']!=other])
        return dict(raw_token='fake',token_id='token',mention='test',scratch_dir=str(tmp_path))
    kwargs=dict(project_id='p',module='v02',group_id='g',doc_ref='p.v02.0116.0001-B',
        action_scope='new',mode='single',continuation_target_seq=None,continuation_review_mode=False,
        continuation_instruction_mode=None,continuation_locale=None,issued_to='u',api_base_url='http://fake.invalid',
        mention_builder=lambda *a:'test',issue_builder=issue_builder,
        document_review_loop=dict(reviewer_provider_id=items[0]['id'],rework_provider_id=items[1]['id'],
                                  total_timeout_sec=3600,rework_timeout_sec=1800,review_count=1))
    if deleted_before_registration:
        with pytest.raises(HTTPException) as exc:admission.start_run(**kwargs)
        assert exc.value.status_code==422
        assert world.started==[] and service._runs=={}
        assert world.revoked==['token'] and len(world.released)==1
        assert world.store._fetch_all('SELECT * FROM ai_invoke_document_review_loops')==[]
    else:
        result=admission.start_run(**kwargs)
        assert result['ok'] and len(world.started)==1
        run=service._runs[result['run_id']]
        assert run['chain_provider_ids']==[items[first]['id']]
        with pytest.raises(db.ProviderInUseError) as exc:
            system([p for p in items if p['id']!=other])
        assert exc.value.reason=='active_loop'


def test_evaluation_control_without_shared_guard_reproduces_unavailable(world,monkeypatch):
    """Negative control: the same interleaving fails with the old unlocked start."""
    from contextlib import nullcontext
    from modules.flow_gate.utils import provider_lifecycle
    items=system([cli('target'),cli('judge')])['providers']
    original=evaluation.connection.resolve_subject
    with ThreadPoolExecutor(max_workers=1) as pool:
        def resolve(*a,**kw):
            subject=original(*a,**kw)
            if a[1]==items[1]['id']:
                pool.submit(lambda:system(items[:1])).result(timeout=3)
            return subject
        monkeypatch.setattr(evaluation.connection,'resolve_subject',resolve)
        monkeypatch.setattr(provider_lifecycle,'guard',nullcontext())
        row=evaluation.start(items[0]['id'],items[1]['id'],None)
        state=evaluation._RUNS[row['evaluation_id']]
        with pytest.raises(evaluation._StageFailed) as exc:
            evaluation._call(state['judge'],'prompt',state['handle'],1,Path('.'),'control')
        assert exc.value.reason_code=='provider_became_unavailable'


def test_deleted_default_cannot_be_saved_and_paused_resume_uses_remaining_chain(world,monkeypatch):
    items=system([cli('old'),cli('remaining')])['providers']
    pid=items[0]['id']
    world.store._execute('INSERT INTO ai_invoke_paused_chains VALUES (?,?,?)',['paused',pid,pid])
    system(items[1:])
    with pytest.raises(svc.AiSettingsValidationError) as exc:
        svc.save_user_default('alice',pid)
    assert exc.value.errors==[{'field':'default_provider_id','reason':'bad_default'}]
    # Resume's effective/default resolution sees the remaining head after both
    # continuation pins have been cleared, rather than the removed provider.
    monkeypatch.setattr(svc._projects_db,'get_by_id',lambda _:dict(project_id='p'))
    from modules.flow_gate.services import provider_capability_service
    monkeypatch.setattr(provider_capability_service,'provider_capabilities',lambda *a,**kw:{})
    from modules.flow_gate.settings import workspace_service
    monkeypatch.setattr(workspace_service,'allowed_resource_ids',lambda *a:None)
    effective=svc.resolve_effective('p')
    assert [p['id'] for p in effective['providers']]==[items[1]['id']]
    run=run_record()
    admission._register_run(run,effective['providers'])
    assert run['chain_provider_ids']==[items[1]['id']]


def test_request_cache_cannot_hide_a_concurrent_tombstone(world,monkeypatch):
    from modules.flow_gate.db import request_cache
    from modules.flow_gate.db.connection import FlowGateStore
    items=system([cli('A')])['providers']
    pid=items[0]['id']
    primary=threading.get_ident()
    read_store=FlowGateStore()
    read_store._db=SimpleNamespace(fetch_one=world.store._fetch_one)
    monkeypatch.setattr(db,'get_store',lambda:read_store if threading.get_ident()==primary else world.store)
    monkeypatch.setenv('FLOWGATE_REQUEST_CACHE','1')
    with request_cache.request_scope():
        assert db.get_system_row(pid) is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda:system([])).result(timeout=3)
        # Another request's writes cannot invalidate this request's earlier read.
        assert db.get_system_row(pid) is not None
        with pytest.raises(HTTPException):
            admission._register_run(run_record(),items)
        assert db.get_system_row(pid) is None


def test_no_output_retry_retains_provider_after_rejected_delete(world,monkeypatch):
    from modules.flow_gate.services.ai_invoke import worker
    items=system([api('head'),api('tail')])['providers']
    run=run_record()
    run.update(project_id='p',attempts_used=1,attempts_max=2,
               continuation_selected_provider_id=items[1]['id'])
    admission._register_run(run,items)
    with pytest.raises(db.ProviderInUseError):system(items[:1])
    monkeypatch.setattr(worker.ai_settings_service,'resolve_effective',lambda _:dict(providers=items))
    assert worker._retry_provider_chain(run)==items[1:]
    assert db.get_secret(None,items[1]['id'])=='not-a-real-secret'


def test_recovered_orphan_evaluation_no_longer_blocks_deletion(world):
    items=system([cli('target'),cli('judge')])['providers']
    row=evaluations.create_pending(target_id=items[0]['id'],target_name='target',
        judge_id=items[1]['id'],judge_name='judge',created_by=None)
    assert evaluations.recover_orphans()==1
    assert evaluations.get(row['evaluation_id'])['lifecycle']=='failed'
    assert system([])['providers']==[]


def test_connection_test_is_not_a_capability_evaluation_protection_marker(world):
    from modules.flow_gate.services import ai_connection_test_service as connection
    items=system([cli('probe')])['providers']
    pid=items[0]['id']
    subject=connection.resolve_subject(evaluation.SCOPE,pid,fingerprint=False)
    world.store._execute('CREATE TABLE ai_evaluation_runs(evaluation_id TEXT,provider_id TEXT,lifecycle TEXT)')
    world.store._execute('INSERT INTO ai_evaluation_runs VALUES (?,?,?)',['test',pid,'running'])
    assert system([])['providers']==[]
    assert not connection.still_effective(subject)
    assert world.store._fetch_one('SELECT lifecycle FROM ai_evaluation_runs')['lifecycle']=='running'


def test_snapshot_history_keeps_deleted_provider_name(world,monkeypatch):
    from modules.flow_gate.services import snapshot_request_service as snapshots
    item=system([cli('historic name')])['providers'][0]
    system([])
    monkeypatch.setattr(snapshots,'resolve_run_provenance',lambda *a,**kw:{})
    data=snapshots.request_data_for_run(dict(run_id='old',provider_id=item['id']),{},dict(reason='fixture'))
    assert data['provider_id']==item['id'] and data['actual_provider_name']=='historic name'


def test_author_revision_loop_keeps_reviewer_protected(world):
    item=system([cli('reviewer')])['providers'][0]
    loop=dict(reviewer_provider_id=item['id'],rework_provider_id=None)
    assert review.admit_author_revision_loop(loop,'review')
    assert loop['rework_provider_id']==item['id']
    admission._register_run(run_record(loop),[item])
    with pytest.raises(db.ProviderInUseError) as exc:system([])
    assert exc.value.reason=='active_loop'
