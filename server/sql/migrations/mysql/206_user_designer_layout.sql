-- 206_user_designer_layout.sql
-- Saved dock sizes: one row per user for the Designer's three dock sizes --
-- left panel width, right panel width, bottom panel height, in px. Read and written through
-- GET/PATCH /api/v1/me/ui-settings (designer_left_width / designer_right_width / designer_bottom_height).
--
-- A table of its own rather than three columns on user_ui_settings (089), for the reason 089 gives for not
-- joining the chat table: that row's existence is the answer to "has this person ever chosen a finished-card
-- retention" (is_default). Dragging a panel must not create that row and flip the answer.
--
-- No schema default and no range CHECK on purpose: the defaults (280 / 300 / 320) and the accepted range live
-- in services/ui_settings_service.py only. A stored value outside the range reads back as the default.
--
-- Additive only, and reversible: remove the table, then delete its row from the migrations ledger.

CREATE TABLE IF NOT EXISTS user_designer_layout (
    user_id       VARCHAR(191) NOT NULL PRIMARY KEY
                           REFERENCES users(user_id) ON DELETE CASCADE,
    left_width    INTEGER NOT NULL,
    right_width   INTEGER NOT NULL,
    bottom_height INTEGER NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
