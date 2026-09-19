-- flowgate.default.0583 T0004: one durable verdict per AI review round.
--
-- The round identity is (review_run_id, doc_id, revision_no): the server-owned run that
-- is reviewing, the document, and the revision it reviewed. attempt_no is deliberately
-- NOT part of it (0486 NR0028 F1 -- it names a provider launch, not a round), and
-- doc_id/revision_no alone are NOT the key either, because an explicit [rerun] is a new
-- run over the same revision and must keep being allowed (T0004 section 3.2).
--
-- The PRIMARY KEY is the barrier itself: the claim is inserted inside the same
-- transaction as the review row it protects, so a second registration for one round
-- cannot commit even when two review tokens race.
--
-- Deliberately a separate table rather than a UNIQUE index on document_reviews: existing
-- deployments already carry duplicate review history written before this barrier
-- (T0004 section 2), and an index added to that table could fail to build at boot.
-- Legacy/manual rows (review_run_id NULL) stay outside it entirely.
CREATE TABLE IF NOT EXISTS document_review_round_claims (
 review_run_id TEXT NOT NULL,
 doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
 revision_no INTEGER NOT NULL,
 token_id TEXT,
 claimed_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY (review_run_id, doc_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_review_round_claims_target
 ON document_review_round_claims(doc_id, revision_no);

-- Backfill, so the barrier knows about the rounds that are ALREADY registered.
--
-- A table created empty would only guard rounds registered after this deployment: a
-- (run, doc, revision) whose verdict was written before it has no claim, so a review
-- token issued before the deploy -- or a late submission on an old token -- would find
-- the round unclaimed and add a second durable verdict at exactly the boundary this
-- migration exists to close.
--
-- GROUP BY collapses the pre-barrier duplicate history (T0004 section 2) to one claim per
-- identity, so the backfill itself cannot trip the PRIMARY KEY however many rows a round
-- already holds; the claimed row keeps the FIRST verdict's timestamps, which is the one
-- section 7 calls the round's result. A backfilled claim names no token because
-- document_reviews does not record one. NULL review_run_id rows are not a round and are
-- skipped; so are reviews whose document is gone, which the foreign key would reject.
-- Re-applying this file is harmless (existing claims are kept, not overwritten).
INSERT OR IGNORE INTO document_review_round_claims
 (review_run_id, doc_id, revision_no, token_id, claimed_at, created_at)
SELECT r.review_run_id, r.doc_id, r.revision_no, NULL,
       MIN(r.reviewed_at), MIN(r.created_at)
  FROM document_reviews r
 WHERE r.review_run_id IS NOT NULL
   AND EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = r.doc_id)
 GROUP BY r.review_run_id, r.doc_id, r.revision_no;
