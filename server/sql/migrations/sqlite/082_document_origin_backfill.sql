-- 082_document_origin_backfill.sql
-- flowgate.default.0410 T0008 / TR0009 rev2: backfill the two snapshot columns added by 081.
--
-- 081 did not backfill. Only documents created after those columns were added have an author AI;
-- older documents remain NULL and the screen continues to show only the registering account (e.g. test).
-- This explains half of the rejection reporting that nothing had changed.
--
-- Infer no new values. The sole source is ai_invoke_runs.reached_doc_ids:
-- the JSON array of documents actually created by that run. Copy the provider_name saved
-- when the row was finalized, together with that row's run_id.
-- Do not re-query the provider master: renamed or deleted providers retain their name at that time.
--
-- Safety rules:
--   * Only modify rows where both columns are NULL; never overwrite an existing snapshot.
--   * Documents without a run record (human-created documents or unfinalized runs) stay NULL.
--   * Exclude runs with an empty or whitespace-only provider_name; never guess a name.
--   * If several runs report the same document, the earliest finalized run wins (its creating run).
--   * Re-running is idempotent: WHERE selects no rows after the first application.
--
-- LIKE is used to scan JSON arrays because it is the only mechanism shared by all three dialects. Match doc_id
-- only as a double-quoted element. (An underscore in doc_id is a one-character LIKE wildcard,
-- but a false match requires another document differing by only that character to appear in the
-- same run's list.)

UPDATE documents
   SET origin_provider_name = (
           SELECT r.provider_name
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE '%"' || documents.doc_id || '"%'
            ORDER BY r.finished_at ASC, r.run_id ASC
            LIMIT 1
       ),
       origin_ai_run_id = (
           SELECT r.run_id
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE '%"' || documents.doc_id || '"%'
            ORDER BY r.finished_at ASC, r.run_id ASC
            LIMIT 1
       )
 WHERE origin_provider_name IS NULL
   AND origin_ai_run_id IS NULL
   AND EXISTS (
           SELECT 1
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE '%"' || documents.doc_id || '"%'
       );
