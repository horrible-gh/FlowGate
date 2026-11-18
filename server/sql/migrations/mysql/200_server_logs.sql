-- 200_server_logs.sql  (mysql)
-- flowgate.v02test.0050 T0005: every server log record of WARNING and above, in one table. See the sqlite file.
-- MySQL has no CREATE INDEX IF NOT EXISTS, so the indexes are declared inside CREATE TABLE IF NOT EXISTS
-- (same names and columns as the other dialects).

CREATE TABLE IF NOT EXISTS server_logs (
    id             INTEGER      NOT NULL AUTO_INCREMENT,
    first_seen_at  VARCHAR(40)  NOT NULL,
    last_seen_at   VARCHAR(40)  NOT NULL,
    level          VARCHAR(16)  NOT NULL,
    logger_name    VARCHAR(191) NOT NULL,
    location       VARCHAR(512) NOT NULL,
    func_name      VARCHAR(191) NULL,
    message        TEXT         NOT NULL,
    exc_type       VARCHAR(191) NULL,
    exc_text       TEXT         NULL,
    request_id     VARCHAR(64)  NULL,
    source         VARCHAR(16)  NOT NULL DEFAULT 'server',
    repeat_count   INTEGER      NOT NULL DEFAULT 1,
    fingerprint    VARCHAR(64)  NOT NULL,
    extra_json     TEXT         NULL,
    PRIMARY KEY (id),
    KEY idx_server_logs_fingerprint (fingerprint, first_seen_at),
    KEY idx_server_logs_last_seen (last_seen_at),
    KEY idx_server_logs_level_seen (level, last_seen_at),
    CONSTRAINT ck_server_logs_repeat_count CHECK (repeat_count >= 1)
) ROW_FORMAT=DYNAMIC;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS server_logs;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
