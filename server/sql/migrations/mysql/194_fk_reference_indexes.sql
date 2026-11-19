-- 194_fk_reference_indexes.sql (mysql)
-- flowgate.v02test.0040 (0001-B): index every column that holds a FOREIGN KEY to documents/groups but is not
-- the leading column of any index. See the postgres file: a module identifier change rewrites document and
-- group ids in one transaction, and each changed id then scans every referencing table without an index.
-- Only six of the eleven postgres indexes are created here. InnoDB already built an index for the five
-- table-level FOREIGN KEYs (it requires one and adds it itself), so creating them again would only duplicate:
--   attachment_upload_requests.group_id (fk_aur_group, 181), review_dry_run_receipts.group_id
--   (fk_review_receipts_group, 112), tr_self_check_runs.group_id (fk_selfcheck_group, 127),
--   user_chat_source_access.group_id (fk_ucsa_group_group, 177), workflow_return_point_docs.doc_id (053).
-- The six below were declared with column-level inline REFERENCES, which InnoDB parses and silently discards
-- (076b note 3, 086a), so neither the FK nor its automatic index exists. They get the index anyway so every
-- column the schema declares as referencing documents/groups is indexed in all three dialects, and a later
-- table-level FK repair (as 086a did) reuses it instead of adding a second one.
-- CREATE INDEX without IF NOT EXISTS: MySQL 8 has none and the filename-keyed ledger applies
-- this file once (076b note 4, 190). No BEGIN/COMMIT: DDL commits implicitly.
-- DOWN: DROP INDEX idx_ai_invoke_paused_chains_doc_ref ON ai_invoke_paused_chains; DROP INDEX idx_document_mention_copies_doc_id ON document_mention_copies; DROP INDEX idx_events_related_doc_id ON events; DROP INDEX idx_test_runs_tsr_doc_id ON test_runs; DROP INDEX idx_tokens_group_id ON tokens; DROP INDEX idx_workflow_sequence_items_source_doc_id ON workflow_sequence_items;
CREATE INDEX idx_ai_invoke_paused_chains_doc_ref ON ai_invoke_paused_chains (doc_ref);
CREATE INDEX idx_document_mention_copies_doc_id ON document_mention_copies (doc_id);
CREATE INDEX idx_events_related_doc_id ON events (related_doc_id);
CREATE INDEX idx_test_runs_tsr_doc_id ON test_runs (tsr_doc_id);
CREATE INDEX idx_tokens_group_id ON tokens (group_id);
CREATE INDEX idx_workflow_sequence_items_source_doc_id ON workflow_sequence_items (source_doc_id);
