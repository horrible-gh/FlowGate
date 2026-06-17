-- 041 — Project-control remote tool storage (group 0003: R0001 / DB0007).
--
-- Three tables per DB0007:
--   remote_tool_grant        permission grant (1 token = 1 delegation)
--   remote_tool_grant_scope  selected permission scope set (read/write/grep/remove)
--   remote_tool_op_log       operation history (1 request = 1 row)
--
-- enum CHECKs mirror P0005 §6 / L0006 §5.1·§8 / DB0007 §7. The success⇔NULL
-- bijection AND the non-success result↔error_code 1:1 mapping (DB0007 §7.5) are
-- both enforced by portable CHECK constraints on the table — see the two CHECKs
-- at the end of remote_tool_op_log. (Earlier revisions used a BEFORE INSERT
-- trigger with WHEN/RAISE for the value mapping; that is SQLite-only procedural
-- SQL that does not translate to MariaDB/PostgreSQL — B0091, group 0091 — and
-- the mapping is a static per-value constraint a single CHECK expresses exactly.)

BEGIN;

CREATE TABLE IF NOT EXISTS remote_tool_grant (
  grant_id      TEXT PRIMARY KEY,
  token_hash    TEXT NOT NULL UNIQUE,
  project       TEXT NOT NULL,
  module        TEXT NOT NULL,
  report_doc_id TEXT,
  session_id    TEXT,
  status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'revoked', 'expired')),
  issued_at     TEXT NOT NULL,
  expires_at    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_tool_grant_scope (
  grant_id TEXT NOT NULL
             REFERENCES remote_tool_grant(grant_id) ON DELETE CASCADE,
  scope    TEXT NOT NULL CHECK (scope IN ('read', 'write', 'grep', 'remove')),
  PRIMARY KEY (grant_id, scope)
);

CREATE TABLE IF NOT EXISTS remote_tool_op_log (
  log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  grant_id        TEXT NOT NULL
                    REFERENCES remote_tool_grant(grant_id) ON DELETE RESTRICT,
  op              TEXT NOT NULL
                    CHECK (op IN ('read', 'write', 'grep', 'glob', 'remove')),
  target_path     TEXT,
  target_pattern  TEXT,
  result          TEXT NOT NULL
                    CHECK (result IN ('success', 'denied', 'not_found',
                                      'conflict', 'too_large', 'error', 'unavailable')),
  error_code      TEXT
                    CHECK (error_code IS NULL OR error_code IN
                      ('forbidden', 'not_found', 'conflict',
                       'too_large', 'invalid_request', 'unavailable')),
  bytes_processed INTEGER CHECK (bytes_processed IS NULL OR bytes_processed >= 0),
  occurred_at     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  -- (1) success ⇔ error_code IS NULL bijection (DB0007 §7.5).
  CHECK ((result =  'success' AND error_code IS NULL) OR
         (result <> 'success' AND error_code IS NOT NULL)),
  -- (2) non-success result ↔ error_code 1:1 value mapping (DB0007 §7.5). This
  -- mirrors remote_tool_service.RESULT_BY_STATUS / ERROR_CODE_BY_STATUS exactly.
  -- Portable across SQLite/MariaDB/PostgreSQL (no trigger needed).
  CHECK (result = 'success'
         OR (result = 'denied'      AND error_code = 'forbidden')
         OR (result = 'error'       AND error_code = 'invalid_request')
         OR (result = 'not_found'   AND error_code = 'not_found')
         OR (result = 'conflict'    AND error_code = 'conflict')
         OR (result = 'too_large'   AND error_code = 'too_large')
         OR (result = 'unavailable' AND error_code = 'unavailable'))
);

CREATE INDEX IF NOT EXISTS idx_oplog_grant_time ON remote_tool_op_log (grant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_oplog_time       ON remote_tool_op_log (occurred_at);
CREATE INDEX IF NOT EXISTS idx_oplog_result     ON remote_tool_op_log (result);

COMMIT;
