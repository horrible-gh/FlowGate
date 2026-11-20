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
