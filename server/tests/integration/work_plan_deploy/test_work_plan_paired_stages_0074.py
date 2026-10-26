"""T0017 latest rejection: card-owned stored stages -> deploy -> durable Graph runs/reviews.
External AI processes are recorders; DB, routes, token issuing, stage handoff and approvals are real.
"""
from copy import deepcopy
import pytest
from test_work_plan_instruction_mode_0074 import (  # noqa: F401
    api, env, db_path, card_approval_port, card_start_port, decided, _put, _stop_run,
    _read, _save, _preview, _execute, _confirm_and_run_after_the_root, _flow, _write_plan,
    _line_doc, _file, _one_job, _row, _sql, _h, FA,
)
import _flowgate_test_imports  # noqa: F401
from test_fg_instruction_stage_0056 import _record_start, _register
from test_fg_card_settings_review import (
    _write_written, _run_ended, _review_status, _recorded_start_run, _result,
    INSTRUCTION_RUN, WORK_RUN, REVIEW_RUN,
)

@pytest.fixture(autouse=True)
def configured_api_base(monkeypatch):
    # The transport is not called; the real token/mention path needs its configured base URL.
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    monkeypatch.setattr(ai_node_starter, "api_base_url", lambda: "http://127.0.0.1:8089/api/v1")

def _row_run_id(env, key):
    return _sql(env['db'], 'SELECT run_id FROM graph_node_runs WHERE node_key = ?', (key,))[0]['run_id']


TYPES = ['D', 'P', 'L', 'DB', 'N', 'T']


def paired(env, typ, *, prewritten=False, review=0, mode='use'):
    from modules.flow_gate.services.work_plan_card_stages import upgrade
    _, doc, keys = _flow(env)
    _write_plan(doc, {typ: 1})
    view = _read(env, doc)
    body = upgrade(view['body'])
    instruction, result = body['steps']
    instruction.update(provider_id='aip_card', provider_display_name='aip_card',
                       review_count=review, reviewer_provider_id='aip_review' if review else None,
                       reviewer_provider_display_name='aip_review' if review else None,
                       note='instruction-only-note', instruction_mode=mode,
                       pre_instruction_text='Written instruction' if prewritten else None)
    result.update(provider_id='aip_assignee', provider_display_name='aip_assignee',
                  review_count=0, note='result-only-note')
    if typ in ['D', 'P', 'L', 'DB']:
        result['pre_instruction_text'] = 'Independent design editing text'
    response = _put(env, doc, body, view['revision_no'])
    assert response.status_code == 200, response.text
    saved = response.json()
    assert _read(env, doc)['body'] == saved['body']
    preview = _preview(env, doc, 'first', saved['revision_no'])
    assert not preview['blockers'], preview
    assert not preview['cards'][0]['settings_skipped'], preview
    [key] = _execute(env, doc, 'first', preview['baseline'])['created_node_keys']
    return doc, keys, key, saved


@pytest.mark.parametrize('typ', TYPES)
@pytest.mark.parametrize('prewritten', [False, True])
@pytest.mark.parametrize('review_passed', [False, True])
def test_independent_stages_save_deploy_review_and_result_run(api, decided, monkeypatch, typ, prewritten, review_passed):
    from flowgate.card_settings import repository
    doc, keys, key, saved = paired(api, typ, prewritten=prewritten, review=2)
    instruction, result = saved['body']['steps']
    assert instruction['card_id'] == result['card_id']
    assert [instruction['pair_role'], result['pair_role']] == ['instruction', 'result']
    assert instruction['key'] != result['key']
    stored = repository.values(FA, key)
    assert (stored['instruction_ai'], stored['run_ai'], stored['instruction_review_count'], stored.get('review_count', 0)) == (
        'aip_card', 'aip_assignee', 2, 0)
    instruction_doc = _line_doc(api, key)
    calls = _recorded_start_run(monkeypatch) if prewritten else None
    _confirm_and_run_after_the_root(api, keys['tmp_r'], key)
    if not prewritten:
        job = _one_job(api)
        assert (job['stage'], job['provider_id'], job['target_doc_id']) == ('instruction', 'aip_card', instruction_doc)
        _, _, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
        assert 'instruction-only-note' in issue['mention']
        assert 'result-only-note' not in issue['mention']
        _write_written(instruction_doc)
        calls = _recorded_start_run(monkeypatch)
        _run_ended(INSTRUCTION_RUN, action_scope='edit', doc_ref=instruction_doc)
    assert api['jobs'] == []
    assert _row(api, key)['stage'] == 'instruction'
    assert _review_status(instruction_doc) != 'approved'
    [review] = calls
    assert review['doc_ref'] == instruction_doc
    assert review['document_review_loop']['review_count'] == 2
    assert review['document_review_loop']['reviewer_provider_id'] == 'aip_review'
    assert review['document_review_loop']['rework_provider_id'] == 'aip_card'
    assert review['graph_exec_id']
    assert _sql(api['db'], 'SELECT status FROM graph_runs')[-1]['status'] == 'running', _sql(api['db'], 'SELECT status, idle_reason FROM graph_runs')
    # Model the recorder worker completing its durable lease before invoking the real run-end hook.
    from flowgate.graph_run import execution_store as ledger
    lease = ledger.acquire(review['graph_exec_id'], REVIEW_RUN)
    ledger.begin_handoff(review['graph_exec_id'], _row_run_id(api, key))
    ledger.end(review['graph_exec_id'], lease)
    # Each durable execution ends once. Rejection must not advance the result.
    _run_ended(REVIEW_RUN, action_scope='review', doc_ref=instruction_doc,
               graph_exec_id=review['graph_exec_id'],
               document_review_loop={'stop_reason': 'review_passed' if review_passed else 'review_count_exhausted'})
    ledger.end_handoff(review['graph_exec_id'])
    if not review_passed:
        assert api['jobs'] == []
        assert _row(api, key)['stage'] == 'instruction'
        assert _review_status(instruction_doc) != 'approved'
        return
    result_job = _one_job(api)
    assert (result_job['stage'], result_job['provider_id'], result_job['ref_doc_ids']) == (
        'result', 'aip_assignee', [instruction_doc])
    expected = {'N': 'NR', 'T': 'TR'}.get(typ, typ)
    assert result_job['result_doc_type'] == expected
    assert _review_status(instruction_doc) == 'approved'
    _, _, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    assert 'result-only-note' in issue['mention']
    assert 'instruction-only-note' not in issue['mention']
    if typ in ['D', 'P', 'L', 'DB']:
        assert 'Independent design editing text' in issue['mention']
    result_doc = _result(81, expected)
    _register(api, result_doc, expected, issue['token_id'])
    _run_ended(WORK_RUN)
    assert _row(api, key)['status'] == 'done'
    assert _review_status(result_doc) == 'approved'


@pytest.mark.parametrize('typ', TYPES)
@pytest.mark.parametrize('missing_mode', ['absent', 'null'])
def test_omit_reopen_then_missing_mode_redeploy_keeps_documents_and_restores_instruction(api, decided, typ, missing_mode):
    from flowgate.card_settings import repository
    from modules.flow_gate.services import work_plan_service as wp
    doc, keys, key, saved = paired(api, typ, prewritten=True, mode='omit')
    ids = [_line_doc(api, key), _line_doc(api, key, 'report')]
    before = [_file(d).read_bytes() for d in ids]
    assert wp.totals(saved['body'])['steps'] == 1
    body = deepcopy(saved['body'])
    if missing_mode == 'null':
        body['steps'][0]['instruction_mode'] = None
    else:
        body['steps'][0].pop('instruction_mode')
    response = _put(api, doc, body, saved['revision_no'])
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['body']['steps'][0]['instruction_mode'] == 'use'
    assert wp.totals(_read(api, doc)['body'])['steps'] == 2
    preview = _preview(api, doc, 'add', saved['revision_no'])
    _execute(api, doc, 'add', preview['baseline'])
    assert repository.values(FA, key)['instruction'] == 'use'
    assert _line_doc(api, key) == ids[0]
    assert _file(ids[1]).read_bytes() == before[1]
    _confirm_and_run_after_the_root(api, keys['tmp_r'], key)
    assert _one_job(api)['stage'] == 'result'  # prewritten instruction approved, then result
    assert _review_status(ids[0]) == 'approved'
    _stop_run(api)
    changed = deepcopy(saved['body'])
    changed['steps'][0]['instruction_mode'] = 'omit'
    refused = _put(api, doc, changed, saved['revision_no'])
    assert refused.status_code == 422, refused.text
    assert _read(api, doc)['body'] == saved['body']


@pytest.mark.parametrize('typ', TYPES)
def test_omit_executes_result_only_with_no_instruction_review(api, decided, monkeypatch, typ):
    from modules.flow_gate.services import work_plan_service as wp
    doc, keys, key, saved = paired(api, typ, review=2, mode='omit')
    ids = [_line_doc(api, key), _line_doc(api, key, 'report')]
    before = [_file(d).read_bytes() for d in ids]
    reviews = _recorded_start_run(monkeypatch)
    _confirm_and_run_after_the_root(api, keys['tmp_r'], key)
    job = _one_job(api)
    assert job['stage'] == 'result'
    assert job['result_doc_type'] == {'N':'NR', 'T':'TR'}.get(typ,typ)
    assert ids[0] not in (job['ref_doc_ids'] or [])
    assert reviews == []
    assert [_file(d).read_bytes() for d in ids] == before
    assert wp.totals(_read(api, doc)['body'])['steps'] == 1


def test_legacy_conversion_is_idempotent_preserves_ids_and_does_not_alias_stage_edits(api):
    from modules.flow_gate.services import work_plan_card_stages as stages
    from modules.flow_gate.services import work_plan_service as wp
    _, doc, _ = _flow(api)
    _write_plan(doc, {'D': 2, 'P': 1, 'DS': 1})
    original = _read(api, doc)['body']
    original['steps'][1]['pre_instruction_text'] = 'legacy shared text'
    paired_body = stages.upgrade(original)
    assert stages.upgrade(paired_body) == paired_body
    checked = wp.validate(paired_body, enforce_provider_scope=False)
    assert checked == paired_body
    assert checked['steps'][0] == original['steps'][0]  # hidden legacy DS retained, not linked by ordinal
    for old in original['steps'][1:]:
        pair = [s for s in checked['steps'] if s['card_id'] == old['card_id']]
        assert len(pair) == 2
        assert pair[1]['key'] == old['key']
    checked['steps'][1]['pre_instruction_text'] = 'instruction only'
    assert checked['steps'][2]['pre_instruction_text'] == 'legacy shared text'
