"""Situation matrix and stage regressions for v02test.0049 T0011."""
import pytest

from modules.flow_gate.services import chat_run_hooks, invoke_mention_service, mention_service
from modules.flow_gate.services import prompt_selection as selection
from modules.flow_gate.services.ai_invoke import provider_api
from flowgate.conversation import integration

BASE = 'http://h/flowgate/api/v1'


@pytest.fixture
def hooks(monkeypatch):
    saved = dict(chat_run_hooks._handlers)
    chat_run_hooks.clear()
    integration.register()
    yield
    chat_run_hooks.clear()
    for name, handler in saved.items():
        chat_run_hooks.register(name, handler)


@pytest.mark.parametrize('mode', ['read', 'execute', 'edit'])
@pytest.mark.parametrize('unanswered', [False, True, None])
@pytest.mark.parametrize('current_run', [False, True, None])
def test_cli_and_api_situation_matrix(hooks, mode, unanswered, current_run):
    situation = selection.ChatSituation(mode == 'edit', unanswered, current_run)
    raw = integration.provide_chat_tools(None)
    selected = selection.select_chat_guidance(raw, situation)
    visible = [t for t in selected if t['prompt_visible']]
    names = {t['name'] for t in visible}
    text = '\n'.join(chat_run_hooks.chat_mention_sections(BASE, 'test-token', omit=('run_command',), situation=situation))
    assert 'list_card_questions' in names
    assert ('answer_card_question' in names) == (unanswered is not False)
    assert ('BEFORE you submit' in text) == (unanswered is not False)
    assert ('read_flow_recovery_basis' in names) == (current_run is not False)
    assert ('/recovery-basis' in text) == (current_run is not False)
    for name in selection._CONTROL_WRITE | selection._DOCUMENT_WRITE:
        assert (name in names) == (mode == 'edit')
    for name in selection._RECOVERY_WRITE:
        assert (name in names) == (mode == 'edit' and current_run is not False)
    assert {'get_ai_options', 'get_card_roles', 'read_card_document', 'read_document_by_id'} <= names
    assert any('attachment' in name for name in names)
    assert '/documents/{doc_id}' in text and '/ai-options' in text and '/roles' in text
    if mode != 'edit':
        descriptions = '\n'.join(t['description'] + t.get('prompt_hint', '') for t in visible)
        for forbidden in ['/cards', '/revisions', 'retry_flow_card', 'restart_flow_run',
                          'revise_document_by_id', 'create_new_document', 'place_flow_cards']:
            assert forbidden not in text
            assert forbidden not in descriptions
        assert '/help/items/chat_control' not in text
        assert '/help/items/chat_documents' not in text
    else:
        assert '/help/items/chat_control' in text
        assert '/help/items/chat_documents' in text
    # Presentation does not mutate the descriptors, handlers, schemas or permission gates.
    for original, result in zip(raw, selected):
        assert 'prompt_visible' not in original
        for key in ['handler', 'schema', 'call_handler', 'own_validation']:
            assert result.get(key) is original.get(key)


@pytest.mark.parametrize('q_value', [[], ['p.m.0001.0002-T'], 'error'])
@pytest.mark.parametrize('run_value', [None, {'run_id': 'graph-run'}, 'error'])
def test_lookup_empty_present_and_failure_are_independent(monkeypatch, q_value, run_value):
    from modules.flow_gate.db import documents, questions
    from modules.flow_gate.graph_workflow.store import run_store
    monkeypatch.setattr(documents, 'get_by_id', lambda doc_id: {'type_code': 'T'})
    seen = []
    def lookup(value, group):
        seen.append(group)
        if value == 'error':
            raise RuntimeError('stored-state read failed')
        return value
    monkeypatch.setattr(questions, 'list_open_doc_ids_by_group', lambda group: lookup(q_value, group))
    monkeypatch.setattr(run_store, 'current_run', lambda group: lookup(run_value, group))
    result = selection.chat_situation('bound-flow', can_write=False)
    assert result.unanswered is (None if q_value == 'error' else bool(q_value))
    assert result.current_run is (None if run_value == 'error' else run_value is not None)
    assert not result.can_write
    assert seen == ['bound-flow', 'bound-flow']


def test_api_hook_uses_admitted_capability_and_stored_state(hooks, monkeypatch):
    monkeypatch.setattr(selection, 'chat_situation', lambda group, can_write: selection.ChatSituation(can_write, False, False))
    for access in [None, 'execute', 'edit']:
        tools = chat_run_hooks.chat_tools({'action_scope': 'chat', 'group_id': 'bound', 'chat_command_access': access})
        visible = {t['name'] for t in tools if t['prompt_visible']}
        assert ('revise_card_document' in visible) == (access == 'edit')
        assert 'answer_card_question' not in visible
        assert 'read_flow_recovery_basis' not in visible


def test_specific_help_survives_api_conversion():
    for item in ['chat_control', 'chat_documents', 'chat_questions', 'chat_recovery', 'submit']:
        prompt = 'Before this action read GET ' + BASE + '/help/items/' + item
        converted = provider_api._api_help_prompt(prompt)
        assert 'read_help with item=' + item in converted
        assert 'GET ' not in converted
    converted = provider_api._api_help_prompt('Keep artifacts in scratch. GET ' + BASE + '/help/items/submit')
    assert 'Keep artifacts in scratch.' in converted


@pytest.fixture
def mention_context(monkeypatch):
    from modules.flow_gate.services import q_service
    from modules.flow_gate.workflow import prompt_copy_service
    monkeypatch.setattr(mention_service, '_include_remote_source_crud', lambda project: False)
    monkeypatch.setattr(mention_service.template_provision, 'has_body_template', lambda kind: False)
    monkeypatch.setattr(q_service, 'qa_context_doc_ids', lambda doc: [])
    monkeypatch.setattr(prompt_copy_service, '_append_qa_block', lambda *a, **kw: None)
    return dict(project='p', module='m', group='0001', parent_type='D', parent_doc_number='D0002',
                parent_title='Target', parent_doc_id='p.m.0001.0002-D', parent_canonical_doc_id='p.m.0001.0002-D',
                parent_revision_no=3, parent_content_state='readable', head_type='', head_status='',
                scratch_dir='scratch', raw_token='test-token', api_base_url=BASE, action_scope='edit')


def test_first_write_edit_and_rework_keep_only_stage_instructions(mention_context):
    plain = mention_service.build_mention(**mention_context)
    assert 'Read the target document before editing.' in plain
    assert 'review feedback below' not in plain
    assert 'next_type:' not in plain
    assert '"expected_revision": 3' in plain
    assert '/help/items/submit' in plain
    first = mention_service.build_mention(**mention_context, first_write=True)
    assert 'first write, not a revision' in first
    assert '## Revision instructions' not in first
    rework = mention_service.build_mention(**mention_context, edit_reason='rejected',
        current_review={'revision_no': 3, 'verdict': 'issues', 'findings': [{'locus': 'test', 'note': 'fix me'}]},
        rejection_target={'rejection_id': 'r1', 'reason': 'human reason'})
    for expected in ['review feedback below', 'fix me', 'human reason', 'rejection_response', '"rejection_id": "r1"']:
        assert expected in rework
    assert 'next_type:' not in rework
    assert 'body_sha256' not in rework  # detailed format is in submit help


@pytest.mark.parametrize('reason', [None, '', '   ', 123])
def test_rework_without_renderable_reason_does_not_request_feedback(mention_context, reason):
    text = mention_service.build_mention(**mention_context, edit_reason='rejected',
        current_review=None, rejection_target={'rejection_id': 'r1', 'reason': reason})
    assert '## Human rejection (latest)' not in text
    assert 'review feedback below' not in text
    assert 'Read the target document before editing.' in text


def test_preflight_has_q_task_without_artifact_submission(mention_context):
    params = dict(mention_context, action_scope='new', head_type='D', continuous=True, continuous_review_mode=True)
    text = mention_service.build_mention(**params)
    assert 'pre-flight Q registration' in text
    assert '## Artifact registration' not in text
    scratch = text.split('## Scratch directory')[1].split('## Reminder')[0]
    assert 'doc_path' not in scratch and '/help/items/submit' not in scratch
    assert '/q/p.m.0001.0002-D/questions' in text


def test_read_chat_length_before_after(hooks, monkeypatch):
    monkeypatch.setattr(invoke_mention_service.chat_settings_service, 'resolve_chat_settings_safe', lambda user: {})
    monkeypatch.setattr(invoke_mention_service.chat_doc_settings_service, 'context_window',
                        lambda *a, **kw: {'after_seq': 0, 'folded': 0})
    monkeypatch.setattr(mention_service, '_include_remote_source_crud', lambda project: True)
    from modules.flow_gate.services import question_policy
    monkeypatch.setattr(question_policy, 'text', lambda user: '')
    monkeypatch.setattr(selection, 'chat_situation', lambda *a, **kw: selection.ChatSituation(False, False, False))
    params = dict(doc_id='p.m.0001.0003-CH', project='p', module='m', group_name='p.m.0001',
                  raw_token='test-token', token_id='test-id', api_base_url=BASE,
                  source_access='read', command_execution=True)
    after = invoke_mention_service.build_conversation_mention(**params)
    def legacy_sections(base, token, omit=(), **kw):
        return [t['cli_mention'](base, token) for t in integration.provide_chat_tools(None)
                if t['name'] not in omit and callable(t.get('cli_mention'))]
    monkeypatch.setattr(chat_run_hooks, 'chat_mention_sections', legacy_sections)
    before = invoke_mention_service.build_conversation_mention(**params)
    print(f'READ_CHAT_CHARS before={len(before)} after={len(after)}')
    assert len(after) < len(before)
    assert '/cards' in before and '/cards' not in after
    assert '/revisions' in before and '/revisions' not in after


def test_conversation_questions_do_not_trigger_card_answer_guidance(monkeypatch):
    from modules.flow_gate.db import documents, questions
    from modules.flow_gate.graph_workflow.store import run_store
    monkeypatch.setattr(questions, 'list_open_doc_ids_by_group', lambda group: ['conversation'])
    monkeypatch.setattr(documents, 'get_by_id', lambda doc: {'type_code': 'CH'})
    monkeypatch.setattr(run_store, 'current_run', lambda group: None)
    assert selection.chat_situation('bound').unanswered is False


def test_review_retains_verdict_without_authoring_instructions(mention_context):
    text = mention_service.build_review_mention(
        token_rec={'project': 'p', 'group_id': 'p.m.0001', 'scratch_dir': 'scratch'},
        target_doc={'doc_id': 'p.m.0001.0002-TR', 'type_code': 'TR', 'seq': 2, 'title': 'Result'},
        api_base_url=BASE, raw_token='test-token')
    assert '## Review instructions' in text and 'verdict values:' in text
    assert '/help/items/submit' in text
    for excluded in ['## Work scope', '## N/T', '## TS', 'next_type:', '## Artifact registration']:
        assert excluded not in text


@pytest.mark.parametrize('head_type', ['TR', 'TS', 'N', 'T', 'D'])
def test_preflight_never_advertises_authoring_or_source_mutation(mention_context, monkeypatch, head_type):
    monkeypatch.setattr(mention_service, '_include_remote_source_crud', lambda project: True)
    params = dict(mention_context, action_scope='new', head_type=head_type, continuous=True, continuous_review_mode=True)
    text = mention_service.build_mention(**params)
    for excluded in ['write, patch', '## Work scope', '## TR test responsibility', '## Artifact registration',
                     '## Document template', '## N/T authoring', '## TS authoring']:
        assert excluded not in text
