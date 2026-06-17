-- 049_notification_seen_state.sql
-- R0001 (group 0045): persistent 🔔 notification center for document inflow (NR0003 — A안 + D안).
--
-- R0001: "여러 AI를 관리하다 보면 어떤 문서가 언제·어디에 들어왔는지 알 수가 없다 / 어느 그룹을
-- 확인해야 하는지 일목요연하게 / 개요는 잘 안 본다 / 차라리 알람 히스토리를 넣든가". NR0003
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

BEGIN;

CREATE TABLE IF NOT EXISTS notification_seen (
    user_id       TEXT NOT NULL,
    project_id    TEXT NOT NULL
                      REFERENCES projects(project_id) ON DELETE CASCADE,
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, project_id)
);

COMMIT;
