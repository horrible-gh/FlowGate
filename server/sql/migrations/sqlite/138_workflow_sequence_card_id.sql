-- 138_workflow_sequence_card_id.sql
-- flowgate.default.0649 T#1 (NR0003 §5.4 / O0): which work-plan card a sequence row came from.
-- A WP step's card_id survives renumbering and revisions, so a row can be matched to its card
-- even after cards of the same type overtake each other. Rows poured before this column
-- exist stay NULL; they are classified (linked / retired / unresolved) lazily on the first
-- reflecting write. Retired and acknowledged-unresolved rows store a `retired:` marker,
-- which is longer than a live card id (64); SQLite keeps it as TEXT.
-- Additive only; no index — rows are always read per sequence.

BEGIN;

ALTER TABLE workflow_sequence_items
    ADD COLUMN source_wp_card_id TEXT DEFAULT NULL;

COMMIT;
