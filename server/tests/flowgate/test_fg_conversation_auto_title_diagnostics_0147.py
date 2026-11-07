"""Actual endpoint/error envelope and CAS; deterministic model/store fixture, not live AI evidence."""
import json
import logging
import pytest
from test_fg_conversation_auto_title_0047 import env, _post, _state_of, BODY, DOC
from flowgate.conversation import legacy_adapter

@pytest.mark.parametrize('reason', ['timeout', 'transport_failed', 'provider_launch_failed', 'execution_failed', 'remote_cli_unsupported', 'future_reason'])
def test_failed_reasons_are_terminal_and_safe(env, caplog, reason):
    env['answer'] = (None, reason)
    before = json.dumps(env['turns'], sort_keys=True)
    with caplog.at_level(logging.WARNING):
        response = _post(BODY)
    assert response.status_code == 502
    assert response.json()['error']['message'] == reason
    assert _state_of(env['doc']) == 'failed'
    assert _post(BODY).status_code == 409
    assert len(env['prompts']) == 1
    assert json.dumps(env['turns'], sort_keys=True) == before
    assert 'stage=model_result' in caplog.text
    assert '로그인 오류 원인을 찾아 줘' not in caplog.text
    assert 'future_reason' not in caplog.text

@pytest.mark.parametrize('reason', ['provider_unavailable', 'provider_not_found', 'provider_disabled'])
def test_unexecuted_provider_can_recover_once_with_cas(env, caplog, reason):
    env['answer'] = (None, reason)
    with caplog.at_level(logging.WARNING):
        first = _post(BODY)
    assert first.status_code == 409
    assert _state_of(env['doc']) == 'owed'
    assert 'stage=provider_unavailable' in caplog.text
    env['answer'] = ('isolated recovered title', None)
    assert _post(BODY).status_code == 200
    assert _state_of(env['doc']) == 'done'
    assert _post(BODY).status_code == 409
    assert len(env['prompts']) == 2 # boundary invocations, not actual model executions

@pytest.mark.parametrize('answer', ['', '   ', '""', 'title: ...'])
def test_empty_or_unclean_answer_is_failed(env, caplog, answer):
    env['answer'] = (answer, None)
    with caplog.at_level(logging.WARNING):
        response = _post(BODY)
    assert response.status_code == 502
    assert response.json()['error']['message'] == 'empty_response'
    assert _state_of(env['doc']) == 'failed'
    assert 'stage=empty_title' in caplog.text
    assert _post(BODY).status_code == 409
    assert len(env['prompts']) == 1

def test_unexpected_exception_retains_claimed_without_logging_secrets(env, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError('secret=do-not-record')
    monkeypatch.setattr(legacy_adapter, 'complete_prompt', fail)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        _post(BODY)
    assert _state_of(env['doc']) == 'claimed'
    assert 'stage=exception' in caplog.text
    assert 'exception_type=RuntimeError' in caplog.text
    assert 'do-not-record' not in caplog.text
    assert _post(BODY).status_code == 409
