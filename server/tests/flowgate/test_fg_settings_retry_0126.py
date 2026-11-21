"""A3/A4: real settings routes, SQLite assignments/ledger, AI admission recorder.
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

def test_same_assignment_automatic_retry_keeps_its_provider_after_settings_change(
        env,decided,monkeypatch,starts):
    from flowgate.card_settings import stage_assignment as sa
    key,_,_,_ = _spent(env,monkeypatch,starts,'step',follow=False)
    writer=one(env,key,'result')
    new_settings(env,key)
    target=sa.retry_target(writer['run_id'],key,writer['attempt_id'],'result')
    assert target['provider_id']==X
    assert target['assignment_ref']=={'attempt_id':writer['attempt_id'],'stage':'result','seq':0}
