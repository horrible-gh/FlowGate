-- 066_question_options.sql
-- Extend Q options (flowgate.default.0243: R0001 -> D0006 -> DB0007 -> L0008 -> T0009).
-- Two additive JSON-in-TEXT columns:
--   question_items.options   — option array [{"id","label"}]. Display order follows array order.
--   answers.selected_options — selected option ID array. v1 allows zero or one item for single selection;
--                              the array permits multiple selection later without a schema change.
-- No DB CHECK: JSON validation has no syntax common to all three dialects (SQLite json_valid /
-- MySQL / PostgreSQL differ). This follows the unchecked JSON-in-TEXT precedent of 035 findings;
-- validation is enforced by the server's single write entry point (q_service).
-- DEFAULT '[]' automatically backfills existing rows, so no separate backfill UPDATE is needed.

ALTER TABLE question_items ADD COLUMN options TEXT NOT NULL DEFAULT '[]';
ALTER TABLE answers ADD COLUMN selected_options TEXT NOT NULL DEFAULT '[]';
