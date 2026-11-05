"""Legacy TR2 snapshot backfill: backup-only NONE, never an existing-body revision."""
from modules.flow_gate.db import documents, document_revisions
from modules.flow_gate.db.connection import in_transaction, now_iso
from . import boundary, store, write_gate


def tr2_backfill(doc):
    from modules.flow_gate.documents import tr2_service as old
    revision = int(doc.get('revision_no') or 0)
    if revision <= 0 or document_revisions.get_single_by_doc_revision(doc['doc_id'], revision) is not None:
        return
    def stage(ctx):
        ctx.assert_held()
        fresh = documents.get_by_id(doc['doc_id'])
        if not fresh or fresh['revision_no'] != revision:
            return
        if document_revisions.get_single_by_doc_revision(doc['doc_id'], revision) is not None:
            return
        path = old.canonical_path_for_doc(fresh)
        raw = path.read_bytes()
        old._decode_canonical(raw, fresh, loc='document.json')
        destination = old.snapshot_path(fresh, revision)
        # The backup may not overwrite another body, a prior snapshot, or an orphan.
        with write_gate.paths_scope([destination]):
            store.exclusive_snapshot(destination, raw)
            _, created = document_revisions.create_once({
                'doc_id':fresh['doc_id'], 'revision_no':revision,
                'backup_path':old.storage_paths.to_storage_relative(destination, fresh['project_id']),
                'edit_reason':old.ORIGIN_HUMAN, 'linked_doc_id':None,
                'created_by':fresh['owner_id'], 'created_at':now_iso()})
            if not created:
                raise RuntimeError('revision snapshot concurrently created')
    return boundary.enter(doc, boundary.JOIN if in_transaction() else boundary.OWN, stage)
