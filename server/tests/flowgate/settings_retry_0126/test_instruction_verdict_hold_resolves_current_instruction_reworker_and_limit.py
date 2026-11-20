"""0126 T#5 A3/A4: real settings routes, SQLite assignments/ledger, AI admission recorder.
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
def test_instruction_verdict_hold_resolves_current_instruction_reworker_and_limit(
        env,decided,monkeypatch,starts,mode):
    from test_fg_ai_roles_l2_assignment import record_starts
    key=flow(env,extra_settings=(('instruction_review_count',3),))
    roles(key,instruction=X,execution=A,review=B)
    command(env,mode)
    instruction=_submitted_ai_instruction(env,monkeypatch,provider=X)
    record_starts(monkeypatch,starts)
    first=one(env,key,'instruction_review',1)
    assert first['review_rounds']==3 and first['rework_provider_id']==X
    from test_fg_card_intervention_assigned_0119 import _loop_row,_loop_end
    from test_fg_card_settings_review import REVIEW_RUN
    _loop_row(instruction,REVIEW_RUN,'review')
    _loop_end(env,first['intent_exec_id'],instruction,REVIEW_RUN,'review_verdict_hold')
    _read(env)
    frozen=dict(one(env,key,'instruction_review',1))
    for field,value in [('instruction_role',{'kind':'assigned','provider_id':Y}),
                        ('instruction_review_ai',A),('instruction_review_count',1)]:
        save(env,key,field,value)
    calls=len(_loops(starts))
    command(env,mode)
    second=one(env,key,'instruction_review',2)
    assert second and (second['provider_id'],second['rework_provider_id'],second['review_rounds'])==(A,Y,1)
    assert second['attempt_id']==first['attempt_id']
    assert one(env,key,'instruction_review',1)==frozen
    [call]=_loops(starts)[calls:]
    assert call['doc_ref']==instruction
    assert (call['document_review_loop']['reviewer_provider_id'],call['document_review_loop']['rework_provider_id'])==(A,Y)
    assert node_run(env,key)['stage']=='instruction' and one(env,key,'result') is None


