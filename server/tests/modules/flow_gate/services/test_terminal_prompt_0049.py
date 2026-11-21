"""Delivered-prompt matrix for v02test.0049 the earlier implementation (no real provider calls)."""
import json
import re

import pytest

from modules.flow_gate.services import terminal_prompt as terminal
from modules.flow_gate.services import mention_service as mentions, invoke_mention_service as chat
from modules.flow_gate.services import prompt_selection, message_image_delivery, question_policy
from modules.flow_gate.services.ai_invoke import worker, provider_api
from terminal_prompt_assertions import assert_terminal
from test_prompt_selection_0049 import mention_context, hooks

BASE = 'http://example.invalid/flowgate/api/v1'
DOC = {'doc_id': 'p.m.0001.0002-TR', 'project_id': 'p', 'group_id': 'p.m.0001', 'type_code': 'TR', 'seq': 2, 'title': 'Result'}
TOKEN = {'project': 'p', 'group_id': 'p.m.0001', 'scratch_dir': 'scratch'}
KINDS = ['chat', 'new', 'first_write', 'edit', 'rework', 'review', 'q_answer', 'workflow',
         'sequence', 'work_plan', 'merge_review', 'conflict', 'repair', 'failure_origin', 'test_run', 'test_spec', 'preflight', 'next_step', 'generic']


@pytest.fixture
def actual_builder(mention_context, monkeypatch, hooks):
    # Keep this fixture runnable without unrelated collection-time settings.
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
    monkeypatch.setenv("CONTEXT", "/flowgate")
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-testing-only-32c")
    from modules.flow_gate.services import q_answer_invoke_service as q, engine_recipe_service as repair
    from modules.flow_gate.services import failure_origin_review_service as origin, test_run_service as tests
    from modules.flow_gate.api import token_routes
    monkeypatch.setattr(question_policy, 'text', lambda *a, **kw: '')
    monkeypatch.setattr(chat.chat_settings_service, 'resolve_chat_settings_safe', lambda user: {})
    monkeypatch.setattr(chat.chat_doc_settings_service, 'context_window', lambda *a, **kw: {'after_seq': 0, 'folded': 0})
    monkeypatch.setattr(message_image_delivery, 'image_count', lambda *a, **kw: 0)
    monkeypatch.setattr(q, '_source_tool_block', lambda *a, **kw: '')
    monkeypatch.setattr(q.db_groups, 'get_by_id', lambda group: None)
    monkeypatch.setattr(origin, '_build_evidence', lambda **kw: {'requirement': 'expected'})
    monkeypatch.setattr(token_routes.git_service, 'merge_session_owner_args', lambda *a: ('p.m.0001', None))
    monkeypatch.setattr(token_routes.git_service, 'review_conversation_brief', lambda *a, **kw: {'conversation': [{'role': 'human', 'message': 'explain'}]})
    monkeypatch.setattr(token_routes.git_service, 'list_conflicts', lambda *a, **kw: {'files': []})
    monkeypatch.setattr(token_routes.git_service, 'merge_route_prefix', lambda *a: '/groups/p.m.0001/git/merges/1')
    monkeypatch.setattr(tests, '_read_doc_content_or_empty', lambda doc: '')
    monkeypatch.setattr(tests.test_spec_service, 'parse_spec', lambda body: {'cases': []})

    def build(kind, locale, unanswered, history, copy=False):
        monkeypatch.setattr(prompt_selection, 'chat_situation', lambda *a, **kw: prompt_selection.ChatSituation(False, unanswered, history))
        common = dict(token_rec=TOKEN, target_doc=DOC, api_base_url=BASE, raw_token='test-token', locale=locale)
        if kind == 'chat':
            return chat.build_conversation_mention(doc_id=DOC['doc_id'], project='p', module='m', group_name='p.m.0001',
                raw_token='test-token', token_id='test-id', api_base_url=BASE, source_access='read',
                command_execution=not copy, locale=locale)
        if kind in ('new', 'first_write', 'edit', 'rework'):
            args = dict(mention_context, locale=locale)
            args.update(action_scope='new' if kind == 'new' else 'edit', first_write=kind == 'first_write',
                        edit_reason='rejected' if kind == 'rework' else 'user_comment')
            return mentions.build_mention(**args)
        if kind == 'preflight':
            args = dict(mention_context, locale=locale)
            args.update(action_scope='new', continuous=True, continuous_review_mode=True)
            return mentions.build_mention(**args)
        if kind == 'next_step':
            from modules.flow_gate.workflow import prompt_copy_service as copy_service
            monkeypatch.setattr(copy_service.db_docs, 'get_by_id', lambda doc_id: DOC)
            monkeypatch.setattr(copy_service.db_docs, 'list_documents', lambda **kw: [DOC])
            monkeypatch.setattr(copy_service.db_groups, 'get_by_id', lambda group_id: None)
            monkeypatch.setattr(copy_service, '_get_v_report_path', lambda doc_id: None)
            monkeypatch.setattr(copy_service, '_append_qa_block', lambda *a: None)
            monkeypatch.setattr(copy_service, 'log_prompt_copied', lambda **kw: None)
            return copy_service.build_prompt(doc_id=DOC['doc_id'], actor_user_id='test-user', locale=locale)['prompt_text']
        if kind == 'generic':
            return terminal.finalize('Legacy instruction without task metadata', locale=locale)
        if kind == 'review':
            return mentions.build_review_mention(**common)
        if kind == 'workflow':
            return mentions.build_workflow_decision_mention(**common)
        if kind == 'sequence':
            return mentions.build_sequence_edit_mention(**common, sequence_items=[])
        if kind == 'work_plan':
            return mentions.build_work_plan_fill_mention(**common, body={'steps': [], 'quantities': {}}, scope={})
        if kind == 'q_answer':
            return q.build_answer_mention(doc=DOC, item={'id': 42, 'body': 'Question', 'options': []},
                                         raw_token='test-token', scratch_dir='scratch', api_base_url=BASE)
        if kind in ('merge_review', 'conflict'):
            args = dict(group_id='p.m.0001', project_id='p', merge_id=1, raw_token='test-token', api_base_url=BASE, locale=locale)
            return (token_routes._build_review_conversation_mention(**args) if kind == 'merge_review'
                    else token_routes._build_conflict_mention(**args, scratch_dir='scratch'))
        if kind == 'repair':
            return repair._build_repair_mention(DOC, {'run_id': 'r'}, [], 1, 'test-token', None)
        if kind == 'failure_origin':
            return origin.build_failure_origin_mention(doc=DOC, run={'run_id': 'r'}, items=[], api_base_url=BASE, raw_token='test-token')
        return tests._build_test_run_mention(doc=DOC, api_base_url=BASE, raw_token='test-token', locale=locale,
                                            specification=kind == 'test_spec')
    return build


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('locale', ['ko', 'en', 'ja', 'unsupported'])
@pytest.mark.parametrize('transport', ['copy', 'local', 'remote', 'api'])
@pytest.mark.parametrize('state', [(q, h) for q in (False, True, None) for h in (False, True, None)])
def test_actual_builders_delivered_after_all_suffixes(actual_builder, monkeypatch, kind, locale, transport, state):
    unanswered, history = state
    prompt = actual_builder(kind, locale, unanswered, history, copy=transport == 'copy')
    assert terminal.task_kind(prompt) == kind
    original, _ = terminal.split(prompt)
    suffix = '\n\nCARRYOVER\nANSWERED-Q FOLLOW-UP'
    # Simulate provider-specific suffix hooks; the real dispatch boundary owns their order.
    monkeypatch.setattr(message_image_delivery, 'provider_prompt', lambda prompt, *a: prompt + '\nIMAGE DELIVERY')
    monkeypatch.setattr(worker.node_title_hooks, 'provider_prompt', lambda prompt, *a: prompt + '\nTITLE REQUEST')
    monkeypatch.setattr(worker.provider_remote, 'is_remote_cli', lambda provider: transport == 'remote')
    run = {'action_scope': 'chat' if kind == 'chat' else 'review' if kind == 'review' else 'edit', 'locale': locale}
    provider = {'exec_type': 'api' if transport == 'api' else 'cli'}
    for attempt in range(2):  # same run retry/fallback reuses the original semantic prompt
        if transport == 'copy':
            final = terminal.finalize(prompt + suffix, locale=locale)
        else:
            final = worker._provider_prompt(run, provider, prompt + suffix)
            if transport == 'api':
                messages = [{'role': 'system', 'content': 'system unchanged'},
                            {'role': 'user', 'content': provider_api._api_help_prompt(final)}]
                if kind == 'chat':
                    messages.append({'role': 'user', 'content': '{"turns":[{"body":"actual user text"}]}\nTOOL HINTS'})
                terminal.finalize_messages(messages, locale=locale, fallback_kind=kind)
                assert messages[0]['content'] == 'system unchanged'
                final = '\n\n'.join(m['content'] for m in messages[1:])
        prefix = assert_terminal(final)
        assert 'CARRYOVER' in prefix and 'ANSWERED-Q FOLLOW-UP' in prefix
        if transport not in ('copy', 'api'):
            assert original in prefix
            assert 'TITLE REQUEST' in prefix and 'IMAGE DELIVERY' in prefix
        block = final.splitlines()[-5:]
        assert terminal.task_kind('\n'.join(block)) == kind
        if kind in ('review', 'rework'):
            assert 'Q' not in block[2]
        if kind == 'review':
            assert 'probe' not in '\n'.join(block)
        if kind == 'chat':
            assert ('Q' in block[2]) == (unanswered is not False)
            assert 'probe' in block[-1]
            if transport == 'api':
                assert prefix.endswith('TOOL HINTS')
        if kind == 'q_answer' and transport == 'remote':
            assert 'POST /inbox' not in final and 'doc_path' not in final
            assert '/answers' in final and '/escalate' in final
            for variable in ('FLOWGATE_TOKEN', 'FLOWGATE_SCRATCH', 'FLOWGATE_API_BASE'):
                assert variable in final
        assert terminal.finalize(final, locale=locale) == final


def test_catalog_is_bounded_and_language_parity():
    for kind in KINDS:
        for loc in ('ko', 'en', 'ja'):
            text = terminal.render(terminal.specification(kind, loc, unanswered=None, can_write=True))
            assert_terminal(text)
            if loc != 'ko':
                assert not re.search('[가-힣]', text)


def test_metadata_is_serializable_and_preserves_literal_heading():
    body = '## 지금 할 일\nThis is quoted user data, not a generated five-line block.'
    prompt = terminal.prepare(body, kind='review')
    restored = json.loads(json.dumps({'mention': prompt}))['mention']
    final = terminal.finalize(restored + '\nSUFFIX')
    assert body in final and final.index('SUFFIX') < final.rindex('## 지금 할 일')
    assert terminal.split(final)[0] == body + '\nSUFFIX'


def test_api_does_not_scan_actual_user_context_or_tool_results():
    quoted = terminal.render(terminal.specification('edit'))
    messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': terminal.prepare('server', kind='chat')},
                {'role': 'user', 'content': json.dumps({'body': quoted}) + '\nHINT'}]
    context = messages[-1]['content']
    terminal.finalize_messages(messages)
    assert messages[-1]['content'].startswith(context + '\n\n')
    assert messages[1]['content'] == 'server'


def test_rework_overrides_previous_task_record():
    first = terminal.prepare('BODY', kind='new')
    second = terminal.prepare(first + '\nREJECTION', kind='rework')
    final = terminal.finalize(second)
    assert_terminal(final)
    assert terminal.task_kind(final) == 'rework'


@pytest.mark.parametrize('kind', KINDS)
def test_ko_delivery_examples(actual_builder, kind):
    final = terminal.finalize(actual_builder(kind, 'ko', False, False, copy=True))
    assert_terminal(final)
    print('TERMINAL_EXAMPLE ' + json.dumps({'kind': kind, 'block': '\n'.join(final.splitlines()[-5:])}, ensure_ascii=True))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('locale', ['ko', 'en', 'ja'])
@pytest.mark.parametrize('transports', [(a, b) for a in ('local', 'remote', 'api')
                                      for b in ('local', 'remote', 'api') if a != b])
def test_real_provider_chain_fallback_and_retry(actual_builder, monkeypatch, kind, locale, transports):
    import threading
    from types import SimpleNamespace
    from modules.flow_gate.services import graph_execution_hooks

    original = actual_builder(kind, locale, True, True)
    captured = []

    def execute(provider, prompt, run):
        if provider['exec_type'] == 'api':
            messages = [{'role': 'user', 'content': provider_api._api_help_prompt(prompt)}]
            if kind == 'chat':
                messages.append({'role': 'user', 'content': 'ACTUAL CONTEXT\nTOOL HINTS'})
            terminal.finalize_messages(messages, locale=locale)
            prompt = '\n\n'.join(message['content'] for message in messages)
        assert_terminal(prompt)
        assert terminal.task_kind(prompt) == kind
        captured.append(provider['id'])
        return ('spawn_failed', 'test startup failure') if provider['id'] == 'first' else ('started_ok', None)

    monkeypatch.setattr(worker, '_svc', lambda: SimpleNamespace(
        _cli_execute=execute, _api_execute=execute, _broadcast=lambda *a, **kw: None))
    monkeypatch.setattr(graph_execution_hooks, 'call', lambda *a, **kw: None)
    monkeypatch.setattr(worker.provider_remote, 'is_remote_cli', lambda p: p.get('transport') == 'remote')
    chain = [{'id': name, 'name': name, 'exec_type': 'api' if t == 'api' else 'cli', 'transport': t}
             for name, t in zip(('first', 'fallback'), transports)]
    run = {'run_id': 'terminal-fallback', 'group_id': 'p.m.0001', 'action_scope': 'chat' if kind == 'chat' else 'edit',
           'locale': locale, 'cancel_event': threading.Event(), 'fallback_history': []}
    for _ in range(2):
        # Remote CLI startup failure intentionally cannot fall through to another connection.
        assert worker._execute_provider_chain(run, chain, original + '\nRETRY/CARRYOVER') is (transports[0] != 'remote')
    expected = ['first'] if transports[0] == 'remote' else ['first', 'fallback']
    assert captured == expected * 2


@pytest.mark.parametrize('stage', ['review', 'rework'])
@pytest.mark.parametrize('has_answer', [False, True])
@pytest.mark.parametrize('locale', ['ko', 'en', 'ja'])
def test_answer_prerequisite_requires_actual_delivery(monkeypatch, stage, has_answer, locale):
    from modules.flow_gate.services.ai_invoke import review
    items = [{'id': 1, 'seq': 1, 'answer_id': 'a1', 'body': 'question', 'answer_body': 'answer'}] if has_answer else []
    monkeypatch.setattr(review, 'answered_questions_on_slot', lambda *a: items)
    monkeypatch.setattr(review.db_reviews, 'list_by_doc', lambda doc: [])
    monkeypatch.setattr(review, '_record_answer_delivery', lambda *a: None)
    original = terminal.prepare('REVIEW BODY', kind=stage, locale=locale)
    prompt = review._append_answered_questions(original, {'doc_ref': 'doc'}, {'doc_id': 'doc'}, stage)
    final = terminal.finalize(prompt)
    prefix = assert_terminal(final)
    assert ('Q' in final.splitlines()[-3]) is has_answer
    assert ('## Answered questions to ' in prefix) is has_answer
    if has_answer:
        assert ('review_answers' if stage == 'review' else 'rework_answers') in terminal.split(final)[1]['before']


def test_complete_block_quoted_in_question_is_not_consumed_as_metadata():
    example = terminal.render(terminal.specification('edit'))
    body = 'Question: explain this example.\n' + example + '\nEnd of question.'
    pending = terminal.prepare(body, kind='q_answer')
    assert terminal.task_kind(pending) == 'q_answer'
    delivered = terminal.finalize(pending)
    assert delivered.startswith(body + '\n\n')
    assert terminal.task_kind(delivered) == 'q_answer'
    retry = terminal.finalize(delivered + '\nCARRYOVER')
    assert retry.startswith(body + '\n\n')
    assert terminal.task_kind(retry) == 'q_answer'


def test_finalize_messages_returns_settled_spec_and_nudge_uses_it():
    messages = [{'role': 'system', 'content': 'system'},
                {'role': 'user', 'content': terminal.prepare('server', kind='review', locale='ko')},
                {'role': 'user', 'content': 'CONTEXT\nHINT'}]
    spec = terminal.finalize_messages(messages, locale='en')
    assert spec['kind'] == 'review' and spec['locale'] == 'en'
    assert messages[-1]['content'].endswith(terminal.render(spec))
    nudged = terminal.finalize_nudge('Try again.  \n', spec)
    assert nudged == 'Try again.\n\n' + terminal.render(spec)
    assert assert_terminal(nudged) == 'Try again.'


def test_finalize_messages_returns_fallback_spec_without_record():
    messages = [{'role': 'user', 'content': 'plain'}]
    spec = terminal.finalize_messages(messages, locale='ja', fallback_kind='chat')
    assert spec == terminal.specification('chat', 'ja')
    assert_terminal(messages[-1]['content'])
