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

@pytest.mark.parametrize('mode', ['full'])
def test_changed_review_to_no_review_drives_completion_without_old_ai(env,decided,monkeypatch,starts,mode):
    key,_,result,_=_spent(env,monkeypatch,starts,mode,follow=False)
    save(env,key,'review_role',{'kind':'not_used'})
    count=len(_loops(starts))
    command(env,mode)
    assert one(env,key,'result_review',2)['actor']=='no_review'
    assert len(_loops(starts))==count
    assert node_run(env,key)['status']=='done'


