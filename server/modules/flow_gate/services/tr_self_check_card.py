"""0053 1-D: the exceptional verification row on a Graph card.

Reads never reconcile a Graph run, claim a queue slot or change a card state. Terminal
feedback is returned by the action only; the next read hides it. No command/output/path
or token is exposed. A button always names the exact self-check run it was drawn for.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flowgate.card_settings import fields, repository
from flowgate.card_settings.verify_limits import resolve_verify_limits
from modules.flow_gate.db import tr_self_check_runs as runs
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.services import tr_self_check_queue as queue
from modules.flow_gate.services.git import instance_registry

GRACE_SECONDS = 60
ACTIVE = ('pending', 'running')


def _now():
    return datetime.now(timezone.utc)


def _time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _node(flow_id, node_key):
    node = repository.card_node(flow_id, node_key)
    if node is None:
        raise GraphApiError(404, 'node_not_found', {'node_key': node_key})
    return node


def _rows(flow_id=None, node_key=None, run_id=None):
    # The attempt relation, not a caller-supplied queue key/TR, proves card ownership.
    sql = ('SELECT s.*, a.node_key FROM tr_self_check_runs s'
           ' JOIN graph_node_attempts a ON a.attempt_id = s.attempt_id'
           ' JOIN graph_runs g ON g.run_id = a.run_id AND g.group_id = s.group_id WHERE 1 = 1')
    args = []
    for column, value in (('s.group_id', flow_id), ('a.node_key', node_key),
                          ('s.self_check_run_id', run_id)):
        if value is not None:
            sql += ' AND ' + column + ' = ?'
            args.append(value)
    if run_id is None:
        sql += " AND s.status IN ('queued', 'pending', 'running')"
    return runs.get_store()._fetch_all(sql, args) or []


def _deadline(row):
    started = _time(row.get('started_at'))
    if started is None:
        return None
    seconds = resolve_verify_limits(row['group_id'], row['node_key']).run_timeout_sec
    return started + timedelta(seconds=seconds + GRACE_SECONDS)


def _latest(rows):
    return max(rows, key=lambda r: (_time(r.get('started_at')) or datetime.min.replace(tzinfo=timezone.utc),
                                   r.get('queue_order') or '', r['self_check_run_id']))


def _brief(row, now):
    started = _time(row.get('started_at'))
    deadline = _deadline(row)
    return {'self_check_run_id': row['self_check_run_id'], 'status': row['status'],
            'started_at': row.get('started_at'), 'enqueued_at': row.get('enqueued_at'),
            'finished_at': row.get('finished_at'),
            'show_at': deadline.isoformat() if deadline else None,
            'elapsed_seconds': max(0, int((now - started).total_seconds())) if started else None}


def status(flow_id, node_key, can_read_flow=lambda project_id: False):
    node = _node(flow_id, node_key)
    out = {'flow_id': flow_id, 'node_key': node_key, 'visible': False, 'state': 'hidden',
           'run': None, 'blocker': None, 'actions': []}
    if fields.card_kind(node.get('node_type'), node.get('doc_type')) != fields.RUN:
        return out
    rows = _rows(flow_id, node_key)
    now = _now()
    executing = [r for r in rows if r['status'] in ACTIVE]
    if executing:
        # A later launch resets the card's display clock, including a restarted run.
        row = _latest(executing)
        deadline = _deadline(row)
        out['run'] = _brief(row, now)
        if deadline is not None and now >= deadline:
            out.update(visible=True, state='stop_failed' if row.get('cancel_requested') else 'overdue',
                       actions=['force_stop'])
        return out
    waiting = [r for r in rows if r['status'] == 'queued']
    if not waiting:
        return out
    row = max(waiting, key=lambda r: (r.get('queue_order') or '', r['self_check_run_id']))
    out['run'] = _brief(row, now)
    host = instance_registry.node_key()
    cards = {}
    for candidate in _rows():
        if candidate['status'] not in ACTIVE:
            continue
        if host and candidate.get('host_node_key') not in (None, host):
            continue
        key = (candidate['group_id'], candidate['node_key'])
        cards.setdefault(key, []).append(candidate)
    overdue = []
    for candidates in cards.values():
        candidate = _latest(candidates)
        deadline = _deadline(candidate)
        if deadline is not None and now >= deadline:
            overdue.append(candidate)
    if overdue:
        out.update(visible=True, state='waiting', actions=['cancel_waiting'])
        # Do not disclose another project's card identity to an unauthorized reader.
        readable = [r for r in overdue if r['group_id'] == flow_id or can_read_flow(r['project_id'])]
        if readable:
            blocker = min(readable, key=lambda r: _time(r['started_at']))
            card = repository.card_node(blocker['group_id'], blocker['node_key']) or {}
            out['blocker'] = dict(_brief(blocker, now), flow_id=blocker['group_id'],
                                  node_key=blocker['node_key'], label=card.get('label'),
                                  project_id=blocker.get('project_id'))
    return out


def control(flow_id, node_key, run_id, action):
    node = _node(flow_id, node_key)
    if fields.card_kind(node.get('node_type'), node.get('doc_type')) != fields.RUN:
        raise GraphApiError(409, 'selfcheck_not_supported')
    rows = _rows(flow_id, node_key, run_id)
    if not rows:
        raise GraphApiError(404, 'selfcheck_run_not_found')
    row = rows[0]
    if action == 'cancel_waiting':
        if row['status'] == 'cancelled' and row.get('error_code') == queue.CANCELLED_WAITING:
            ended = row  # safe repeat of the same button
        else:
            ended = queue.cancel_waiting(run_id)
            if ended is None:
                raise GraphApiError(409, 'selfcheck_not_waiting')
        state = 'cancelled'
    else:
        if row['status'] == 'queued':
            raise GraphApiError(409, 'selfcheck_not_running')
        try:
            ended = queue.force_stop(run_id)
        except queue.QueueError as exc:
            raise GraphApiError(409, exc.code) from exc
        state = 'stopped' if ended and ended['status'] in runs.QUEUE_TERMINAL else 'stop_failed'
    return {'flow_id': flow_id, 'node_key': node_key, 'visible': True, 'state': state,
            'run': _brief(dict(ended or row, node_key=row['node_key']), _now()), 'blocker': None,
            'actions': ['force_stop'] if state == 'stop_failed' else []}
