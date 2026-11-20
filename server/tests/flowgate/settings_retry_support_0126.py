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


def history(env):
    return {t: _sql(env['db'], 'SELECT * FROM '+t) for t in (
        'document_revisions', 'document_reviews', 'graph_stage_assignments')}


def new_settings(env, key):
    before = get_settings(env, FA, key)
    for field, value in [('execution_role', {'kind':'assigned','provider_id':A}),
                         ('review_role', {'kind':'assigned','provider_id':Y}), ('review_count',1)]:
        after = save(env,key,field,value)
    assert after['settings_version'] == before['settings_version'] + 3
    return before, after


def _submitted_ai_instruction(env,monkeypatch,provider):
    """0158: the instruction AI's write as a submitted document (``pending_review``), as an edit submission leaves it.
    ``_ai_writes_instruction`` alone writes the body only; its review start then submits it itself (flowgate.v02test.0015,
    the case below), which is not this recovery case."""
    import test_fg_ai_roles_l2_review as l2_review
    from modules.flow_gate.db import documents as db_documents
    real=l2_review._write_written

    def written_and_submitted(doc_id,*args,**kwargs):
        real(doc_id,*args,**kwargs)
        db_documents.update(doc_id,{'doc_review_status':'pending_review'})

    monkeypatch.setattr(l2_review,'_write_written',written_and_submitted)
    try:
        return l2_review._ai_writes_instruction(env,monkeypatch,provider=provider)
    finally:
        monkeypatch.setattr(l2_review,'_write_written',real)


def _instruction_reviews(env,key):
    return sorted((a for a in assignments(env,key) if a['stage']=='instruction_review'),key=lambda a:a['seq'])


def _event_seqs(env,run_id):
    return [r['event_seq'] for r in _sql(env['db'],'SELECT event_seq FROM graph_run_events WHERE run_id = ?',(run_id,))]


