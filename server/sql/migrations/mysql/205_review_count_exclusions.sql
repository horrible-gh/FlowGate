-- 205_review_count_exclusions.sql
-- flowgate.v02test.0051 T0007 (A2, R 0051.0001 A-3 "rejections caused by questions do not count toward the review budget", NR 0003 §2.4):
-- a document review loop's review verdict that was caused by a question wait is recorded here and left out of the
-- loop's review_count only (review._document_loop_counted_rounds). The review row itself is never changed or
-- deleted: it stays the verdict of its round (rejection, rework, stall check, history). Insert-only.
--
-- exclusion_id      rce_<hex>
-- review_id         document_reviews.id of the excluded verdict (unique: one cause per review)
-- doc_ref           the reviewed document; review_run_id / revision_no copied from the review for reading
-- cause             question_wait (the only cause)
-- wait_id           question_loop_waits.wait_id the verdict belongs to (migration 200)
-- basis             asked_with_verdict  -- the reviewer asked and rejected in the hop that parked, and every
--                                          finding of that verdict cites one of its questions (or it has none)
--                   answer_contradicted -- the first review after a rework that submitted while asking, and every
--                                          finding cites one of those (now answered) questions
-- question_item_ids JSON array of the wait's question_items.id the verdict cites
CREATE TABLE IF NOT EXISTS review_count_exclusions (
    exclusion_id VARCHAR(64) NOT NULL PRIMARY KEY,
    review_id INTEGER NOT NULL,
    doc_ref VARCHAR(191) NOT NULL,
    review_run_id VARCHAR(64),
    revision_no INTEGER,
    cause VARCHAR(32) NOT NULL CHECK (cause IN ('question_wait')),
    wait_id VARCHAR(64) NOT NULL,
    basis VARCHAR(32) NOT NULL CHECK (basis IN ('asked_with_verdict', 'answer_contradicted')),
    question_item_ids TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_review_count_exclusions_review (review_id),
    KEY idx_review_count_exclusions_doc (doc_ref)
);
