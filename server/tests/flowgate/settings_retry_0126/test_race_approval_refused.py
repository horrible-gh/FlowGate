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
@pytest.mark.parametrize('winner', ['automatic', 'command'])
@pytest.mark.parametrize('reason', ['approval_refused'])
def test_system_error_command_and_stale_automatic_retry_open_one_seq(
        env, decided, monkeypatch, starts, mode, winner, reason):
    """Both serialized lock orders, even when the winner's new start fails (no active-work guard)."""
    from flowgate.card_settings import assigned_review as ar, legacy_adapter as legacy
    from flowgate.graph_run import command as commands
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store
    from test_fg_command_rerequest_0124 import _refuse_starts, _spent as same_submission_spent

    key, _, _, result, first = same_submission_spent(env, monkeypatch, starts, stop='total_timeout')
    # A failed start of this submission, with no needs-intervention barrier from the spent fixture.
    _sql(env['db'], "DELETE FROM graph_run_events WHERE cause='auto_approval_skipped'")
    _sql(env['db'], "UPDATE graph_stage_assignments SET stage_state='failed', verdict=NULL, "
                   "fail_cause='review_not_started' WHERE attempt_id=? AND stage='result_review'",
         (first['attempt_id'],))
    _reason(env, key, result, first, reason)
    stale = one(env, key, 'result_review', 1)
    assert ar.review_retry_due(stale)
    frozen = dict(stale)

    def begin_command():
        old = legacy.active_run(FA)
        assert run_store.set_run_status(old['run_id'], 'idle', 'running') == 1
        ctx = run_apply.load_context({**old, 'status': 'running'}, 'flowgate', 'u_kim', [])
        commands.record_command(ctx, mode)
        run_apply.emit_run_status(ctx, 'idle', 'running', 'user_command')
        assert run_store.set_run_command(old['run_id'], mode, [key] if mode == 'step' else None) == 1
        return legacy.run_by_id(old['run_id'])

    run = with_flow_guard(FA, begin_command)
    outside = legacy.node_run(run['run_id'], key)
    assert ar.rerequest_due(FA, run, outside, None) is not None
    refused, _ = _refuse_starts(monkeypatch)

    def automatic():
        jobs = []
        ar.retry_review_stage(FA, stale, ar.HELD, jobs)
        ar._run_jobs(jobs)

    def continuation():
        ar._command_review_row(FA, run, outside)

    if winner == 'automatic':
        automatic()
        continuation()
    else:
        continuation()
        automatic()
    assert one(env, key, 'result_review', 1) == frozen
    rows = _reviews(env, key)
    assert [r['seq'] for r in rows] == [1, 2]
    assert rows[-1]['stage_state'] == 'failed'
    from flowgate.card_settings import stage_assignment as sa
    assert sa.basis_of(rows[0]) == sa.basis_of(rows[1]) == sa.mark(_doc(result))
    requests = [r for r in ledger(env) if r['assignment_stage'] == 'result_review' and r['assignment_seq'] == 2]
    assert len(requests) == 1 and len(refused) == 1
    assert ar.review_start_tries(rows[-1]) <= 2


