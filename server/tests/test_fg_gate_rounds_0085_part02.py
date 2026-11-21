"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_rounds_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_rounds_0085 import (
    FA,
    LEE,
    Path,
    _act,
    _assert_link,
    _attempt,
    _confirm,
    _confirm_next,
    _confirmed,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _facts,
    _finish,
    _gate,
    _gate_events,
    _git,
    _h,
    _history,
    _inspect,
    _job_row,
    _jobs,
    _linear,
    _nodes,
    _parked_after_merge,
    _resume,
    _rev,
    _rewind,
    _run_job,
    _side_commit,
    _sql,
    _start,
    _states,
    _version,
    _work,
    copy,
    db_path,
    edge,
    env,
    git_env,
    json,
    node,
    product,
    pytest,
    setting,
    sys,
    time_machine_routes,
)

@pytest.mark.parametrize('status', ['pending', 'running', 'recovery_required', 'succeeded', 'cancelled', 'failed'])
def test_time_machine_excludes_terminal_gate_jobs_and_blocks_unfinished_jobs(product, status):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store
    env = product
    g, _t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject'})
    with get_store().transaction():
        job = job_store.insert_gate_publish('flowgate', {'decision_id': 'fixture', 'group_id': FA,
                                            'run_id': run_id, 'node_key': g}, group_id=FA, target_key=g)
    _sql(env['db'], 'UPDATE operation_job SET status = ? WHERE job_id = ?', (status, job['job_id']))
    assessed = _inspect(g)
    terminal = status in ('succeeded', 'cancelled', 'failed')
    assert assessed['allowed'] is terminal
    if not terminal:
        assert 'gate_jobs_in_progress' in {r['code'] for r in assessed['reasons']}
        before = _facts(env)
        _rewind(env, run_id, g, expect=409)
        assert _facts(env) == before
    else:
        _rewind(env, run_id, g)
def test_unfinished_job_blocks_revision_and_protects_its_pending_gate_and_inputs(product):
    from flowgate.graph_run.editing import protect_definition
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store
    env = product
    g, t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject'})
    _rewind(env, run_id, g)
    _confirm_next(t, 'gn_extra')
    with get_store().transaction():
        job = job_store.insert_gate_publish('flowgate', {'decision_id': 'fixture', 'group_id': FA,
                                           'run_id': run_id, 'node_key': g}, group_id=FA, target_key=g)
    out = _resume(env, run_id, expect=409)
    assert out['error']['code'] == 'graph_revision_migration_blocked'
    assert out['error']['params']['reason'] == 'gate_jobs_in_progress'
    before = definition_store.revision(FA, 1)
    for remove_node in (False, True):
        nodes = [n for n in before['nodes'] if not remove_node or n['node_key'] != g]
        edges = [e for e in before['edges'] if e['target'] != g]
        with pytest.raises(GraphApiError) as error:
            protect_definition(FA, nodes, edges)
        assert error.value.code == 'graph_node_protected'
    _sql(env['db'], "UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?", (job['job_id'],))
    assert _resume(env, run_id)['run']['revision_no'] == 2
@pytest.mark.parametrize('keep_branch', [True, False], ids=['kept_slot', 'released_slot'])
def test_done_gate_rewinds_only_internal_integration_and_preserves_publish_and_archives(git_env, keep_branch):
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.services import git_service as svc
    r, env = git_env, git_env['env']
    keys = _confirmed(env, [node('tmp_r', 'R'), node('tmp_g', None, 'gate'),
                           node('tmp_t', 'T', assignee=LEE), node('tmp_end', None, 'gate')],
                      [edge('tmp_1', 'tmp_r', 'tmp_g'), edge('tmp_2', 'tmp_g', 'tmp_t'),
                       edge('tmp_3', 'tmp_t', 'tmp_end')])
    g, t, end = (keys['tmp_' + key] for key in ('g', 't', 'end'))
    run_id = _start(env)['run_id']
    _gate(env, g, merge_mode='merge', merge_target='origin', push=True, archive=True, keep_branch=keep_branch)
    sha = _work(r, 'gate-result.txt')
    out = _confirm(env, run_id, g, 'first')
    did = out['decision']['decision_id']
    assert _run_job(out['job']['job_id'])[0]['status'] == 'succeeded'
    # Attribute one downstream integration, exactly as result_merge records it.
    start = _rev(r['wt'], 'HEAD')
    result_sha = _side_commit(r, start, 'tail.txt')
    _git(['merge', '--no-ff', '-m', 'tail integration', result_sha], cwd=r['wt'])
    integrated = _rev(r['wt'], 'HEAD')
    result_doc = _finish(env, t, 90)
    aid = _attempt(env, run_id, t)['attempt_id']
    _sql(env['db'], "UPDATE graph_node_attempts SET input_source = 'predecessors', input_commit = ?, "
                    "result_commit = ?, integrated_commit = ?, integration_status = 'integrated', "
                    "input_attempt_ids = ? WHERE attempt_id = ?",
         (start, result_sha, integrated, json.dumps([_decision(env, did)['attempt_id']]), aid))
    tr_commit_ledger.record_commit(group_id=FA, doc_id=result_doc, commit_sha=result_sha,
                                  commit_subject='tail', attempt_id=aid, node_key=t)
    # The completed run's actual end HEAD is newer than the first gate's archive. A terminal merge makes
    # that real end reachable, allowing normal keep_branch=false cleanup to release both branch and worktree.
    _resume(env, run_id)
    _gate(env, end, merge_mode='merge', merge_target='origin', push=True, archive=False, keep_branch=False)
    final = _confirm(env, run_id, end, 'end')
    assert _run_job(final['job']['job_id'])[0]['status'] == 'succeeded'
    assert _current(env)['idle_reason'] == 'completed'
    external = _rev(r['bare'], 'refs/heads/main')
    for job in _sql(env['db'], "SELECT job_id FROM operation_job WHERE kind = 'gate_cleanup' AND status = 'pending'"):
        assert _run_job(job['job_id'])[0]['status'] == 'succeeded'
    record = _sql(env['db'], 'SELECT * FROM gate_cleanup_records')[-1]
    assert record['end_head_sha'] == integrated and integrated != sha
    if keep_branch:
        assert record['result'] == 'retained' and r['wt'].is_dir()
    else:
        assert record['result'] == 'deleted' and record['basis_kind'] == 'merged_target'
        assert not r['wt'].exists() and svc.db_git.get_state(FA)['worktree_registered'] == 0
        assert _rev(r['base'], f"refs/heads/{r['branch']}") is None
    checkpoints = _sql(env['db'], 'SELECT * FROM gate_archive_checkpoints')
    effects = _effects(env, did)
    assessed = _inspect(g)
    assert assessed['allowed']
    history = assessed['impact']['gate_decisions']
    original = next(entry for entry in history if entry['decision_id'] == did)
    assert original['external_effects'] == 'retained' and original['archive_action'] == 'preserved'
    tm = _rewind(env, run_id, g)['time_machine']
    assert [c['commit_sha'] for c in tm['reverts']] == [integrated]
    revert = tm['reverts'][0]['revert_commit']
    assert _rev(r['wt'], revert + '^1') == integrated   # restored at actual end HEAD, never the old archive
    assert svc.db_git.get_state(FA)['worktree_registered'] == 1
    assert not (r['wt'] / 'tail.txt').exists() and (r['wt'] / 'gate-result.txt').exists()
    assert _rev(r['base'], 'refs/heads/main') == _rev(r['bare'], 'refs/heads/main') == external
    assert _sql(env['db'], 'SELECT * FROM gate_archive_checkpoints') == checkpoints
    assert _effects(env, did) == effects
    assert _states(env)[g] == _states(env)[t] == 'pending'
    sha2 = _work(r, 'gate-result.txt', 'reworked\n')
    assert sha2 != sha
    _resume(env, run_id)
    new = _confirm(env, run_id, g, 'second')
    assert _run_job(new['job']['job_id'])[0]['status'] == 'succeeded'
    new_id = new['decision']['decision_id']
    _assert_link(env, did, new_id, tm['operation_id'])
    assert _decision(env, new_id)['frozen_sha'] == sha2
    assert len(_sql(env['db'], 'SELECT * FROM gate_archive_checkpoints')) == 2
    assert _rev(r['base'], f'refs/flowgate/gate-archive/{did}') == sha
