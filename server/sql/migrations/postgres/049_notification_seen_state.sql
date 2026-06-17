-- 049_notification_seen_state.sql
-- R0001 (group 0045): persistent notification center for document inflow (NR0003, A option + D option).
--
-- R0001: when managing several AI workers, users cannot tell which document arrived when or where,
-- which group needs attention, and they rarely check the overview; notification history is preferable. NR0003
-- diagnosed that the inflow data ALREADY exists in workflow_events (time/group indexed) and the 🔔
-- UI entry point already exists (AppHeader emit + DashboardView dead handler) — the only missing
-- piece is a persistent, always-visible, unread-aware SURFACE. The notification feed itself is read
-- straight from workflow_events (no new collection); this table only persists the lightweight
-- per-user "how far have I read" watermark so the 🔔 can show an "unread N" badge that survives
-- reloads/tabs/devices.
--
-- One row per (user, project): a "mark all read" OVERWRITES last_seen_at (UPSERT). This mirrors the
-- 0015 document_mention_copies user-state pattern (046_mention_copy_state.sql) — server user-state,
-- not localStorage, so the badge is consistent across sessions. unread_count is derived at read time
-- by counting feed items with occurred_at > last_seen_at; nothing per-item is stored.
--
-- NOTE: the loader globs *.sql in sorted order; 046/047/048 are taken (mention-copy / conversation
-- doctype / group events), so the next free number is 049. A gap would be harmless either way.

CREATE TABLE IF NOT EXISTS notification_seen (
    user_id       TEXT NOT NULL,
    project_id    TEXT NOT NULL
                      REFERENCES projects(project_id) ON DELETE CASCADE,
    last_seen_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    PRIMARY KEY (user_id, project_id)
);