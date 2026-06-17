-- T157: groups category SQL queries
-- FlowGate groups CRUD and query operations

-- Get next group ID counter
-- key: issue_group_id
-- Note: Uses id_counter table per D009, not group_sequences
SELECT value FROM id_counter
WHERE counter_key = ?;

-- Get group by group_id
-- key: get_group
SELECT * FROM groups
WHERE group_id = ?;

-- Get all groups
-- key: get_all_groups
SELECT * FROM groups
ORDER BY created_at DESC;

-- Get groups by status
-- key: get_groups_by_status
SELECT * FROM groups
WHERE status = ?
ORDER BY created_at DESC;

-- Insert new group
-- key: insert_group
INSERT INTO groups
(group_id, project, module, title, priority, status, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);

-- Update group status
-- key: update_group_status
UPDATE groups
SET status = ?, closed_at = ?, updated_at = ?
WHERE group_id = ?;

-- Update group updated_at timestamp
-- key: update_group_updated_at
UPDATE groups
SET updated_at = ?
WHERE group_id = ?;

-- Close group
-- key: close_group
UPDATE groups
SET status = ?, closed_at = ?, updated_at = ?
WHERE group_id = ?;
