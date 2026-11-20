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


from l3_states_support_0127 import Probe


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
