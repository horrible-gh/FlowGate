"""0428 T#3: mutation boundaries for completion decisions.

Take slot write locks before mutating the evidence; generation, decision history
and waiting dispatch cancellation commit with the mutation or roll back together.
No approval, source application or continuation runs here.
"""
from __future__ import annotations

import json
import inspect
from functools import wraps
from contextlib import contextmanager

from .connection import get_store
from . import workflow_completion as completion


def _write_intent(store) -> None:
    # A deferred SQLite transaction must acquire its writer reservation before
    # reading candidate slots. Otherwise a competing dispatch writer can make
    # the read-to-write upgrade fail immediately (busy_timeout cannot fix it).
    # On other engines this no-row UPDATE has no row lock or mutation.
    store._execute('UPDATE workflow_sequence_items SET slot_generation = slot_generation WHERE id = -1', [])


def document_slots(doc_id: str, *, deleting: bool = False) -> list[dict]:
    store = get_store()
    # Include linked reports even before their test_run has been associated. The
    # paired TS slot is authoritative when old documents lack target/triggered_by.
    return store._fetch_all(
        "SELECT DISTINCT s.* FROM workflow_sequence_items s "
        "LEFT JOIN documents d ON d.doc_id = s.result_doc_id "
        "LEFT JOIN workflow_sequences q ON q.id = s.sequence_id "
        "WHERE s.result_doc_id = ? "
        "OR (s.type = 'TSR' AND (d.target_id = ? OR d.triggered_by = ? "
        "OR EXISTS (SELECT 1 FROM test_runs r WHERE r.doc_id = ? AND r.tsr_doc_id = s.result_doc_id) "
        "OR EXISTS (SELECT 1 FROM workflow_sequence_items ts WHERE ts.sequence_id = s.sequence_id "
        "AND ts.type = 'TS' AND ts.result_doc_id = ? AND ts.sort_order = s.sort_order - 1))) "
        "OR (? = 1 AND q.doc_id = ?) ORDER BY s.sequence_id, s.id",
        [doc_id, doc_id, doc_id, doc_id, doc_id, int(deleting), doc_id],
    )


def lock_slots(rows: list[dict]) -> list[dict]:
    store = get_store()
    fresh = []
    for row in sorted(rows, key=lambda r: (r['sequence_id'], r['id'])):
        store._execute(completion._sql(store, 'serialize_slot'), [row['sequence_id'], row['id']])
        current = store._fetch_one(
            'SELECT * FROM workflow_sequence_items WHERE sequence_id = ? AND id = ?',
            [row['sequence_id'], row['id']],
        )
        if current is not None:
            fresh.append(current)
    return fresh


def invalidate_rows(rows: list[dict], reason: str) -> None:
    for row in rows:
        if not completion.invalidate_slot(row['sequence_id'], row['id'],
                                         expected_generation=row['slot_generation'], reason=reason):
            raise completion.CompletionConflict('completion_invalidation_conflict')


def _meta_evidence(value):
    try:
        meta = json.loads(value) if isinstance(value, str) else value or {}
    except (TypeError, ValueError):
        return value
    if not isinstance(meta, dict):
        return meta
    return {k: v for k, v in meta.items() if k in {
        'content_sha256', 'test_spec', 'basis', 'basis_id', 'baseline_fingerprint',
        'edit_spec', 'spec_hash', 'result', 'overall', 'content_fingerprint',
        'test_basis', 'superseded_test_basis', 'test_contract_version',
    }}


def document_reason(before: dict, updates: dict) -> str | None:
    if 'doc_review_status' in updates and updates['doc_review_status'] in {
        'pending_review', 'revised', 'rejected', 'wf_in_progress',
    }:
        # Explicit reopening/re-review is an event even at the same revision/status.
        return 'document_reopened'
    for key in ('revision_no', 'type_code', 'target_id', 'triggered_by', 'group_id', 'project_id'):
        if key in updates and updates[key] != before.get(key):
            return 'document_' + key + '_changed'
    if 'status' in updates and updates['status'] != before.get('status'):
        return 'document_status_changed'
    if 'meta' in updates and _meta_evidence(updates['meta']) != _meta_evidence(before.get('meta')):
        return 'document_evidence_changed'
    return None


def apply_document_update(doc_id: str, updates: dict, sql: str, params: list) -> int:
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = lock_slots(document_slots(doc_id))
        member_ids = {r['id'] for r in document_slots(doc_id)}
        rows = [r for r in rows if r['id'] in member_ids]
        before = store._fetch_one('SELECT * FROM documents WHERE doc_id = ?', [doc_id])
        affected = store._execute_affected(sql, params)
        reason = document_reason(before, updates) if before is not None else None
        if affected == 1 and reason:
            invalidate_rows(rows, reason)
        return affected


@contextmanager
def document_deletion(doc_id: str):
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = lock_slots(document_slots(doc_id, deleting=True))
        invalidate_rows(rows, 'document_deleted')
        yield


def invalidate_test(doc_id: str, reason: str) -> None:
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = [r for r in document_slots(doc_id) if r['type'] == 'TSR']
        invalidate_rows(lock_slots(rows), reason)


def reopen_sequence(root_doc_id: str, target_seq: int) -> None:
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = store._fetch_all(
            'SELECT s.*, d.seq AS result_seq FROM workflow_sequence_items s '
            'JOIN workflow_sequences q ON q.id = s.sequence_id '
            'LEFT JOIN documents d ON d.doc_id = s.result_doc_id WHERE q.doc_id = ? '
            'ORDER BY s.sort_order', [root_doc_id])
        affected = [r for r in rows if r.get('result_seq') is not None and r['result_seq'] >= target_seq]
        if affected:
            first = min(r['sort_order'] for r in affected)
            invalidate_rows(lock_slots([r for r in rows if r['sort_order'] >= first]), 'workflow_reopen')



def _run_evidence(row: dict | None):
    if row is None:
        return None
    out = {k: row.get(k) for k in (
        'doc_id', 'revision_no', 'tsr_doc_id', 'status', 'overall',
        'case_passed', 'case_failed', 'error', 'result_meta',
    )}
    try:
        meta = json.loads(out['result_meta']) if out['result_meta'] else {}
        if isinstance(meta, dict):
            meta.pop('chain', None)  # handoff bookkeeping is not result evidence
        out['result_meta'] = meta
    except (TypeError, ValueError):
        pass
    return out


def test_evidence_mutation(kind: str):
    """Wrap actual run/Case writes, not their service callers or display reads.

    Guarded terminal writes that lose their CAS and exact evidence replays leave
    the generation unchanged. All exceptions roll back both evidence and history.
    """
    def decorate(func):
        signature = inspect.signature(func)
        @wraps(func)
        def wrapped(*args, **kwargs):
            values = signature.bind(*args, **kwargs).arguments
            store = get_store()
            with store.transaction():
                _write_intent(store)
                case_id = values.get('case_id', values.get('case_row_id'))
                run_id = values.get('run_id')
                case_before = None
                if kind == 'case':
                    case_before = store._fetch_one('SELECT * FROM test_run_cases WHERE id = ?', [case_id])
                    run_id = (case_before or {}).get('run_id')
                before = store._fetch_one('SELECT * FROM test_runs WHERE run_id = ?', [run_id]) if run_id else None
                doc_ids = {values.get('doc_id'), values.get('tsr_doc_id'),
                           (before or {}).get('doc_id'), (before or {}).get('tsr_doc_id')} - {None}
                rows = {}
                for doc_id in sorted(doc_ids):
                    for row in document_slots(doc_id):
                        if row['type'] == 'TSR':
                            rows[row['id']] = row
                locked = lock_slots(list(rows.values()))
                # Fresh evidence after slot serialization, before the actual write.
                if run_id:
                    before = store._fetch_one('SELECT * FROM test_runs WHERE run_id = ?', [run_id])
                if case_id is not None:
                    case_before = store._fetch_one('SELECT * FROM test_run_cases WHERE id = ?', [case_id])
                result = func(*args, **kwargs)
                if kind == 'admission':
                    changed = result is not None
                    reason = 'test_run_created'
                elif kind == 'case':
                    after = store._fetch_one('SELECT * FROM test_run_cases WHERE id = ?', [case_id])
                    changed = ({k: v for k, v in (case_before or {}).items() if k != 'finished_at'}
                               != {k: v for k, v in (after or {}).items() if k != 'finished_at'})
                    reason = 'tsr_case_changed'
                else:
                    after = store._fetch_one('SELECT * FROM test_runs WHERE run_id = ?', [run_id])
                    changed = _run_evidence(before) != _run_evidence(after)
                    reason = 'tsr_result_or_basis_changed'
                if changed:
                    invalidate_rows(locked, reason)
                return result
        return wrapped
    return decorate


def observe_basis(doc_id: str, basis_token: str) -> None:
    """Remember measurements durably so measured Basis A -> B -> A cannot revive A.

    Only decision/measurement paths call this; memo/display paths never reopen.
    The token also represents an unverifiable/missing source, which closes any
    prior verified completion until a new explicit decision is made.
    """
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = lock_slots([r for r in document_slots(doc_id) if r['type'] == 'TSR'])
        store._execute('UPDATE documents SET doc_id = doc_id WHERE doc_id = ?', [doc_id])
        old = store._fetch_one('SELECT basis_token FROM workflow_completion_basis WHERE doc_id = ?', [doc_id])
        if old is not None and old['basis_token'] == basis_token:
            return
        # An older decision without an observation is unverifiable, not inherited.
        if old is not None or any(r.get('slot_decision_id') for r in rows):
            invalidate_rows(rows, 'tsr_basis_changed')
        if old is None:
            store._execute('INSERT INTO workflow_completion_basis (doc_id, basis_token) VALUES (?, ?)',
                           [doc_id, basis_token])
        else:
            store._execute('UPDATE workflow_completion_basis SET basis_token = ? WHERE doc_id = ?',
                           [basis_token, doc_id])



@contextmanager
def source_mutation(project_id: str, group_id: str, root):
    """Observe actual source bytes under the caller's G lock.

    With no completion decision there is no fingerprint cost. Files excluded by
    the existing Basis policy do not reopen a report. Filesystem writes are not
    DB writes: even a partially failed mutation invalidates surviving decisions
    before its original exception is re-raised.
    """
    from modules.flow_gate.services import source_fingerprint
    store = get_store()
    error = None
    with store.transaction():
        _write_intent(store)
        rows = store._fetch_all(
            "SELECT s.* FROM workflow_sequence_items s JOIN documents d ON d.doc_id = s.result_doc_id "
            "WHERE s.type = 'TSR' AND s.slot_decision_id IS NOT NULL "
            "AND d.project_id = ? AND d.group_id = ? ORDER BY s.sequence_id, s.id",
            [project_id, group_id])
        rows = lock_slots(rows)
        def fingerprint():
            try:
                return source_fingerprint.measure(root)['content_fingerprint']
            except source_fingerprint.SourceFingerprintError:
                return None
        before = fingerprint() if rows else None
        try:
            yield
        except BaseException as exc:
            error = (exc, exc.__traceback__)
        if rows:
            after = fingerprint()
            if before is None or after is None or before != after:
                invalidate_rows(rows, 'tsr_source_basis_changed')
    if error is not None:
        raise error[0].with_traceback(error[1])



@contextmanager
def pending_slot_deletion(sequence_id: int, keep_ids=()):
    store = get_store()
    with store.transaction():
        _write_intent(store)
        rows = store._fetch_all('SELECT * FROM workflow_sequence_items WHERE sequence_id = ? '
                                'AND result_doc_id IS NULL ORDER BY id', [sequence_id])
        keep = set(keep_ids)
        invalidate_rows(lock_slots([r for r in rows if r['id'] not in keep]), 'slot_deleted')
        yield
