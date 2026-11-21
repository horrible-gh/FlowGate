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
def test_command_recovers_lost_loop_end_once_and_fences_old_callback(
        env,decided,monkeypatch,starts,mode):
    from flowgate.card_settings import assigned_review
    from test_fg_card_settings_review import REVIEW_RUN
    key,_,result,_=_spent(env,monkeypatch,starts,mode,follow=False)
    first=one(env,key,'result_review',1)
    # The durable loop stopped; its callback was lost before the assignment verdict committed.
    _sql(env['db'],"UPDATE graph_stage_assignments SET stage_state='active', verdict=NULL "
                   "WHERE attempt_id=? AND stage='result_review' AND seq=1",(first['attempt_id'],))
    new_settings(env,key)
    count=len(_loops(starts))
    command(env,mode)
    second=one(env,key,'result_review',2)
    assert second and (second['provider_id'],second['rework_provider_id'],second['review_rounds'])==(Y,A,1)
    assert len(_loops(starts))==count+1
    if mode=='step': assert json.loads(_run(env)['step_node_keys'])==[key]
    frozen=history(env)
    waits=_sql(env['db'],"SELECT * FROM graph_run_events WHERE cause='auto_approval_skipped'")
    assigned_review.after_loop_end(FA,{'run_id':REVIEW_RUN},(first['attempt_id'],'result_review',1),False)
    assert history(env)==frozen
    assert _sql(env['db'],"SELECT * FROM graph_run_events WHERE cause='auto_approval_skipped'")==waits
    assert len(_loops(starts))==count+1


