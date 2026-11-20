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
def test_explicit_command_repairs_exhausted_approval_retries_without_repeating_ai(
        env,decided,monkeypatch,starts,mode):
    from flowgate.card_settings import legacy_adapter
    from test_fg_command_rerequest_0124 import _refused,LIMIT
    key,follower,result,_=_spent(env,monkeypatch,starts,mode)
    command(env,mode)
    monkeypatch.setattr(legacy_adapter,'completion_gates',_refused)
    _pass(env,key,2,result,'air_0126_approval_failure')
    for _ in range(LIMIT+2): _read(env)
    assert node_run(env,key)['status']=='review'
    assert one(env,key,'result_review',2)['verdict']=='passed'
    before=history(env)
    loops=len(_loops(starts))
    new_settings(env,key)
    monkeypatch.setattr(legacy_adapter,'completion_gates',lambda doc:None)
    command(env,mode)
    assert node_run(env,key)['status']=='done'
    assert node_run(env,follower)['status']==('pending' if mode=='step' else 'open')
    assert len(_loops(starts))==loops
    after=history(env)
    assert all(row in after[table] for table,rows in before.items() for row in rows)
    assert len(_reviews(env,key))==2  # full may append the successor's person assignment


