-- 043_tokens_dry_run_count.sql
-- Add a per-token dry-run attempt counter for Inbox POST dry-run (R0001, group 0050).
-- Basis: NR0003 §5.4, D0005 §6, L0007 §6.2/§7, DB0008 §2.
--
-- DB0008 §2 specified file 041, but the dev branch has since taken 041/042 (×2);
-- this lands as 043 to preserve the "filename order + apply once" convention (DB0008 §1).
-- Pure ADD COLUMN (like 016 scratch_dir); no table rebuild needed for an additive column.
-- NOT NULL DEFAULT 0 backfills every existing token row to 0 automatically (no UPDATE).

ALTER TABLE tokens ADD COLUMN dry_run_count INTEGER NOT NULL DEFAULT 0;
