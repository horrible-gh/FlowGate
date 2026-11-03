"""0126 T#2: consume human decisions before dispatch and through normal Graph completion.

No pending node is completed here: only the frontier opens it. Reads can repair a lost
post-commit job for open/review nodes. Decisions, reviews and assignments stay immutable;
the completion's decision_id is the explicit override of earlier review waits.
"""
from __future__ import annotations

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
from modules.flow_gate.graph_workflow.services import run_apply, result_binder, run_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store
from flowgate.card_documents import decision_context, decision_repository, lines, slots
from flowgate.card_completion import completion, gates, legacy_adapter as legacy, outputs, own_effects, repository


def _document(flow_id, node, slot):
    line = lines.line_document(flow_id, node['node_key'], slot, node.get('node_type'), node.get('doc_type')) or {}
    doc = line.get('document')
    if not doc or doc.get('group_id') != flow_id or not lines.legacy.is_alive(doc, flow_id):
        return None
    if doc['doc_id'] in legacy.stale_documents(flow_id, node['node_key']):
        return None
    return doc


def instruction_approval(flow_id, node, doc_id):
    doc = _document(flow_id, node, slots.INSTRUCTION)
    if not doc or doc['doc_id'] != doc_id:
        return None
    approval = decision_repository.valid_approval(flow_id, node['node_key'], slots.INSTRUCTION, doc)
    if approval:
        gates.require_gates(doc)
    return approval


def evidence(flow_id, node):
    """Required live outputs with a human-approved result, or None. No raw badge shortcut."""
    if not node or node.get('node_type') == 'gate' or node.get('doc_type') in ('R', 'B'):
        return None
    result_slots = [s.slot for s in slots.approval_slots(node.get('node_type'), node.get('doc_type'))
                    if s.slot != slots.INSTRUCTION]
    for slot in result_slots:
        doc = _document(flow_id, node, slot)
        decision = decision_repository.valid_approval(flow_id, node['node_key'], slot, doc)
        if not decision:
            continue
        try:
            gates.require_gates(doc)
            wanted = outputs.instruction_type(flow_id, node)
            instruction = _document(flow_id, node, slots.INSTRUCTION) if wanted else None
            # A new attempt has no stage yet; do not mistake that for a legacy one-stage card.
            if wanted and str(doc.get('type_code') or '').upper() != wanted:
                if not instruction or instruction.get('type_code') != wanted:
                    return None
                gates.require_gates(instruction)
                projection = decision_repository.projection(flow_id, node['node_key'], slots.INSTRUCTION, instruction)
                if projection.get('doc_review_status', instruction.get('doc_review_status')) not in outputs.APPROVED:
                    return None
            if doc.get('type_code') == 'TR2' and not own_effects._tr2_succeeded(doc['doc_id'], int(doc.get('revision_no') or 0)):
                return None
        except gates.GateRefused:
            return None
        return {'doc': doc, 'decision': decision, 'instruction': instruction}
    return None


def _node(flow_id, run_id, key):
    run = run_store.run_by_id(run_id)
    if not run or run.get('group_id') != flow_id:
        return None
    return run_apply.load_context(run, legacy.project_of(flow_id), None).node_rows.get(key)


def require_evidence(flow_id, run_id, key, doc_id, decision_id, actor_kind, actor_id):
    found = evidence(flow_id, _node(flow_id, run_id, key))
    if not get_store().table_exists('graph_card_completion_decisions'):
        raise completion.CompletionRefused('human_completion_store_missing')
    if (not found or found['doc']['doc_id'] != doc_id or found['decision']['decision_id'] != decision_id
            or actor_kind != 'user' or not actor_id or found['decision']['actor_id'] != actor_id):
        raise completion.CompletionRefused('human_decision_invalid')


def record_valid(rec):
    found = evidence(rec['group_id'], _node(rec['group_id'], rec['run_id'], rec['node_key']))
    if not found or found['doc']['doc_id'] != rec['result_doc_id'] or not rec.get('decision_id'):
        return False
    original = get_store()._fetch_one(
        "SELECT * FROM graph_document_decisions WHERE decision_id = ?", [rec['decision_id']])
    latest = decision_repository.latest(rec['group_id'], rec['node_key'], original['slot']) if original else None
    # Reaffirming unchanged output with a new request keeps the original completion.
    # A changed body/event or T#3's revoked completion cannot inherit that evidence.
    return bool(original and latest and original['document_mark_json'] == latest['document_mark_json']
                and original['action'] == 'approve' and original['doc_id'] == rec['result_doc_id'])


def result_approved(flow_id, run_id, key, doc_id):
    found = evidence(flow_id, _node(flow_id, run_id, key))
    return bool(found and found['doc']['doc_id'] == doc_id)


def _editable(ctx, key, found):
    docs = {'report': found['doc'], 'instruction': found['instruction']}
    try:
        decision_context.require_editable(ctx.flow_id, key, docs)
    except GraphApiError:
        return False
    return True


def before_dispatch(ctx, key):
    found = evidence(ctx.flow_id, ctx.node_rows.get(key))
    if not found or not _editable(ctx, key, found):
        return False
    # The opening is still pending inside apply_decision. It is bound after that
    # opening commits; the durable decisions let a run read repair a lost job.
    ctx.jobs.append(lambda: refresh(ctx.flow_id))
    return True


def _can_continue(ctx):
    from flowgate.graph_run import command
    if ctx.command_pending:
        return False
    if ctx.run.get('status') == 'running':
        return True
    return command.approval_wakes(ctx) and command._waiting_command(ctx, human=True)


def _partial(ctx, row):
    if row.get('status') != 'open' or row.get('stage') != 'instruction' or not _can_continue(ctx):
        return None
    node = ctx.node_rows.get(row['node_key'])
    doc = _document(ctx.flow_id, node, slots.INSTRUCTION) if node else None
    if doc and instruction_approval(ctx.flow_id, node, doc['doc_id']):
        return doc
    return None


def due(ctx, key):
    row = legacy.node_run(ctx.run_id, key)
    if not row or row['status'] not in ('open', 'review') or ctx.run.get('status') == 'stopping':
        return False
    found = evidence(ctx.flow_id, ctx.node_rows.get(key))
    if found and _editable(ctx, key, found):
        if row.get('result_doc_id') not in (None, found['doc']['doc_id']):
            return False
        rec = repository.live(ctx.run_id, key, legacy.active_attempt_id(ctx.run_id, key), found['doc']['doc_id'])
        return (rec is None or row['status'] == 'open'
                or (rec.get('cause') == completion.HUMAN_APPROVED and not record_valid(rec)))
    return _partial(ctx, row) is not None


def reconcile(ctx, key):
    if evidence(ctx.flow_id, ctx.node_rows.get(key)):
        ctx.human_approval = True
    if not due(ctx, key):
        return
    row = legacy.node_run(ctx.run_id, key)
    node = ctx.node_rows[key]
    found = evidence(ctx.flow_id, node)
    from flowgate.card_settings import stage_assignment as sa
    attempt = legacy.active_attempt_id(ctx.run_id, key)
    if not found:
        doc = _partial(ctx, row)
        if not doc:
            return
        decision_context.require_editable(ctx.flow_id, key, {'instruction': doc})
        from flowgate.graph_run import command
        if ctx.run['status'] == 'idle':
            command.wake_approval(ctx)
        # Existing assignments/reviews remain history; the current human decision
        # is the override. No instruction author or AI verdict is rewritten.
        sa.result_stage_start(ctx, node, row, doc['doc_id'], attempt)
        return
    doc, decision = found['doc'], found['decision']
    if row['status'] == 'open':
        sa.legacy.set_stage(ctx.run_id, key, sa.RESULT,
                            found['instruction']['doc_id'] if found['instruction'] else None)
        state = run_apply.read_run_state(ctx)
        change = resolve(ctx.revision, state, RunEvent('result_submitted', key, doc_id=doc['doc_id']),
                         policies_of(run_store.policy_entries(ctx.run_id)))
        if change.ignored:
            return
        run_apply.apply_decision(ctx, change, settle=False)
    completion.record(ctx.flow_id, ctx.run_id, key, attempt, doc['doc_id'],
                      cause=completion.HUMAN_APPROVED, actor_kind='user', actor_id=decision['actor_id'],
                      requested_by=completion.requested_by_of(ctx.run), revision_no=int(doc.get('revision_no') or 0),
                      decision_id=decision['decision_id'])


def refresh(flow_id):
    """After commit and on replay: repair from durable state, never create a run/command."""
    project_id = legacy.project_of(flow_id)
    result_binder.reconcile_if_needed(flow_id, project_id)
    run_service.settle_if_needed(flow_id, project_id)
