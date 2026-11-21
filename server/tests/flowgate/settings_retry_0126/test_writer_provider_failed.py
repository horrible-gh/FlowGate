"""Real settings routes, SQLite assignments/ledger, AI admission recorder.
No external model, deployed server, or existing user data is used.
"""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    A, Y, X, B, FA, _sql, assignments, command, db_path, decided, env, events,
    flow, ledger, node_run, one, roles, starts, stored, wired,
)
from test_fg_command_review_0125 import (  # noqa: F401
    _spent, _pass, _loops, _reviews, _doc, _read, _run, completions, fork_join,
)
from test_fg_command_rerequest_0124 import _reason
from test_fg_settings_capability_0126 import save
from test_fg_card_settings import _h, get_settings

pytestmark = pytest.mark.usefixtures('fork_join', 'completions')


from settings_retry_support_0126 import (history, new_settings, _submitted_ai_instruction, _instruction_reviews, _event_seqs)

@pytest.mark.parametrize('mode', ['step'])
@pytest.mark.parametrize('failure', ['provider_failed'])
@pytest.mark.parametrize('changed',[False,True])
def test_failed_writer_explicit_command_gets_new_attempt_and_current_provider(
        env,decided,monkeypatch,starts,mode,failure,changed,tmp_path):
    from test_fg_provider_failure_retry_0121 import _finalized
    key=flow(env,extra_settings=(('instruction','omit'),('review_count',3)))
    roles(key,execution=X,review=B)
    command(env,mode)
    [job]=env['jobs']
    first=one(env,key,'result')
    ending={'stop_code':failure}
    if failure=='timeout': ending['end_reason']='timeout'
    if failure=='no_result': ending.update(stop_code='no_output_exhausted',exit_code=0)
    _finalized(env,'air_0126_writer_end',job['graph_exec_id'],**ending)
    _read(env)
    frozen=history(env)
    old,new=new_settings(env,key) if changed else (get_settings(env,FA,key),get_settings(env,FA,key))
    expected_provider=A if changed else X
    before_calls=len(starts)
    command(env,mode)
    writers=[r for r in assignments(env,key) if r['stage']=='result']
    # 0121 keeps its contract while the write role still resolves to the recorded assignment: a provider failure or a
    # clean end with no result is written again on the SAME attempt by the same AI; every other end, and any end whose
    # role changed, resolves a new assignment in a new attempt (the earlier one stays as history)
    same_attempt=(not changed) and failure in ('provider_failed','no_result')
    assert len(writers)==(1 if same_attempt else 2)
    second=first if same_attempt else next(r for r in writers if r['attempt_id']!=first['attempt_id'])
    assert second['provider_id']==expected_provider and first in writers
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    new_job=next(j for j in env['jobs'] if (j.get('assignment_ref') or {}).get('attempt_id')==second['attempt_id'])
    ai_node_starter.start_ai_invoke(new_job)
    [call]=[c for c in starts[before_calls:] if not c.get('document_review_loop')]
    assert call['provider_id']==expected_provider and call['graph_attempt_id']==second['attempt_id']
    assert all(row in history(env)[table] for table,rows in frozen.items() for row in rows)
    old_attempt=_sql(env['db'],'SELECT * FROM graph_node_attempts WHERE attempt_id=?',(first['attempt_id'],))[0]
    assert old_attempt['status']==('active' if same_attempt else 'invalidated')
    (tmp_path/'writer-evidence.json').write_text(json.dumps({
        'failure':failure,'mode':mode,'old_version':old['settings_version'],'new_version':new['settings_version'],
        'old_assignment':first,'new_assignment':second,'actual_provider_id':call['provider_id']},indent=2),encoding='utf-8')


