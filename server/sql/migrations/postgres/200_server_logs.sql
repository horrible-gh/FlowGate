-- 200_server_logs.sql  (postgres)
-- flowgate.v02test.0050 T0005: every server log record of WARNING and above, in one table. See the sqlite file.

CREATE TABLE IF NOT EXISTS server_logs (
    id             SERIAL  PRIMARY KEY,
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

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS server_logs;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
