-- 194_fk_reference_indexes.sql (postgres)
-- flowgate.v02test.0040 (0001-B): index every column that holds a FOREIGN KEY to documents/groups but is not
-- the leading column of any index (PK/UNIQUE included). A module identifier change rewrites document and group
-- ids in one transaction under defer_foreign_keys; at COMMIT each changed id runs the RI check on every
-- referencing table, and without an index that check is a full scan per id (events.related_doc_id 16 ms,
-- workflow_sequence_items.source_doc_id 3.4 ms, tokens.group_id 1.2 ms x 6,049 documents = ~2 minutes of the
-- 3-minute global lock on 11/17). Plain CREATE INDEX (no CONCURRENTLY): the migrator may run a file inside a
-- transaction. workflow_sequence_items.result_doc_id is not listed: uq_wfseq_items_result_doc (090) leads with it.
-- DOWN: DROP INDEX IF EXISTS idx_ai_invoke_paused_chains_doc_ref, idx_attachment_upload_requests_group_id, idx_document_mention_copies_doc_id, idx_events_related_doc_id, idx_review_dry_run_receipts_group_id, idx_test_runs_tsr_doc_id, idx_tokens_group_id, idx_tr_self_check_runs_group_id, idx_user_chat_source_access_group_id, idx_workflow_return_point_docs_doc_id, idx_workflow_sequence_items_source_doc_id;
CREATE INDEX IF NOT EXISTS idx_ai_invoke_paused_chains_doc_ref ON ai_invoke_paused_chains (doc_ref);
CREATE INDEX IF NOT EXISTS idx_attachment_upload_requests_group_id ON attachment_upload_requests (group_id);
CREATE INDEX IF NOT EXISTS idx_document_mention_copies_doc_id ON document_mention_copies (doc_id);
CREATE INDEX IF NOT EXISTS idx_events_related_doc_id ON events (related_doc_id);
CREATE INDEX IF NOT EXISTS idx_review_dry_run_receipts_group_id ON review_dry_run_receipts (group_id);
CREATE INDEX IF NOT EXISTS idx_test_runs_tsr_doc_id ON test_runs (tsr_doc_id);
CREATE INDEX IF NOT EXISTS idx_tokens_group_id ON tokens (group_id);
CREATE INDEX IF NOT EXISTS idx_tr_self_check_runs_group_id ON tr_self_check_runs (group_id);
CREATE INDEX IF NOT EXISTS idx_user_chat_source_access_group_id ON user_chat_source_access (group_id);
CREATE INDEX IF NOT EXISTS idx_workflow_return_point_docs_doc_id ON workflow_return_point_docs (doc_id);
CREATE INDEX IF NOT EXISTS idx_workflow_sequence_items_source_doc_id ON workflow_sequence_items (source_doc_id);
