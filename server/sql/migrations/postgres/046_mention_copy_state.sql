-- 046_mention_copy_state.sql
-- R0001 (group 0015): persistent "mention copied" header badge (NR0003 rev4, B option).
--
-- A "mention" is the work-instruction block a person copies and pastes to an AI worker
-- to advance the document (edit / review / next-step / ...). The only existing signal was a
-- transient toast, so on re-entry/refresh the user could not tell whether they had already
-- copied this document's mention (R0001). This table persists that fact as SERVER user-state
-- (not localStorage) so the header badge survives reloads/tabs/devices.
--
-- One row per (user, doc): a fresh copy OVERWRITES the previous (UPSERT) because the badge
-- shows only the LAST copied mention (NR0003 §1/§3). NR0005 identified 9 copy sites that map
-- into mention_kind codes; the client renders the localized label from the code.
--
-- NOTE: migration 045 is taken by the in-flight group 0024 work (045_seed_global_design_
-- templates.sql); next free number on this base is 046. The loader globs *.sql in sorted
-- order, so a gap is harmless.

CREATE TABLE IF NOT EXISTS document_mention_copies (
    user_id       TEXT NOT NULL,
    doc_id        TEXT NOT NULL
                      REFERENCES documents(doc_id) ON DELETE CASCADE,
    mention_kind  TEXT NOT NULL,        -- stable code; client maps it to a localized label
    copied_at     TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    PRIMARY KEY (user_id, doc_id)
);