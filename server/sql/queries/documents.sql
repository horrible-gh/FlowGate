-- T156: documents category SQL queries
-- FlowGate document CRUD and query operations

-- Get next number for doc_id generation
-- key: get_next_number
-- Usage: SELECT * FROM documents WHERE doc_id LIKE ?
SELECT COUNT(*) as count 
FROM documents 
WHERE doc_id LIKE ?;

-- Get document by doc_id
-- key: get_document_by_id
SELECT * FROM documents 
WHERE doc_id = ?;

-- Get document by primary key (id)
-- key: get_document_by_pk
SELECT * FROM documents 
WHERE id = ?;

-- Get documents by group_id
-- key: get_documents_by_group_id
SELECT * FROM documents 
WHERE group_id = ?
ORDER BY created_at ASC, id ASC;

-- Get documents by target_id
-- key: get_documents_by_target_id
SELECT * FROM documents 
WHERE target_id = ?
ORDER BY created_at ASC, id ASC;

-- Get all documents
-- key: get_all_documents
SELECT * FROM documents 
ORDER BY id DESC;

-- Get documents filtered by multiple criteria
-- key: get_documents_filtered
SELECT * FROM documents 
WHERE 1=1
ORDER BY updated_at DESC, id DESC;

-- Get documents by status and types
-- key: get_documents_by_status_and_types
SELECT * FROM documents 
WHERE status = ?
ORDER BY updated_at ASC, id ASC;

-- Get documents by status
-- key: get_documents_by_status
SELECT * FROM documents 
WHERE status = ?
ORDER BY id DESC;

-- Get open documents
-- key: get_open_documents
SELECT * FROM documents 
WHERE status = 'open'
ORDER BY id DESC;

-- Get outbox documents
-- key: get_outbox_documents
SELECT * FROM documents 
WHERE type IN (?, ?, ?, ?, ?)
ORDER BY created_at DESC, id DESC;

-- Get inbox process documents
-- key: get_inbox_process_documents
SELECT * FROM documents 
WHERE type IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ORDER BY created_at DESC, id DESC;

-- Get recent closed or rejected documents
-- key: get_recently_closed_or_rejected_documents
SELECT * FROM documents 
WHERE status IN ('closed', 'rejected')
ORDER BY updated_at DESC, id DESC
LIMIT ?;

-- Get rejected documents
-- key: get_rejected_documents_with_reasons
SELECT * FROM documents 
WHERE status = 'rejected'
ORDER BY updated_at DESC, id DESC;

-- Get latest T number in group
-- key: get_latest_t_number_in_group
SELECT doc_id FROM documents 
WHERE group_id = ? AND type = 'T';

-- Get latest DS in group
-- key: get_latest_ds_in_group
SELECT * FROM documents 
WHERE group_id = ? AND type = 'DS'
ORDER BY id DESC
LIMIT 1;

-- Get latest D in group
-- key: get_latest_d_in_group
SELECT * FROM documents 
WHERE group_id = ? AND type = 'D'
ORDER BY id DESC
LIMIT 1;

-- Insert document
-- key: insert_document
INSERT INTO documents 
(doc_id, type, project, module, target_id, group_id, owner, priority, due_date, title, status, next, direction, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- Update document status
-- key: update_document_status
UPDATE documents 
SET status = ?, updated_at = ?
WHERE doc_id = ?;

-- Update document status by pk
-- key: update_document_status_by_pk
UPDATE documents 
SET status = ?, updated_at = ?
WHERE id = ?;

-- Update document metadata
-- key: update_document_metadata
UPDATE documents 
SET owner = ?, priority = ?, due_date = ?, updated_at = ?
WHERE doc_id = ?;

-- Update document fields (generic)
-- key: update_document_fields
UPDATE documents 
SET updated_at = ?
WHERE doc_id = ?;

-- Set review required flag
-- key: set_review_required
UPDATE documents 
SET review_required = ?, updated_at = ?
WHERE doc_id = ?;

-- Set superseded_by field
-- key: set_superseded_by
UPDATE documents 
SET superseded_by = ?, updated_at = ?
WHERE doc_id = ?;

-- Set triggered_by field
-- key: set_triggered_by
UPDATE documents 
SET triggered_by = ?, updated_at = ?
WHERE doc_id = ?;

-- Get pending NR/TR documents
-- key: get_pending_nr_tr_documents
SELECT * FROM documents 
WHERE status = 'open' AND type IN ('N', 'T')
ORDER BY id DESC;

-- Get linked result documents (target_id references)
-- key: get_linked_result_documents
SELECT d.doc_id, d.type, d.status, d.title, d.created_at, d.target_id, e.memo_file
FROM documents d
LEFT JOIN events e ON e.doc_id = d.doc_id AND e.event_type = 'created'
WHERE d.type IN ('NR', 'TR')
ORDER BY d.id DESC;

-- Get next outbox sequence
-- key: get_next_outbox_seq
SELECT COUNT(*) as cnt FROM documents 
WHERE group_id = ? AND type = ?;

-- Get documents by status grouped
-- key: get_documents_grouped_by_status
SELECT status, COUNT(*) as count FROM documents 
GROUP BY status;

-- Get active TV for T document
-- key: get_active_tv_for_t
SELECT d.* 
FROM documents d
JOIN tv_status ts ON ts.tv_doc_id = d.doc_id
WHERE d.target_id = ? AND d.type = 'TV'
ORDER BY d.id DESC
LIMIT 1;

-- Get previous active TV
-- key: get_previous_active_tv
SELECT d.* 
FROM documents d
WHERE d.type = 'TV' AND d.target_id = ? AND d.doc_id != ?
  AND (d.superseded_by IS NULL OR d.superseded_by = '')
ORDER BY d.id DESC
LIMIT 1;
