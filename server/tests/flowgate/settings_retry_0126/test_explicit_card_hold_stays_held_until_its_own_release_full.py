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

@pytest.mark.parametrize('mode', ['full'])
def test_explicit_card_hold_stays_held_until_its_own_release(env,decided,monkeypatch,starts,mode):
    key,_,_,_ = _spent(env,monkeypatch,starts,mode,follow=False)
    new_settings(env,key)
    save(env,key,'hold',True)
    before=history(env)
    command(env,mode)
    assert history(env)==before
    save(env,key,'hold',False)
    command(env,mode)
    assert one(env,key,'result_review',2)['rework_provider_id']==A


