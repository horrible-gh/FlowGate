-- 087_tr_commit_reapply.sql
-- flowgate.default.0332 T0018 §3-1 (R0001 / D0005 K5·K11 / DB0008 §2-1):
-- the forward restore's half of the time machine. 086 gave a TR approval a commit and a rewind
-- a cancel commit; nothing put the source back when the workflow was restored FORWARD again, so
-- a person who rewound and came back found their documents approved and their tree still
-- reverted. K11 parked that gap for a later group and this is that group.
--
-- One nullable column, and deliberately NOT a new `state` value. A reapply is a NEW `live` row
-- (record_reapply), never a mutation of the canceled row it restores: that row's whole content
-- is "this commit existed and was reverted", and rewriting it is the history edit D0005 K5
-- refuses. Widening the `state` CHECK would also mean a full table rewrite on SQLite, and a
-- rewrite of a table with children is the 042/052 accident (see [[sqlite-rename-rewrites-child-
-- references]] — tr_commit_ledger has no children, but the rule is not worth testing here).
--
-- `restored_from_id` points at the canceled row this live row put back, so the Git status panel
-- can label it as a restored commit instead of showing two indistinguishable live rows for one step. No FK:
-- a self-referencing FK spells three different ways across the dialects and buys nothing —
-- ledger rows are never deleted except by the CASCADE from their document/group (DB0008 §5-1),
-- which takes both ends of the pointer at once.
--
-- Numbering: authored as 086 and moved to 087 with its sibling — 085_tr_commit_ledger.sql
-- became 086_tr_commit_ledger.sql once origin/main merged its own 085, and this file follows it
-- so the pair keeps its order. `migration_renames.RENAMES` carries
-- 086_tr_commit_reapply.sql -> 087_tr_commit_reapply.sql for databases that already applied
-- the old name.
--
-- Additive only. Rollback is `ALTER TABLE tr_commit_ledger DROP COLUMN restored_from_id`.

BEGIN;

ALTER TABLE tr_commit_ledger ADD COLUMN restored_from_id INTEGER;

COMMIT;
