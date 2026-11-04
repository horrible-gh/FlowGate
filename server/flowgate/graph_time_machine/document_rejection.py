"""0126 T#3: scoped document rejection using Time Machine's durable Git/DB executor.

The plan is committed before Git. Only its final transaction changes document decisions,
current node projections and effect validity. The ordinary Time Machine endpoint is never
called. Recovery dispatches this plan from the same startup journal, before execution replay.
"""
import json

from modules.flow_gate.db import documents, groups, tr2_approval_attempts, tr_commit_ledger
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.services import run_apply, canvas_events
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store
from flowgate.card_documents import decision_context as context, decision_repository as decisions_store
from flowgate.card_settings.report_baseline import fingerprint
from . import impact, repository, journal, operation, rewind, rejection_store

KIND = 'document_rejection'


def _effects(flow_id, key, docs):
    store = get_store()
    return bool(store._fetch_one(
        "SELECT 1 FROM graph_card_completions WHERE group_id=? AND node_key=? AND status IN ('recorded','applied') LIMIT 1",
        [flow_id, key]) or store._fetch_one(
        "SELECT 1 FROM tr_commit_ledger WHERE group_id=? AND node_key=? AND state='live' LIMIT 1", [flow_id, key])
        or any(d and (d.get('type_code') == 'TR2' and store._fetch_one(
                          "SELECT 1 FROM tr_commit_ledger WHERE doc_id=? AND state='live' LIMIT 1", [d['doc_id']])
                      or d.get('type_code') == 'TS' and d.get('doc_review_status') in ('approved','wf_done')
                      and store._fetch_one('SELECT 1 FROM test_runs WHERE doc_id=? LIMIT 1', [d['doc_id']]))
               for d in docs.values()))


def _own_effects(docs, preserve_instruction=False):
    """Terminal test runs are history; active own effects are never terminated by rejection."""
    commits = []
    for slot, doc in docs.items():
        if not doc:
            continue
        if doc.get('type_code') == 'TS':
            busy = get_store()._fetch_one("SELECT run_id FROM test_runs WHERE doc_id=? AND status IN ('running','cancelling') LIMIT 1", [doc['doc_id']])
            if busy:
                context.refuse('document_rejection_scope_busy', doc_id=doc['doc_id'], test_run_id=busy['run_id'])
        if doc.get('type_code') != 'TR2' or preserve_instruction and slot == 'instruction':
            continue
        if tr2_approval_attempts.in_progress(doc['doc_id']) or tr2_approval_attempts.recovery_required(doc['doc_id']):
            context.refuse('document_rejection_effect_unresolved', doc_id=doc['doc_id'])
        success = tr2_approval_attempts.latest_success(doc['doc_id'])
        if not success:
            continue
        ledger = tr_commit_ledger.get_by_id(success['ledger_row_id']) if success.get('ledger_row_id') else None
        if not ledger or ledger.get('state') == 'live' and not success.get('commit_sha'):
            context.refuse('document_rejection_effect_unattributed', doc_id=doc['doc_id'])
        if ledger['state'] == 'live':
            commits.append({'kind': 'tr2', 'attempt_id': success['attempt_id'], 'doc_id': doc['doc_id'],
                            'ledger_row_id': ledger['id'], 'commit_sha': success['commit_sha'],
                            'parent_commit': success['pre_apply_head_sha']})
    return commits


def assess(flow_id, node_key, docs, *, recovery_operation=None):
    snapshot = repository.read_snapshot(flow_id)
    row = next((r for r in snapshot.nodes if r['node_key'] == node_key), {})
    attempt = next((a for a in snapshot.attempts if a['node_key'] == node_key), {})
    needed = (row.get('status') == 'done' or bool(attempt.get('integrated_commit'))
              or attempt.get('integration_status') not in (None, '', 'none') or _effects(flow_id, node_key, docs))
    # Active tests and unresolved TR2 effects fence even an unfinished card.
    _own_effects(docs)
    if not needed:
        return None
    if not rejection_store.available():
        context.refuse('document_recovery_unavailable')
    result = impact.calculate(snapshot, node_key)
    keys = set(result['impact']['node_keys'])
    ids = {a['attempt_id'] for a in result['impact']['attempts']}
    # The original endpoint requires an idle Flow. A document decision instead
    # checks the affected cards. Independent requested/running work remains intact.
    scoped = {'run_not_idle', 'executions_in_progress', 'integrations_in_progress', 'merge_conflict_open',
              'gate_jobs_in_progress', 'node_not_done', 'previous_operation_incomplete'}
    reasons = [r for r in result['reasons'] if r['code'] not in scoped]
    completions = run_store._all(
        "SELECT * FROM graph_card_completions WHERE group_id=? AND status IN ('recorded','applied')", [flow_id])
    for node_row in snapshot.nodes:
        if node_row['node_key'] not in keys:
            continue
        current_id = next((a['attempt_id'] for a in snapshot.attempts if a['node_key']==node_row['node_key']), None)
        live = [c for c in completions if c['node_key']==node_row['node_key']]
        graph = snapshot.revisions.get(snapshot.run['revision_no']) if snapshot.run else None
        gate = graph.node(node_row['node_key']) if graph else None
        is_gate = gate is not None and gate.node_type == 'gate'
        if node_row['status']=='done' and not node_row.get('root_bound') and not is_gate and not any(
                c['status']=='applied' and c.get('applied_event_seq') is not None
                and c['attempt_id']==current_id and c['result_doc_id']==node_row.get('result_doc_id') for c in live):
            reasons.append({'code':'completion_provenance_missing','node_key':node_row['node_key']})
        if any(c['run_id'] != (snapshot.run or {}).get('run_id') or c['attempt_id'] != current_id
               or c['result_doc_id'] != node_row.get('result_doc_id') for c in live):
            reasons.append({'code':'completion_context_mismatch','node_key':node_row['node_key']})
    for completion in completions:
        if completion['node_key'] not in keys or completion['status'] != 'applied':
            continue
        event = run_store._one('SELECT payload_json FROM graph_run_events WHERE run_id=? AND event_seq=?',
                               [completion['run_id'], completion.get('applied_event_seq')])
        propagation = run_store._one('SELECT node_key FROM graph_propagations WHERE run_id=? AND source_event_seq=?',
                                    [completion['run_id'], completion.get('applied_event_seq')])
        try:
            linked = json.loads(event['payload_json']).get('completion_id') if event else None
        except (TypeError, ValueError):
            linked = None
        if linked != completion['completion_id'] or not propagation or propagation['node_key'] != completion['node_key']:
            reasons.append({'code':'propagation_provenance_missing','completion_id':completion['completion_id']})
    result['impact']['completions'] = [c for c in completions if c['node_key'] in keys]
    source_sequences = {c['applied_event_seq'] for c in result['impact']['completions'] if c.get('applied_event_seq')}
    result['impact']['propagations'] = [p for p in (run_store.propagations(snapshot.run['run_id']) if snapshot.run else [])
        if p['node_key'] in keys and (p['source_event_seq'] in source_sequences or p['status']=='pending')]
    for a in snapshot.attempts:
        if a['attempt_id'] in ids and a.get('integration_status') not in (None,'','none') and not a.get('integrated_commit'):
            reasons.append({'code':'integration_evidence_missing','attempt_id':a['attempt_id']})
    for ledger in run_store._all("SELECT * FROM tr_commit_ledger WHERE group_id=? AND state='live'", [flow_id]):
        if ledger.get('node_key') in keys and ledger.get('attempt_id') not in ids:
            reasons.append({'code':'commit_attribution_missing','ledger_row_id':ledger['id']})
    if snapshot.run and snapshot.run['status'] not in ('idle', 'running', 'paused'):
        reasons.append({'code': 'run_transition_in_progress'})
    if any(op['operation_id'] != recovery_operation for op in snapshot.incomplete_operations):
        reasons.append({'code': 'previous_operation_incomplete', 'operations': snapshot.incomplete_operations})
    for name, records in [('integrations_in_progress', snapshot.integrations),
                          ('merge_conflict_open', snapshot.conflict_sessions), ('gate_jobs_in_progress', [{**j, 'node_key': j.get('target_key')} for j in snapshot.gate_jobs])]:
        selected = [r for r in records if r.get('attempt_id') in ids or r.get('node_key') in keys
                    or not r.get('attempt_id') and not r.get('node_key')]
        if selected:
            reasons.append({'code': name, 'items': selected})
    if reasons:
        context.refuse('document_rejection_recovery_required', impact=result['impact'], reasons=reasons,
                       resume='resolve_conflict_then_retry')
    contexts = {}
    for key in sorted(keys):
        _node, _row, ctx, current_docs = context.current(flow_id, key)
        context.require_editable(flow_id, key, current_docs, **(
            {'recovery_operation': recovery_operation} if recovery_operation else {}))
        _own_effects(current_docs)
        contexts[key] = ctx
    return {**result, 'allowed': True, 'reasons': [], 'contexts': contexts}


def execute(group, key, slot, body, actor, validate, digest):
    flow_id = group['group_id']
    def begin():
        found = validate()
        if isinstance(found, dict):
            return {'replay': found}
        _node, _row, _ctx, docs = context.current(flow_id, key)
        assessment = assess(flow_id, key, docs)
        if assessment is None:
            return None
        plan = operation._plan(flow_id, assessment, 'ko')
        op_id = journal.new_operation_id()
        plan.update(kind=KIND, operation_id=op_id, group_id=flow_id, actor_id=actor,
                    slot=slot, body=body, request_hash=digest,
                    contexts=assessment['contexts'], generation=rejection_store.generation(flow_id)+1,
                    impact=assessment['impact'], invalidation_reason='document_rejected:' + op_id)
        # Reopen only current dependent outputs. The selected card's decision is
        # written by decisions._record, preserving its instruction on report rejection.
        plan['documents'] = []
        for target_key in plan['node_keys']:
            _n, _r, _c, current_docs = context.current(flow_id, target_key)
            for commit in _own_effects(current_docs, target_key == key and slot == 'report'):
                commit['node_key'] = target_key
                # An attempt merge may already include the TR2 change; never revert both.
                if any(c.get('result_commit') == commit['commit_sha'] for c in plan['commits']):
                    continue
                if commit['commit_sha'] not in {c['commit_sha'] for c in plan['commits']}:
                    plan['commits'].append(commit)
        ctx = run_apply.load_context(run_store.run_by_id(assessment['run_id']), group.get('project_id'), actor)
        journal.emit(ctx, op_id, journal.STARTED, node_key=key, plan=plan)
        return plan
    plan = with_flow_guard(flow_id, begin)
    if plan is None:
        return None
    if 'replay' in plan:
        return plan['replay']
    outcome = operation._finish(flow_id, body['expected_context']['run_id'], plan['operation_id'], plan, actor)
    if outcome['operation_status'] != journal.COMPLETED:
        context.refuse('document_rejection_recovery_failed', **outcome, impact=plan['impact'],
                       resume='retry_after_conflict_resolution' if outcome['operation_status'] == journal.BLOCKED
                       else 'graph_time_machine.operation.recover')
    return json.loads(decisions_store.request(flow_id, key, body['request_key'])['result_json'])


def finish(ctx, plan, reverts):
    """One fenced DB transaction: recheck all contexts, decide, invalidate and preserve history."""
    from flowgate.card_documents import decisions
    group = groups.get_by_id(ctx.flow_id)
    context.authorize(plan['actor_id'], group, 'reject')
    for key, original in plan['contexts'].items():
        _n, _r, current, docs = context.current(ctx.flow_id, key)
        if current != original:
            raise rewind.RewindRefused('document_context_changed', node_key=key)
        context.require_editable(ctx.flow_id, key, docs, recovery_operation=plan['operation_id'])
        _own_effects(docs)
    # Check before writing a badge; a failure here or later rolls everything back.
    rewind._check(ctx, plan['node_keys'], plan, reverts)
    receipt = decisions.write(group, plan['node_key'], plan['slot'], plan['body'], plan['actor_id'], _recovery=plan)
    request_id = receipt['request_id']
    plan['decision_id'] = decisions_store.latest(ctx.flow_id, plan['node_key'], plan['slot'])['decision_id']
    root_key = plan['node_key']
    for key, original in plan['contexts'].items():
        version = decisions_store.version(ctx.flow_id, key)
        for slot, mark in original['documents'].items():
            if not mark:
                continue
            preserved = key == root_key and plan['slot'] == 'report' and slot == 'instruction'
            rejection_store.append(plan, key, 'document', mark['doc_id'], 'preserved' if preserved else 'invalidated',
                                   {'slot': slot, 'mark': {k:v for k,v in mark.items() if k != 'doc_review_status'},
                                    'decision_version': version, 'request_id': request_id})
            if key != root_key and mark['doc_review_status'] in ('approved', 'wf_done'):
                if documents.update_review_status_cas(mark['doc_id'], mark['doc_review_status'], 'pending_review') is None:
                    raise rewind.RewindRefused('document_context_changed', doc_id=mark['doc_id'])
        rejection_store.append(plan, key, 'node', key, 'invalidated', {'request_id': request_id})
    ids = plan['attempt_ids']
    for a in plan['impact']['attempts']:
        rejection_store.append(plan, a['node_key'], 'attempt', a['attempt_id'], 'invalidated', {'request_id': request_id})
    for token in run_store._all('SELECT token_id, node_key, attempt_id FROM graph_run_token_map WHERE run_id=?', [ctx.run_id]):
        if token['attempt_id'] in ids:
            rejection_store.append(plan, token['node_key'], 'token', token['token_id'], 'invalidated',
                                   {'attempt_id': token['attempt_id'], 'request_id': request_id})
    for row in plan['impact'].get('propagations', []):
        if row['node_key'] in plan['node_keys']:
            rejection_store.append(plan, row['node_key'], 'propagation', str(ctx.run_id)+':'+str(row['source_event_seq']),
                                   'invalidated', {**row, 'request_id': request_id})
    for row in run_store._all("SELECT * FROM graph_card_completions WHERE run_id=? AND status IN ('recorded','applied')", [ctx.run_id]):
        if row['node_key'] in plan['node_keys']:
            rejection_store.append(plan, row['node_key'], 'completion', row['completion_id'], 'invalidated',
                                   {'request_id': request_id, 'applied_event_seq': row['applied_event_seq']})
    for assignment in run_store._all('SELECT * FROM graph_stage_assignments WHERE run_id=?', [ctx.run_id]):
        if assignment['attempt_id'] in ids:
            item_id = ':'.join(str(assignment[k]) for k in ('attempt_id','stage','seq'))
            rejection_store.append(plan, assignment['node_key'], 'assignment', item_id, 'invalidated',
                                   {'request_id':request_id, 'stage_state':assignment['stage_state']})
    result = rewind.rewind(ctx, plan, reverts)
    canvas_events.publish_canvas_changed(project_id=ctx.project_id, flow_id=ctx.flow_id,
        reason='documents', actor_user_id=plan['actor_id'], origin_client_id=None, node_keys=plan['node_keys'])
    return {**result, 'request_id': request_id, 'decision_id': plan['decision_id'], 'generation': plan['generation'],
            'next_stage': 'result' if plan['slot'] == 'report' else 'instruction', 'node_keys': plan['node_keys']}


def finish_receipt(plan, result):
    from flowgate.card_documents import decisions
    group = groups.get_by_id(plan['group_id'])
    receipt = decisions_store.request(plan['group_id'], plan['node_key'], plan['body']['request_key'])
    saved = json.loads(receipt['result_json'])
    decisions_store.finish_request(receipt['request_id'], {
        **saved, **decisions._view(group, plan['node_key'], plan['actor_id']),
        'recovery': {'operation_id': plan['operation_id'], **result}})
