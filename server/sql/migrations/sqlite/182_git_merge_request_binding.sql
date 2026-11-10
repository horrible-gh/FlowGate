-- 0153 T#5: immutable globally unique request identities. Results remain in the
-- merge-session journal; this key prevents the same UUID targeting another session.
CREATE TABLE IF NOT EXISTS git_merge_request_binding (
    kind VARCHAR(16) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    binding_digest VARCHAR(64) NOT NULL,
    merge_id BIGINT NOT NULL,
    group_id VARCHAR(255),
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (kind, request_id)
);
