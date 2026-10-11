"""Read-only source comparison and atomic conversion to a pending TR2 revision."""
from __future__ import annotations

import base64
import binascii
import copy
import difflib
import hashlib
import json
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.documents import tr2_service as tr2, tr2_precheck
from modules.flow_gate.documents.tr2_apply_adapter import adapter, _decode_preserving
from modules.flow_gate.services import path_exclusion_rules

MAX_FILES = 200
MAX_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 10 * MAX_BYTES


def _invalid(reason):
    tr2._invalid('files', reason)


def _inputs(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        _invalid('file count must be 1..200')
    total = 0
    for entry in files:
        if isinstance(entry, dict) and isinstance(entry.get('content_base64'), str):
            total += len(entry['content_base64'])
    if total > 4 * ((MAX_TOTAL_BYTES + 2) // 3) + 4 * MAX_FILES:
        _invalid('total upload exceeds 10 MiB')


def _safe_path(root, path):
    if any(ord(c) < 32 for c in path):
        raise tr2.Tr2ValidationError('tr2_path_unsafe', 'file')
    parts = path.split('/')
    # Case-insensitive checks cover Windows Git metadata, ADS and device aliases.
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)),
                *(f'lpt{i}' for i in range(1, 10))}
    if any(p.endswith((' ', '.')) or p.split('.')[0].casefold() in reserved
           or p.casefold() == '.git' for p in parts):
        raise tr2.Tr2ValidationError('tr2_path_unsafe', 'file')
    if path_exclusion_rules.is_excluded_path(path):
        raise tr2.Tr2ValidationError('tr2_path_unsafe', 'file')
    target = tr2._target_path(root, path, 'edit')
    cursor = Path(root)
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink() or getattr(cursor, 'is_junction', lambda: False)():
            raise tr2.Tr2ValidationError('tr2_path_unsafe', 'file')
    if not target.parent.is_dir():
        _invalid('target parent directory must exist')
    return target


def _text(raw):
    if len(raw) > MAX_BYTES:
        _invalid('file exceeds 1 MiB')
    try:
        text = _decode_preserving(raw)[0]
    except UnicodeDecodeError:
        _invalid('UTF-8 text required')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        _invalid('binary/control bytes are not supported')
    return text


def _edit(old, new):
    before, after = old.splitlines(keepends=True), new.splitlines(keepends=True)
    changes = [op for op in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
               if op[0] != 'equal']
    if len(changes) != 1:
        _invalid('multiple change hunks require manual review')
    _, a, b, c, d = changes[0]
    for context in range(4):
        left, right = min(a, context), min(len(before) - b, context)
        anchor = ''.join(before[a-left:b+right])
        replacement = ''.join(after[c-left:d+right])
        if anchor.strip() and old.count(anchor) == 1 and old.replace(anchor, replacement, 1) == new:
            return anchor, replacement
    _invalid('unique BEFORE anchor unavailable; manual review required')


def _prepare(doc, body, files, root):
    _inputs(files)
    paths = []
    for entry in files:
        try:
            paths.append(tr2.normalized_target_path(entry) if isinstance(entry, dict) else None)
        except tr2.Tr2ValidationError:
            paths.append(None)
    counts = {}
    for path in paths:
        if path is not None:
            counts[path.casefold()] = counts.get(path.casefold(), 0) + 1
    occupied = {p.casefold() for p in tr2.target_set(body['edit_spec'])}
    working = copy.deepcopy(body)
    results, material = [], []
    total_bytes = 0
    for index, entry in enumerate(files):
        row = {'index': index, 'file': paths[index], 'status': 'review_required',
               'valid': False, 'item': None, 'reason': None}
        try:
            tr2._object(entry, f'files[{index}]', {'file', 'content_base64'}, {'file', 'content_base64'})
            path = paths[index]
            if path is None:
                raise tr2.Tr2ValidationError('tr2_path_unsafe', 'file')
            if counts[path.casefold()] > 1:
                _invalid('duplicate target path')
            encoded = entry['content_base64']
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
                _invalid('file exceeds 1 MiB or content is invalid')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                _invalid('invalid base64')
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_BYTES:
                _invalid('total upload exceeds 10 MiB')
            new = _text(raw)
            target = _safe_path(root, path)
            exists = target.exists()
            if exists and target.stat().st_size > MAX_BYTES:
                _invalid('baseline file exceeds 1 MiB')
            original = target.read_bytes() if exists else None
            material.append([path, None if original is None else hashlib.sha256(original).hexdigest()])
            old = _text(original) if original is not None else None
            if old == new:
                row.update(status='unchanged', reason='UTF-8 BOM and line endings are preserved by approval')
            else:
                if path.casefold() in occupied:
                    _invalid('target already has pending edits; manual review required')
                item = {'id': 'source-' + hashlib.sha256(path.encode()).hexdigest()[:20],
                        'file': path, 'kind': 'edit' if exists else 'create_file',
                        'rationale': 'Compared uploaded source against group worktree', 'confidence': 'high'}
                if exists:
                    item['anchor_old'], item['replacement_new'] = _edit(old, new)
                else:
                    if not new.strip():
                        _invalid('empty CREATE is unsupported')
                    item['content'] = new
                candidate = tr2._append_batch(working, [{'collection': 'edits', 'item': item}])
                candidate = tr2.validate(candidate, doc=doc)
                # Reuse the exact approval dry-apply checks, without backup/write/attempt.
                probe = copy.deepcopy(candidate['edit_spec'])
                probe['termination'] = 'ready_to_apply'
                probe['edits'] = [item]
                evaluation = adapter.evaluate(probe, root)
                if not evaluation['ready']:
                    _invalid('approval dry-apply rejected this change; manual review required')
                working = candidate
                row.update(status='EDIT' if exists else 'CREATE', valid=True, item=item)
        except tr2.Tr2ValidationError as exc:
            row['reason'] = exc.details.get('reason') or exc.code
        except OSError:
            row['reason'] = 'source file cannot be read safely'
        results.append(row)
    # Bound to inputs, revision, all current proposal targets and original raw bytes.
    fingerprint = hashlib.sha256(json.dumps(
        [doc['doc_id'], doc.get('revision_no') or 0, files, material,
         tr2.target_fingerprint(body['edit_spec'], root)], sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    return {'doc_id': doc['doc_id'], 'revision_no': doc.get('revision_no') or 0,
            'preview_fingerprint': fingerprint, 'files': results}


def _doc(doc_id, revision):
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get('type_code') != tr2.TR2_TYPE_CODE:
        raise tr2.Tr2ValidationError('tr2_workflow_conflict', 'doc_id')
    if (doc.get('revision_no') or 0) != revision:
        raise tr2.Tr2ValidationError('tr2_spec_changed', 'expected_revision')
    tr2._assert_mutable(doc)
    return doc


def preview(doc_id, files, *, expected_revision):
    _inputs(files)
    doc = _doc(doc_id, expected_revision)
    with tr2_precheck.source_lock(doc['project_id'], doc['group_id']) as locked:
        with tr2._lock(doc_id):
            doc = _doc(doc_id, expected_revision)
            result = _prepare(doc, tr2.load_current(doc), files, locked.root)
            tr2_precheck.assert_source_lock(locked)
            return result


def save(doc_id, files, selected, preview_fingerprint, *, actor, expected_revision):
    _inputs(files)
    if (not isinstance(selected, list) or not selected or
            any(type(i) is not int or i < 0 or i >= len(files) for i in selected) or
            len(set(selected)) != len(selected)):
        _invalid('select distinct valid file indices')
    doc = _doc(doc_id, expected_revision)
    with tr2_precheck.source_lock(doc['project_id'], doc['group_id']) as locked:
        def build(current):
            fresh = _doc(doc_id, expected_revision)
            result = _prepare(fresh, current, files, locked.root)
            if result['preview_fingerprint'] != preview_fingerprint:
                raise tr2.Tr2ValidationError('tr2_source_drift', 'preview_fingerprint')
            if current.get('baseline_fingerprint') != tr2.target_fingerprint(current['edit_spec'], locked.root):
                raise tr2.Tr2ValidationError('tr2_source_drift', 'baseline_fingerprint')
            rows = [result['files'][i] for i in selected]
            if not all(row['valid'] for row in rows):
                _invalid('selected file requires review')
            tr2_precheck.assert_source_lock(locked)
            return tr2._append_batch(current, [{'collection': 'edits', 'item': row['item']} for row in rows])
        result = tr2._save(doc_id, build, actor=actor, expected_revision=expected_revision,
                           operation='import_sources')
        result['imported_count'] = len(selected)
        return result
