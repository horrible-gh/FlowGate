-- 0126 T#2: immutable human decision evidence. Additive and repeatable; no history backfill.
CREATE TABLE IF NOT EXISTS graph_card_completion_decisions (
    completion_id VARCHAR(160) NOT NULL PRIMARY KEY,
    decision_id VARCHAR(160) NOT NULL
);
-- Rollback: revert code only; keep audit and completion evidence.
