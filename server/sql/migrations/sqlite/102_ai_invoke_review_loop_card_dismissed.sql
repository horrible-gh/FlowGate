-- 102_ai_invoke_review_loop_card_dismissed.sql
-- flowgate.default.0529 B0001: the header monitor's [remove from list] on a FINISHED
-- document-review-loop card was a purely local delete. The card itself is rebuilt on
-- every /ai-invoke/active-all by `list_review_loops_by_user`, which joins this table
-- with no notion of "the user already got rid of this one" -- so the removed card came
-- straight back on the next bootstrap and stayed for days (run aiv_20260830_000075,
-- group flowgate.default.0481, still on screen 2026-09-06).
--
-- This column is that missing notion: the ISO timestamp at which the owner removed the
-- CARD. It is deliberately not a delete of the row -- the loop's history (rounds, stop
-- reason, stop detail) stays browsable through GET /ai-invoke/runs and the run detail,
-- exactly as before. Only the bootstrap listing skips it.
--
-- NULL = never removed (every row that predates this migration), which is why the
-- column is additive, nullable and carries no default: an existing card must keep
-- showing until its owner actually removes it.
-- SQLite ADD COLUMN cannot carry a CHECK, same as 095/101.

ALTER TABLE ai_invoke_document_review_loops ADD COLUMN card_dismissed_at TEXT;
