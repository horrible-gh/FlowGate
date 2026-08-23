-- 089_user_ui_settings.sql
-- flowgate.default.0452 (DB0004): one row per user for the non-chat UI preferences.
-- The first and so far only column is how long a finished AI-run card stays in the
-- header monitor, in minutes (L0003 1-2, RETENTION_FIELD).
--
-- Numbering: authored against a base whose newest migration on all three dialect trees
-- was 088_tr_conflict_session.sql. If a parallel group takes 089 first, this file moves
-- to the next free ordinal instead: the repo rule (see migration_renames.py) is that the
-- later arrival renumbers, never the earlier one. If it was already applied under 089
-- anywhere before that renumbering, add an (old, new) pair to migration_renames.RENAMES
-- so those databases are not asked to run it a second time.
--
-- A new table rather than a column on the 0362 chat table: that table's columns are all
-- NOT NULL with no schema default, so saving retention alone would have to invent chat
-- values and flip that row's "has this user ever saved" invariant (DB0011 2-6) for people
-- who never opened chat settings. Two tables have no such collision.
--
-- The value column carries no schema default on purpose: the one source of truth for the
-- default is RETENTION_DEFAULT_MINUTES = 30 in services/ui_settings_service.py (DB0004 0-2).
--
-- Additive only, and reversible: save the rows, remove the table, then delete its row from
-- the migrations ledger (DB0004 3-5).

BEGIN;

CREATE TABLE IF NOT EXISTS user_ui_settings (
    user_id                            TEXT    NOT NULL PRIMARY KEY
                                                REFERENCES users(user_id) ON DELETE CASCADE,
    ai_finished_card_retention_minutes INTEGER NOT NULL
                                                CHECK (ai_finished_card_retention_minutes IN
                                                    (-1, 0, 30, 60, 120, 180, 360, 720, 1440)),
    created_at                         TEXT    NOT NULL,
    updated_at                         TEXT    NOT NULL
);

COMMIT;
