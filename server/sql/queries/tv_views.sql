-- T158: TV views using documents.status instead of tv_status table
-- These views provide compatibility for querying TV status via documents

-- Get active TVs (Open, Running, Pass, Fail, Reject statuses)
-- key: get_active_tv_for_t
SELECT d.* FROM documents d 
WHERE d.target_id = ? 
  AND d.type = 'TV' 
  AND d.status IN ('open', 'running', 'pass', 'fail', 'reject')
ORDER BY d.id DESC 
LIMIT 1;

-- Get TVs by statuses with TV-specific fields from meta JSON
-- key: get_active_tvs_by_statuses
SELECT d.* FROM documents d 
WHERE d.type = 'TV' 
  AND d.status IN ({placeholders})
ORDER BY d.id DESC;

-- Get TV/TVR chain for T document
-- key: get_tv_tvr_chain_tvs
SELECT d.* FROM documents d 
WHERE d.target_id = ? 
  AND d.type = 'TV'
ORDER BY d.id ASC;

-- Get TVR documents for TV
-- key: get_tv_tvr_chain_tvrs
SELECT * FROM documents 
WHERE target_id = ? 
  AND type = 'TVR'
ORDER BY id ASC;

-- Get running TV in environment
-- key: get_running_tv_in_env
SELECT d.* FROM documents d
WHERE d.project = ? 
  AND d.module = ?
  AND d.type = 'TV'
  AND d.status IN ('open', 'running')
ORDER BY d.id DESC
LIMIT 1;

-- Get previous active TV (excluding current TV)
-- key: get_previous_active_tv
SELECT d.* FROM documents d 
WHERE d.type = 'TV' 
  AND d.target_id = ? 
  AND d.doc_id != ? 
  AND (d.superseded_by IS NULL OR d.superseded_by = '')
ORDER BY d.id DESC 
LIMIT 1;

-- Helper: Extract tv_status from documents.status (for compatibility)
-- key: extract_tv_status
SELECT 
  d.doc_id as tv_doc_id,
  CASE 
    WHEN d.status = 'open' THEN 'Open'
    WHEN d.status = 'running' THEN 'Running'
    WHEN d.status = 'pass' THEN 'Pass'
    WHEN d.status = 'fail' THEN 'Fail'
    WHEN d.status = 'reject' THEN 'Reject'
    ELSE d.status
  END as tv_status,
  COALESCE(json_extract(d.meta, '$.progress_done'), 0) as progress_done,
  COALESCE(json_extract(d.meta, '$.progress_total'), 0) as progress_total
FROM documents d
WHERE d.type = 'TV' AND d.doc_id = ?;
