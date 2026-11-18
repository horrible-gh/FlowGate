-- 200_server_logs.sql — flowgate.v02test.0050 T0005: every server log record of WARNING and above, in one table.
-- Nobody reads logs/default.log; an AI reads this table instead. Written only by
-- modules.flow_gate.services.server_logs (its own writer thread, its own transaction). Additive only.
--   server_logs  one row per distinct record (fingerprint = source + level + logger + location + function + message
--                template); a repeat within 10 minutes of first_seen_at raises repeat_count / last_seen_at instead of
--                adding a row. message / exc_text are redacted (chat_errors rule); exc_text keeps frames as
--                file:line:function only. source is 'server' (logging) or 'browser' (the next card's report).
--                Rows whose last_seen_at is older than 30 days are deleted by the writer once a day.
BEGIN;

CREATE TABLE IF NOT EXISTS server_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen_at  TEXT    NOT NULL,
    last_seen_at   TEXT    NOT NULL,
    level          TEXT    NOT NULL,
    logger_name    TEXT    NOT NULL,
    location       TEXT    NOT NULL,
    func_name      TEXT,
    message        TEXT    NOT NULL,
    exc_type       TEXT,
    exc_text       TEXT,
    request_id     TEXT,
    source         TEXT    NOT NULL DEFAULT 'server',
    repeat_count   INTEGER NOT NULL DEFAULT 1 CHECK (repeat_count >= 1),
    fingerprint    TEXT    NOT NULL,
    extra_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_server_logs_fingerprint ON server_logs(fingerprint, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_server_logs_last_seen   ON server_logs(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_server_logs_level_seen  ON server_logs(level, last_seen_at);

COMMIT;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS server_logs;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
