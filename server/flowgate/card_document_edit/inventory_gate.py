"""Batch path preparation for legacy NONE writers. Every target is checked before any mutation."""
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
from . import write_gate as gate
from modules.flow_gate.storage import files


def migration_plan(old, group_id, entries, dest_base):
    parsed = old.linter.parse_group_id(group_id)
    if not parsed:
        return []
    reserved, result, pending_counts, consumed = set(), [], {}, set()
    for entry in entries:
        filename, typ = entry['filename'], entry['doc_type']
        directory = os.path.join(dest_base, parsed['project'], parsed['module'], typ)
        seq = old._get_migration_type_seq(group_id, typ, directory) + pending_counts.get(directory, 0)
        prefixed = old._build_migration_filename(group_id, parsed['module'], typ, seq, filename)
        name = old._resolve_prefix_filename(directory, prefixed)
        destination = Path(directory) / name
        if destination in reserved:
            base = Path(directory) / prefixed
            stem, suffix = base.stem, base.suffix
            for number in range(1, old.PREFIX_SUFFIX_MAX + 1):
                candidate = destination.with_name(f'{stem}_{number}{suffix}')
                if candidate not in reserved and not candidate.exists():
                    destination = candidate
                    break
            else:
                raise ValueError('migration destination collision')
        source = Path(entry['source_dir']) / filename
        if source.exists() and source not in consumed:
            reserved.add(destination)
            pending_counts[directory] = pending_counts.get(directory, 0) + 1
            consumed.add(source)
        result.append((entry, source, destination))
    return result


def restore_moves(moved):
    errors = []
    for source, destination, identity in reversed(moved):
        try:
            if files.is_same_file_as(destination, identity) and not source.exists():
                shutil.move(destination, source)
        except Exception as exc:
            errors.append({'source': str(source), 'destination': str(destination), 'error': str(exc)})
    return errors


def batch_migrate(group_id, target_files, dest_base, action):
    from modules.flow_gate import process_service as old
    plan = migration_plan(old, group_id, target_files, dest_base)
    results, moved = [], []
    with gate.paths_scope([p for _, src, dst in plan for p in (src, dst)]):
        try:
            for entry, source, destination in plan:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    identity = files.file_identity(source)
                    shutil.move(source, destination)
                    moved.append((source, destination, files.file_identity(destination)))
                results.append({'original': entry['filename'], 'migrated_to': str(destination.absolute()), 'status': action})
            return {'success': True, 'results': results}
        except Exception as exc:
            errors = restore_moves(moved)
            return {'success': False, 'error': str(exc), 'results': results, 'rollback_errors': errors}


def dc_approve(group_id, dc_doc_id, bundle_files):
    from modules.flow_gate import process_service as old
    selected = []
    for doc in old.db.get_documents_by_group_id(group_id):
        if doc['type'] not in old._DC_ARTIFACT_TYPES:
            continue
        filename = old.db.get_created_memo_file(doc['doc_id'])
        directory = old._locate_file(filename) if filename and filename in bundle_files else None
        if directory:
            selected.append({'filename': filename, 'doc_type': doc['type'], 'source_dir': directory, 'doc': doc})
    plan = migration_plan(old, group_id, selected, old.db.ACCEPT_DIR)
    results, moved, statuses = [], [], []
    with gate.paths_scope([p for _, src, dst in plan for p in (src, dst)]):
        try:
            for entry, source, destination in plan:
                doc = entry['doc']
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    identity = files.file_identity(source)
                    shutil.move(source, destination)
                    moved.append((source, destination, files.file_identity(destination)))
                statuses.append((doc['id'], doc.get('status', '')))
                old.db.update_document_status_by_pk(doc['id'], 'accepted')
                old.db.insert_event(doc['doc_id'], 'accepted', memo_file=entry['filename'],
                                    note=f'DC bundle approval (DC: {dc_doc_id})')
                results.append({'original': entry['filename'], 'migrated_to': str(destination.absolute()), 'action': 'accepted'})
            return {'success': True, 'migration': results}
        except Exception as exc:
            errors = restore_moves(moved)
            for pk, state in reversed(statuses):
                try:
                    old.db.update_document_status_by_pk(pk, state)
                except Exception as rollback_exc:
                    errors.append({'document_pk': pk, 'error': str(rollback_exc)})
            return {'success': False, 'error': str(exc), 'migration': results, 'rollback_errors': errors}


@contextmanager
def scope(kind, **data):
    paths, trees, destinations = [], [], {}
    if kind == 'cancel':
        from modules.flow_gate import process_service as old
        doc, action, memo = data['doc'], data['action'], data['memo']
        reserved = set()
        def cancelled(source):
            source = str(source)
            if source in destinations:
                return destinations[source]
            root, name = Path(old.db.CANCELLED_DIR), Path(source).name
            destination = root / name
            for suffix in range(2, old.PREFIX_SUFFIX_MAX + 3):
                if destination not in reserved and not destination.exists():
                    reserved.add(destination)
                    destinations[source] = destination
                    return destination
                destination = root / f'{Path(name).stem}_{suffix}{Path(name).suffix}'
            raise ValueError('cancel destination collision')
        if action in {'resubmit', 'discard'} and memo:
            source = old._find_file_in_accept(memo)
            if source:
                destination = (Path(old.db.INBOX_DIR) / memo if action == 'resubmit' else
                               cancelled(source))
                paths.extend((source, destination))
        if (doc.get('type') or '').upper() == 'T':
            for child in old.db.get_documents_by_target_id(doc['doc_id'], types=('TR',), statuses=('accepted',)):
                name = old.db.get_created_memo_file(child['doc_id'])
                source = old._find_file_in_accept(name) if name else None
                if source:
                    paths.extend((source, cancelled(source)))
    elif kind == 'legacy_apply':
        from modules.flow_gate import service as old
        filename = data['filename']
        actual = old._resolve_duplicate_filename(filename)
        paths.extend((Path(old.db.INBOX_DIR)/filename, Path(old.db.INBOX_DIR)/actual,
                      Path(old.db.PROCESSED_DIR)/actual, Path(old.db.ACCEPT_DIR)/actual,
                      Path(old.db.ERROR_DIR)/filename, Path(old.db.ERROR_DIR)/('_error_'+filename),
                      Path(old.db.CONFLICT_DIR)/filename, Path(old.db.CONFLICT_DIR)/('_conflict_'+filename)))
    elif kind == 'restore_numbering':
        from modules.flow_gate.db.connection import get_store
        for doc in data['snapshot'].get('documents', []):
            current = get_store()._fetch_one('SELECT * FROM documents WHERE doc_id = ?', [doc['doc_id']])
            if current:
                gate.require_none(current)
            gate.require_none(doc)
            paths.extend(p for p, _ in gate.candidates(doc))
    elif kind == 'copy_storage':
        from modules.flow_gate.storage.migration import COPY_SUBDIRS
        source, destination = Path(data['source']), Path(data['destination'])
        for sub in COPY_SUBDIRS:
            directory = source / sub
            if directory.is_dir():
                paths.extend(destination / sub / p.relative_to(directory) for p in directory.rglob('*') if p.is_file())
    else:
        raise ValueError(kind)
    with gate.paths_scope(paths, trees=trees):
        yield destinations


def dispatch(kind, *args, **kwargs):
    return {'batch_migrate': batch_migrate, 'dc_approve': dc_approve, 'scope': scope}[kind](*args, **kwargs)
