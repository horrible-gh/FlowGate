-- 073 — remote_tool_op_log.op CHECK expansion for patch/stat (group 0347: P0004 §12.2 / L0005 DEFERRED / DB0006).
-- SQLite cannot ALTER a CHECK constraint; the table must be recreated (cf. 027, 042).

PRAGMA foreign_keys = OFF;
BEGIN;

ALTER TABLE remote_tool_op_log RENAME TO remote_tool_op_log_before_patch_stat;

CREATE TABLE remote_tool_op_log (
  log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  grant_id        TEXT NOT NULL
                    REFERENCES remote_tool_grant(grant_id) ON DELETE RESTRICT,
  op              TEXT NOT NULL
                    CHECK (op IN ('read', 'write', 'grep', 'glob', 'remove', 'patch', 'stat')),
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
  CHECK ((result =  'success' AND error_code IS NULL) OR
         (result <> 'success' AND error_code IS NOT NULL)),
  CHECK (result = 'success'
         OR (result = 'denied'      AND error_code = 'forbidden')
         OR (result = 'error'       AND error_code = 'invalid_request')
         OR (result = 'not_found'   AND error_code = 'not_found')
         OR (result = 'conflict'    AND error_code = 'conflict')
         OR (result = 'too_large'   AND error_code = 'too_large')
         OR (result = 'unavailable' AND error_code = 'unavailable'))
);

INSERT INTO remote_tool_op_log
    (log_id, grant_id, op, target_path, target_pattern, result, error_code,
     bytes_processed, occurred_at, created_at)
SELECT
    log_id, grant_id, op, target_path, target_pattern, result, error_code,
    bytes_processed, occurred_at, created_at
FROM remote_tool_op_log_before_patch_stat;

DROP TABLE remote_tool_op_log_before_patch_stat;

CREATE INDEX IF NOT EXISTS idx_oplog_grant_time ON remote_tool_op_log (grant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_oplog_time       ON remote_tool_op_log (occurred_at);
CREATE INDEX IF NOT EXISTS idx_oplog_result     ON remote_tool_op_log (result);

COMMIT;
PRAGMA foreign_keys = ON;
