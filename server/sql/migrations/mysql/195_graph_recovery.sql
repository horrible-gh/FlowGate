-- 195: explicit recovery requests and immutable source expectations.
CREATE TABLE IF NOT EXISTS graph_recovery_requests (
    group_id VARCHAR(191) NOT NULL,
    request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    previous_run_id VARCHAR(255) NOT NULL,
    response_json LONGTEXT NOT NULL,
    PRIMARY KEY (group_id, request_id),
    CONSTRAINT fk_recovery_request_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS graph_recovery_inputs (
    owner_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    group_id VARCHAR(191) NOT NULL,
    input_json LONGTEXT NOT NULL,
    CONSTRAINT fk_recovery_input_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
