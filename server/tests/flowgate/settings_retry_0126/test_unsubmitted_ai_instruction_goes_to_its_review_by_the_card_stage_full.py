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
def test_unsubmitted_ai_instruction_goes_to_its_review_by_the_card_stage(env,decided,monkeypatch,starts,mode):
    """flowgate.v02test.0015 (replaces the 0158 refusal case): an AI instruction written but never submitted (the v0.1
    submit step never ran on it) goes to its review because the card's instruction stage ended -- the review start puts
    it in review itself (``pending_review``, the system as the actor, ``card_review_submitted``) and requests the loop
    in the same savepoint. Formerly the loop's admission refused it (``document_not_submitted``) on every try, and the
    card stood still as ``review_not_started``. Event numbering stays unique."""
    from test_fg_ai_roles_l2_review import _ai_writes_instruction
    key=flow(env,extra_settings=(('instruction_review_count',3),))
    roles(key,instruction=X,execution=A,review=B)
    run_id=command(env,mode)['run_id']
    instruction=_ai_writes_instruction(env,monkeypatch,provider=X)          # body written, never submitted
    _read(env)
    reviews=_instruction_reviews(env,key)
    assert reviews and not any(r['fail_cause']=='review_not_started' for r in reviews)
    assert reviews[0]['seq']==1 and reviews[0]['intent_exec_id']            # the loop's request committed
    doc=_sql(env['db'],'SELECT status, doc_review_status FROM documents WHERE doc_id = ?',(instruction,))[0]
    assert doc['doc_review_status']=='pending_review'
    [submitted]=_sql(env['db'],"SELECT note FROM events WHERE doc_id = ? AND event_type = 'card_review_submitted'",
                     (instruction,))
    note=json.loads(submitted['note'])
    assert note['actor']=='system' and note['actor_kind']=='system'
    seqs=_event_seqs(env,run_id)
    assert len(seqs)==len(set(seqs))
    assert node_run(env,key)['stage']=='instruction' and one(env,key,'result') is None


