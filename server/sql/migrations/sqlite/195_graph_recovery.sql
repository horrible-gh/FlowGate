-- 195: explicit recovery requests and immutable source expectations.
CREATE TABLE IF NOT EXISTS graph_recovery_requests (
    group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    previous_run_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (group_id, request_id)
);
CREATE TABLE IF NOT EXISTS graph_recovery_inputs (
    owner_id TEXT NOT NULL PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    input_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_graph_recovery_group_deleted AFTER DELETE ON groups
BEGIN
    DELETE FROM graph_recovery_requests WHERE group_id = OLD.group_id;
    DELETE FROM graph_recovery_inputs WHERE group_id = OLD.group_id;
END;
