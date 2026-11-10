"""0010-L 5.6: literal SP expectations, T1/T2/T3, seven paths plus two AI identities.

Graph persistence and provider/approval endpoints are controlled fixtures. The
binder, manual-stage guards, duplicate judgement, review driver, approval CAS,
legacy review policy and bounded retry policy are the production functions.
Never derive expected counts from the function being tested.
"""
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from l3verify_support_0127 import (l3, kind, FLOW, PROJECT, USER, PATHS, documents, contacts,
    flow_guard, emit, unchanged)
from flowgate.card_document_edit import boundary
from flowgate.card_settings import manual_stage as manual, assigned_review as ar, stage_assignment as sa
from flowgate.card_settings import review_stage as review, legacy_adapter as legacy
from modules.flow_gate.graph_workflow.services import result_binder as binder, card_settings_ports


# ID, configuration, new review assignments, review starts, approvals, result starts, B-1
SP=[
 ('SP-0',{'active':False},0,0,0,0,0),
 ('SP-0-owner',{'graph_owned':False},0,0,0,0,0),
 ('SP-0-stop',{'refused':True},0,0,0,0,0),
 ('SP-1',{'node_status':'done'},0,0,0,0,0),
 ('SP-2a',{'actor':'ai'},1,1,0,0,0),
 ('SP-2b-setup',{'actor':'wait_setup'},1,0,0,0,0),
 ('SP-2b-unavailable',{'actor':'wait_unavailable'},1,0,0,0,0),
 ('SP-2c',{'actor':'no_review'},1,0,1,0,0),
 ('SP-2d',{'actor':'unknown'},1,0,0,0,0),
 ('SP-2e',{'actor':'wait_setup','reason':'setting_unreadable'},1,0,0,0,1),
 ('SP-3',{'existing':True,'loop':True,'actor':'ai','old_basis':True},0,0,0,0,0),
 ('SP-4',{'existing':True,'actor':'no_review'},0,0,1,0,0),
 ('SP-4-done',{'existing':True,'actor':'no_review','judged':True,'doc_status':'approved'},0,0,0,0,0),
 ('SP-4-approval-recovery',{'existing':True,'actor':'no_review','judged':True},0,0,1,0,0),
 ('SP-5',{'doc_status':'approved'},0,0,0,0,0),
 ('SP-6',{'stage_actor':'ai','stage_state':'done'},0,0,0,0,0),
 ('SP-7-setup',{'stage_actor':'wait_setup'},0,0,0,0,1),
 ('SP-7-unavailable',{'stage_actor':'wait_unavailable'},0,0,0,0,1),
 ('SP-7-failed',{'stage_state':'failed'},0,0,0,0,1),
 ('SP-8-instruction',{'required':'instruction_not_approved'},0,0,0,0,1),
 ('SP-8-question',{'questions':True},0,0,0,0,0),
 ('SP-8-old',{'attempt_current':False},0,0,0,0,0),
 ('SP-9a-review',{'legacy':True,'doc_status':'rejected','review_count':1},0,0,0,0,0),
 ('SP-9a-approve',{'legacy':True,'doc_status':'rejected','review_count':0},0,0,1,0,0),
 ('SP-9a-assigned',{'doc_status':'rejected','actor':'ai'},1,1,0,0,0),
 ('SP-10-ai',{'legacy':True,'uses_ai':True,'review_count':0},0,0,0,0,0),
 ('SP-10-no-result',{'legacy':True,'card':'R'},0,0,0,0,0),
 ('SP-10-stopped',{'legacy':True,'stage_open':False},0,0,0,0,0),
 ('SP-11',{'legacy':True,'review_count':1},0,1,0,0,0),
 ('SP-11-no-ai',{'legacy':True,'review_count':1,'reviewer':False},0,0,0,0,0),
 ('SP-11-loop',{'legacy':True,'review_count':1,'loop':True},0,0,0,0,0),
 ('SP-11-judged',{'legacy':True,'review_count':1,'judged':True},0,0,0,0,0),
 ('SP-12-run',{'legacy':True,'card':'T','review_count':0},0,0,1,0,0),
 ('SP-12-test',{'legacy':True,'card':'TS','review_count':0},0,0,1,0,0),
 ('SP-12-plan',{'legacy':True,'card':'WP','review_count':0},0,0,1,0,0),
 ('SP-12-plan-denied',{'legacy':True,'card':'WP','review_count':0,'approval_allowed':False},0,0,0,0,1),
 ('SP-13a',{'instruction':True,'actor':'ai'},1,1,0,0,0),
 ('SP-13b-setup',{'instruction':True,'actor':'wait_setup'},1,0,0,0,0),
 ('SP-13b-unavailable',{'instruction':True,'actor':'wait_unavailable'},1,0,0,0,0),
 ('SP-13c',{'instruction':True,'actor':'no_review'},1,0,1,1,0),
 ('SP-13d',{'instruction':True,'existing':True,'actor':'no_review'},0,0,1,1,0),
 ('SP-13e',{'instruction':True,'actor':'wait_setup','reason':'setting_unreadable'},1,0,0,0,1),
 ('SP-14-legacy',{'instruction':True,'legacy':True},0,0,0,0,0),
 ('SP-14-ai',{'instruction':True,'stage_actor':'ai'},0,0,0,0,0),
 ('SP-14-failed',{'instruction':True,'stage_state':'failed'},0,0,0,0,0),
 ('SP-14-invalidated',{'instruction':True,'invalidated':True},0,0,0,0,0),
]


class AssignmentRows:
    INTENT_APPROVAL='approval'
    INTENT_EXECUTION='execution'
    def __init__(self): self.rows={}
    def read(self,attempt,stage,seq): return self.rows.get((stage,seq))
    def latest(self,attempt,stage):
        rows=[a for (s,n),a in self.rows.items() if s==stage]
        return max(rows,key=lambda a:a['seq']) if rows else None
    def for_node(self,*a): return list(self.rows.values())
    def update_state(self,attempt,stage,seq,previous,state,**kw):
        row=self.read(attempt,stage,seq)
        if row is None or row['stage_state']!=previous:return False
        row.update(stage_state=state,**kw)
        return True
    def set_intent(self,attempt,stage,seq,kind,exec_id=None):
        self.read(attempt,stage,seq).update(intent_kind=kind,intent_exec_id=exec_id)
    def ledger_exec_for(self,*a): return None


class Probe:
    def __init__(self,h,mp,config):
        self.h,self.mp,self.c=h,mp,config
        self.assignments,self.reviews,self.approvals,self.results,self.waits=[],[],[],[],[]
        self.trigger=[]
        self.rows=AssignmentRows()
        self.stage=sa.INSTRUCTION if config.get('instruction') else sa.RESULT
        self.review_stage=sa.INSTRUCTION_REVIEW if config.get('instruction') else sa.RESULT_REVIEW
        self.run={'run_id':'l3verify-graph-run','group_id':FLOW,'started_by':USER}
        self.node={'node_key':h.node['node_key'],'node_type':'document','doc_type':config.get('card','T')}
        self.row={'node_key':h.node['node_key'],'status':config.get('node_status','open' if config.get('instruction') else 'review'),
            'stage':self.stage,'instruction_doc_id':h.doc_id if config.get('instruction') else None,
            'result_doc_id':None if config.get('instruction') else h.doc_id}
        self.attempt='l3verify-attempt'
        self.rows.rows[(self.stage,0)]={'attempt_id':self.attempt,'stage':self.stage,'seq':0,
            'actor':config.get('stage_actor','person'),'stage_state':config.get('stage_state','active'),
            'run_id':self.run['run_id'],'node_key':self.node['node_key'],'reused_doc_id':h.doc_id}
        if config.get('existing'):
            a=self.new_assignment(FLOW,self.run,self.node,self.attempt,self.review_stage,1,
                author='person',basis={'doc_id':h.doc_id,'revision_no':7 if config.get('old_basis') else 8,'rejection_count':0})
            if config.get('loop'): a['intent_kind']='execution'
            if config.get('judged'): a.update(verdict='auto_approved',stage_state='done')
            self.assignments.clear()
        h.graph={'run':self.run,'node':self.row,'assignments':self.rows.rows,'waits':self.waits}
        self.wire()

    def new_assignment(self,flow,run,node,attempt,stage,seq,*,author,basis,**kw):
        a={'attempt_id':attempt,'run_id':run['run_id'],'node_key':node['node_key'],'stage':stage,'seq':seq,
           'actor':self.c.get('actor','ai'),'actor_reason':self.c.get('reason'),'source':'store_error',
           'stage_state':'waiting' if self.c.get('actor','ai').startswith('wait') else 'active',
           'author':author,'basis_doc_id':basis['doc_id'],'basis_revision_no':basis['revision_no'],
           'basis_rejection_count':basis['rejection_count'],'provider_id':'l3verify-reviewer',
           'review_rounds':1,'rework_provider_id':None,'cause':'l3verify-unavailable'}
        self.rows.rows[(stage,seq)]=a
        self.assignments.append(a)
        return a

    @contextmanager
    def hold(self,*a,**kw): yield self.c.get('stage_open',True)

    def wire(self):
        p,c,h=self.mp,self.c,self.h
        for module in (sa,ar,manual): p.setattr(module,'store',self.rows)
        p.setattr(sa,'enabled',lambda:True)
        p.setattr(sa,'is_legacy_attempt',lambda a:c.get('legacy',False))
        p.setattr(sa,'start_stage',self.new_assignment)
        p.setattr(sa,'stage_plan',lambda a:{'instruction_type':h.kind if c.get('instruction') else None,'result_type':'NR'})
        p.setattr(legacy,'active_run',lambda f:self.run if c.get('active',True) else None)
        p.setattr(legacy,'run_by_id',lambda r:self.run)
        p.setattr(legacy,'node_run',lambda *a:self.row)
        p.setattr(legacy,'node_runs',lambda *a:[self.row])
        p.setattr(legacy,'run_node_row',lambda *a:self.node)
        p.setattr(legacy,'node_of_key',lambda *a:self.node)
        p.setattr(legacy,'open_row',lambda *a:self.row if self.row['status']=='open' else None)
        attempt_reads=[0]
        def current_attempt(*a):
            attempt_reads[0]+=1
            # The contact captured the original attempt; the deferred job sees its replacement.
            return self.attempt if c.get('attempt_current',True) or attempt_reads[0]==1 else 'l3verify-replaced'
        p.setattr(legacy,'active_attempt_id',current_attempt)
        p.setattr(legacy,'person_stage_open',lambda *a:c.get('stage_open',True))
        p.setattr(legacy,'person_hold',self.hold)
        p.setattr(legacy,'explicit_request',self.hold)
        p.setattr(legacy,'document_row',documents.get_by_id)
        p.setattr(legacy,'load_context',lambda *a:None)
        p.setattr(legacy,'open_questions',lambda *a:c.get('questions',False))
        p.setattr(legacy,'invalidated_documents',lambda *a:{h.doc_id} if c.get('invalidated') else set())
        p.setattr(legacy,'taken_judged',lambda *a:False)
        p.setattr(legacy,'loop_of_assignment',lambda *a:{'current_stage':'review'} if c.get('loop') or self.reviews else None)
        p.setattr(legacy,'settle_graph_run',lambda *a:None)
        p.setattr(legacy,'run_issuer',lambda *a:USER)
        p.setattr(legacy,'project_of',lambda *a:PROJECT)
        p.setattr(legacy,'default_reviewer',lambda *a:'l3verify-reviewer' if c.get('reviewer',True) else None)
        p.setattr(legacy,'review_loop_active',lambda *a:c.get('loop',False) or bool(self.reviews))
        p.setattr(legacy,'submission_judged',lambda *a:c.get('judged',False))
        p.setattr(legacy,'approval_wait_rows',lambda *a:list(self.waits))
        p.setattr(legacy,'record_submission_wait',self.record_wait)
        p.setattr(legacy,'set_dispatch_error',lambda r,k,e:self.row.update(dispatch_error_code=e))
        p.setattr(legacy,'clear_dispatch_error',lambda *a:None)
        p.setattr(legacy,'approve',self.approve)
        p.setattr(review,'approve_instruction',self.approve_instruction)
        p.setattr(legacy,'start_result_stage',self.start_result)
        p.setattr(legacy,'start_review_loop',lambda *a,**kw:self.reviews.append({'args':a,'kwargs':kw}))
        p.setattr(review.repository,'available',lambda:True)
        p.setattr(review.repository,'values',lambda *a:{'review_count':c.get('review_count',1),
                                                      'review_ai':'l3verify-reviewer' if c.get('reviewer',True) else None})
        p.setattr(review.settings,'uses_ai',lambda *a:c.get('uses_ai',False))
        p.setattr(review.completion_records,'document',documents.get_by_id)
        p.setattr(manual,'required_result',lambda *a:c.get('required',manual.OK))
        p.setattr(manual,'required_instruction',lambda *a:h.doc_id)
        # External execution-intent storage is a fixture; drive/duplicate/CAS are real.
        p.setattr(ar,'ensure_intent_locked',self.intent)
        p.setattr(binder.run_store,'active_run',lambda *a:self.run if c.get('active',True) else None)
        p.setattr(binder,'read_ownership',lambda *a:SimpleNamespace(state='graph' if c.get('graph_owned',True) else 'legacy'))
        from modules.flow_gate.services import graph_execution_hooks
        p.setattr(graph_execution_hooks,'call',lambda *a:c.get('refused',False))
        p.setattr(binder,'load_context',lambda run,project,actor,jobs:SimpleNamespace(
            flow_id=FLOW,run_id=self.run['run_id'],run=self.run,node_rows={self.node['node_key']:self.node},jobs=jobs))
        p.setattr(binder.run_store,'node_runs',lambda *a:[self.row])
        p.setattr(binder.run_store,'node_run_by_result',lambda r,d:self.row if self.row.get('result_doc_id')==d else None)
        p.setattr(card_settings_ports,'person_result',review.person_result)
        original_instruction=manual.instruction_saved
        def instruction(*a):
            self.trigger.append('registered' if a[-1] else 'content_saved')
            return original_instruction(*a)
        p.setattr(card_settings_ports,'instruction_saved',instruction)
        for name in ('resolve','apply_decision','settle_run'):
            p.setattr(binder,name,lambda *a,**kw:pytest.fail('inbox_edited must not create a node event'))

    def intent(self,flow,run,node,a,doc,mode,after,ctx=None):
        if a.get('intent_kind'):return a
        if a['actor']=='ai':
            a['intent_kind']='execution'
            after.append(lambda:self.reviews.append({'stage':a['stage'],'rework_provider_id':None}))
        elif a['actor']=='no_review': a['intent_kind']='approval'
        return a

    def record_wait(self,flow,key,doc,reason,submission,**kw):
        self.waits.append({'reason':reason,'submission_mark':submission,'stage':kw.get('stage','result')})

    def approve(self,flow,doc,issuer,authority,**kw):
        if not self.c.get('approval_allowed',True):
            assert authority is None, 'PLAN requires issuer permission'
            self.waits.append({'reason':'issuer_lacks_approve'})
            return False
        if 'expected_revision' in kw:
            assert kw['expected_revision']==documents.get_by_id(doc)['revision_no']
        self.approvals.append({'doc':doc,'authority':authority})
        documents.update(doc,{'doc_review_status':'approved'})
        self.row.update(status='done',successor_open=True)
        return True

    def approve_instruction(self,flow,node,doc,issuer,**kw):
        assert kw['expected_revision']==documents.get_by_id(doc)['revision_no']
        self.approvals.append({'doc':doc,'instruction':True})
        documents.update(doc,{'doc_review_status':'approved'})
        return True

    def start_result(self,*a,**kw):
        self.results.append(a)
        self.row['stage']='result'

    def counts(self):
        return [len(x) for x in (self.assignments,self.reviews,self.approvals,self.results,self.waits)]


@pytest.mark.parametrize('via,kind',[(p,p if p in ('WP','TR2') else 'N') for p in (*PATHS,'A-CLI-ai','A-API-ai')])
@pytest.mark.parametrize('scenario',SP,ids=[r[0] for r in SP])
def test_SP_literal_table_T1_T2_T3(l3,monkeypatch,via,kind,scenario):
    name,config,*expected=scenario
    documents.update(l3.doc_id,{'doc_review_status':config.get('doc_status','revised')})
    probe=Probe(l3,monkeypatch,config)
    times={}
    before=l3.observe()
    original=boundary.enter
    def saved(*a,**kw):
        result=original(*a,**kw)
        times.setdefault('T1',l3.observe())
        return result
    monkeypatch.setattr(boundary,'enter',saved)
    def contact(flow,doc,revision,token):
        l3.events.append(('graph',(flow,doc,revision,token)))
        times['T2']=l3.observe()
        assert probe.approvals==[] and probe.results==[]
        binder.on_document_event(binder.EDITED,flow,{'doc_id':doc,'revision_no':revision,'token_id':token})
        times['T3']=l3.observe()
    monkeypatch.setattr(contacts.v01_contact,'after_inbox_edited',contact)
    result=l3.request(via)
    assert result[0]==200,result
    assert set(times)=={'T1','T2','T3'}
    assert times['T1']['O2']['revision_no']==8
    for field in ('status','doc_review_status','owner_id'):
        assert times['T1']['O2'][field]==before['O2'][field]
        assert times['T2']['O2'][field]==times['T1']['O2'][field]
    assert times['T1']['O6']==before['O6']
    assert probe.counts()==expected,(name,via,probe.counts(),expected,[r.getMessage() for r in l3.caplog.records])
    if expected[2]:
        assert times['T3']['O2']['doc_review_status']=='approved'
    else:
        assert times['T3']['O2']['doc_review_status']==times['T2']['O2']['doc_review_status']
    assert all(a['author']=='person' for a in probe.assignments)
    actual_author=legacy.written_by(l3.doc_id)['author']
    expected_author='unknown' if via.startswith('C-') else 'ai' if via.endswith('-ai') else 'person'
    assert actual_author==expected_author,(via,actual_author)
    assert len(times['T1']['O4'])==len(times['T1']['O5'])==1
    if config.get('instruction') and not config.get('refused'):
        assert probe.trigger==['registered' if via.startswith('A-') else 'content_saved']
    if name=='SP-13b-unavailable':
        assert probe.row['dispatch_error_code']=='ai_unavailable:l3verify-unavailable'
    if not expected[2]:
        assert not probe.row.get('successor_open')
    emit(l3,'SP',{'path':via,'scenario':name,'input':config,'expected':expected,'actual':probe.counts(),
        'T0':before['O2']['doc_review_status'],'times':{t:{'O2':o['O2'],'O6':str(o['O6']),'O9':o['O9']} for t,o in times.items()}})


@pytest.mark.parametrize('instruction',[False,True],ids=['SP-2f','SP-13f'])
def test_reconcile_only_bounded_retry(l3,monkeypatch,instruction):
    from flowgate.card_intervention import reasons
    config={'instruction':instruction,'actor':'wait_setup','reason':'setting_unreadable'}
    p=Probe(l3,monkeypatch,config)
    a=p.new_assignment(FLOW,p.run,p.node,p.attempt,p.review_stage,1,author='person',basis=sa.mark(documents.get_by_id(l3.doc_id)))
    # Production fail() additionally emits a Graph event; persistence of that event is external here.
    monkeypatch.setattr(sa,'fail',lambda a,cause:sa.move(a,sa.FAILED,fail_cause=cause))
    for _ in range(3): ar.drive_review_assignment(FLOW,a,ar.HELD,[])
    assert len(p.assignments)==1 and len(p.waits)==1
    for _ in range(reasons.SYSTEM_ERROR_RETRY_LIMIT+3):
        a=p.rows.latest(p.attempt,p.review_stage)
        ar.drive_review_assignment(FLOW,a,ar.HELD,[],retry=True)
    assert len(p.assignments)==reasons.SYSTEM_ERROR_RETRY_LIMIT
    assert len(p.waits)==1 and p.reviews==p.approvals==p.results==[]


@pytest.mark.parametrize('instruction',[False,True])
def test_NO_REVIEW_existing_claim_approves_only_once(l3,monkeypatch,instruction):
    p=Probe(l3,monkeypatch,{'instruction':instruction,'actor':'no_review'})
    a=p.new_assignment(FLOW,p.run,p.node,p.attempt,p.review_stage,1,author='person',basis=sa.mark(documents.get_by_id(l3.doc_id)))
    for _ in range(3): ar.drive_review_assignment(FLOW,a,ar.HELD,[])
    assert len(p.approvals)==1
    assert len(p.results)==(1 if instruction else 0)


@pytest.mark.parametrize('via',['A-CLI','A-API','A-CLI-ai','A-API-ai'])
@pytest.mark.parametrize('legacy_path',[False,True])
@pytest.mark.parametrize('actor,expected_reviews,expected_approvals',[('ai',1,0),('no_review',0,1)])
def test_SP9b_rejected_resubmission_has_real_T2_transition(l3,monkeypatch,via,legacy_path,actor,expected_reviews,expected_approvals):
    documents.update(l3.doc_id,{'doc_review_status':'rejected','rejection_history':json.dumps([
        {'rejection_id':'l3verify-rejection','reason':'fix','review_id':7}])})
    p=Probe(l3,monkeypatch,{'legacy':legacy_path,'actor':actor,'review_count':1 if actor=='ai' else 0})
    points={}
    original=boundary.enter
    def saved(*a,**kw):
        result=original(*a,**kw)
        points['T1']=documents.get_by_id(l3.doc_id)['doc_review_status']
        return result
    monkeypatch.setattr(boundary,'enter',saved)
    def contact(flow,doc,revision,token):
        points['T2']=documents.get_by_id(doc)['doc_review_status']
        assert points['T2']=='revised'
        binder.on_document_event(binder.EDITED,flow,{'doc_id':doc,'revision_no':revision,'token_id':token})
        points['T3']=documents.get_by_id(doc)['doc_review_status']
    monkeypatch.setattr(contacts.v01_contact,'after_inbox_edited',contact)
    result=l3.request(via,body=l3.body(edit_reason='rejected',rejection_response='l3verify addressed',
        rejection_id='l3verify-rejection',rejection_review_id=7))
    assert result[0]==200,result
    assert points=={'T1':'rejected','T2':'revised','T3':'approved' if expected_approvals else 'revised'}
    assert len(p.reviews)==expected_reviews and len(p.approvals)==expected_approvals
    emit(l3,'SP9b',{'path':via,'legacy':legacy_path,'actor':actor,'times':points,'counts':p.counts()})
