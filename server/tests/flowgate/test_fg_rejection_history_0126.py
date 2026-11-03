"""0126 rejection preserves non-empty review and assignment histories."""
import pytest
from test_fg_human_intervention_0126 import (
    FA, _sql, db_path, decided, env, wired, starts, record_starts, roles, stored,
    assignments, b1, command, events, ledger, node_run, one, write_line,
    fork_join, _read, _wire, _spent, _run, _loops, _get, _request, _post, _rows,
    completions, preserve_runtime_environment,
)
pytestmark=pytest.mark.usefixtures('wired','completions')


@pytest.mark.parametrize('slot',['instruction','report'])
def test_b8_nonempty_review_and_assignment_history_survives_recovery(env,decided,monkeypatch,starts,fork_join,slot,tmp_path):
    a,b,c,doc,review=_spent(env,monkeypatch,starts,'review_count_exhausted')
    # The transport fixture ends its loop without an AI verdict submission.
    # Seed a durable historical verdict through its real repository.
    from modules.flow_gate.db import document_reviews, documents
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from test_fg_ai_roles_l2_assignment import B
    with_flow_guard(FA, lambda: document_reviews.insert_review(doc,
        documents.get_by_id(doc)['revision_no'], B, 'hold', '[]', 'fixture history',
        '2026-11-03T00:00:00+00:00', review_run_id='fixture-ended-review', actual_provider_id=B))
    # The old review was result-only. Enable and approve a real instruction
    # without rewriting that historical assignment.
    stored(a, 'instruction', 'use')
    from test_fg_document_decisions_0126 import _body
    _body(_get(env,a)['documents']['instruction']['doc_id'])
    _post(env,a,_request(env,a)); _read(env)
    assert node_run(env,a)['status']=='done'
    prior_assignments=_rows(env,'graph_stage_assignments'); prior_reviews=_rows(env,'document_reviews')
    assert prior_assignments and prior_reviews
    prior_docs=_sql(env['db'],'SELECT doc_id,revision_no,file_path FROM documents ORDER BY doc_id')
    response=_post(env,a,_request(env,a,slot,'reject','reject-with-history'),slot)
    assert _rows(env,'graph_stage_assignments')==prior_assignments
    assert _rows(env,'document_reviews')==prior_reviews
    assert _sql(env['db'],'SELECT doc_id,revision_no,file_path FROM documents ORDER BY doc_id')==prior_docs
    assert node_run(env,a)['status']=='pending'
    recovery=_rows(env,'graph_document_recovery_items')
    assert any(r['item_kind']=='assignment' for r in recovery)
    from pathlib import Path
    import json
    with (tmp_path/'rejection-history-evidence.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'slot':slot,'recovery':response['recovery'],
            'review_ids':[r['id'] for r in prior_reviews],
            'assignments':[{k:r[k] for k in ('attempt_id','stage','seq','stage_state')} for r in prior_assignments]})+'\n')
