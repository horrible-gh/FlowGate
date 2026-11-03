"""0126 T#3: append-only invalidation/carry evidence, linked to the durable Git/DB journal."""
import json

from modules.flow_gate.db.connection import get_store, now_iso
from flowgate.card_settings.report_baseline import fingerprint

TABLE = 'graph_document_recovery_items'


def available():
    return get_store().table_exists(TABLE)


def generation(flow_id, node_key=None):
    if not available():
        return 0
    sql = f'SELECT MAX(generation) AS n FROM {TABLE} WHERE group_id = ?'
    args = [flow_id]
    if node_key is not None:
        sql += ' AND node_key = ?'
        args.append(node_key)
    return int((get_store()._fetch_one(sql, args) or {}).get('n') or 0)


def append(plan, key, kind, item_id, disposition, detail):
    get_store()._execute(
        f'INSERT INTO {TABLE} (operation_id,group_id,node_key,generation,item_kind,item_id,disposition,detail_json,created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        [plan['operation_id'], plan['group_id'], key, plan['generation'], kind, str(item_id), disposition,
         json.dumps({**detail, 'decision_id': plan.get('decision_id')}, ensure_ascii=False), now_iso()])


def document(flow_id, key, doc_id):
    if not available():
        return None
    row = get_store()._fetch_one(
        f'SELECT * FROM {TABLE} WHERE group_id = ? AND node_key = ? AND item_kind = ? AND item_id = ? '
        'ORDER BY generation DESC LIMIT 1', [flow_id, key, 'document', doc_id])
    return {**row, 'detail': json.loads(row['detail_json'])} if row else None


def recoverable(flow_id, key, doc):
    """A human may explicitly decide a current line again; old worker tokens remain fenced."""
    row = document(flow_id, key, doc['doc_id'])
    if not row:
        return False
    from modules.flow_gate.graph_workflow.store import run_store
    owner = run_store.registered_document_attempt(flow_id, doc['doc_id'])
    if owner:
        attempt = get_store()._fetch_one('SELECT status,invalidated_reason FROM graph_node_attempts WHERE attempt_id=?',
                                        [owner['attempt_id']])
        if attempt and attempt['status']=='invalidated' and attempt['invalidated_reason'] != 'document_rejected:'+row['operation_id']:
            return False
    return True


def carried(flow_id, key, doc):
    row = document(flow_id, key, doc['doc_id']) if doc else None
    if not row or not recoverable(flow_id, key, doc) or doc.get('doc_review_status') not in ('approved', 'wf_done'):
        return False
    detail = row['detail']
    if row['disposition'] == 'preserved' and detail['mark'] == fingerprint(doc):
        return True
    # A later explicit human decision may reuse the line, never an old approval.
    from flowgate.card_documents import decision_repository
    latest = decision_repository.latest(flow_id, key, detail['slot'])
    return bool(latest and latest['decision_version'] > detail['decision_version']
                and latest['action'] == 'approve'
                and json.loads(latest['document_mark_json']) == fingerprint(doc))


def waiting(flow_id):
    if not available():
        return frozenset()
    rows = get_store()._fetch_all(
        f"SELECT n.node_key FROM {TABLE} n WHERE n.group_id=? AND n.item_kind='node' "
        f"AND NOT EXISTS (SELECT 1 FROM {TABLE} c WHERE c.operation_id=n.operation_id "
        "AND c.node_key=n.node_key AND c.item_kind='command')", [flow_id])
    return frozenset(r['node_key'] for r in rows)


def release_for_command(flow_id):
    if not available():
        return
    rows = get_store()._fetch_all(
        f"SELECT n.* FROM {TABLE} n WHERE n.group_id=? AND n.item_kind='node' "
        f"AND NOT EXISTS (SELECT 1 FROM {TABLE} c WHERE c.operation_id=n.operation_id "
        "AND c.node_key=n.node_key AND c.item_kind='command')", [flow_id])
    for r in rows:
        append(r, r['node_key'], 'command', r['node_key'], 'released', {'cause': 'explicit_command'})


def without_carried(flow_id, key, ids):
    from modules.flow_gate.db import documents
    return {doc_id for doc_id in ids if not carried(flow_id, key, documents.get_by_id(doc_id))}
