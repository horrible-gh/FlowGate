-- T156: events category SQL queries
-- FlowGate event logging and query operations

-- Insert event
-- key: insert_event
INSERT INTO events 
(doc_id, event_type, memo_file, file_hash, reason, related_doc_id, related_target_id, note, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);

-- Get created memo file
-- key: get_created_memo_file
SELECT memo_file FROM events 
WHERE doc_id = ? AND event_type = 'created' AND memo_file IS NOT NULL AND memo_file != ''
ORDER BY event_id DESC
LIMIT 1;

-- Check if file is processed
-- key: is_file_processed
SELECT 1 FROM events 
WHERE memo_file = ? AND event_type = 'created'
LIMIT 1;

-- Check if hash is processed
-- key: is_hash_processed
SELECT 1 FROM events 
WHERE file_hash = ? AND event_type = 'created'
LIMIT 1;

-- Get events by doc_id
-- key: get_events_by_doc_id
SELECT * FROM events 
WHERE doc_id = ?
ORDER BY event_id DESC;

-- Get recent events by doc_id
-- key: get_recent_events_by_doc_id
SELECT * FROM events 
WHERE doc_id = ?
ORDER BY event_id DESC
LIMIT ?;

-- Get recent events
-- key: get_recent_events
SELECT * FROM events 
ORDER BY event_id DESC
LIMIT ?;

-- Get latest events map
-- key: get_latest_events_map
SELECT e.doc_id, e.event_type, e.note, e.memo_file, e.created_at
FROM events e
INNER JOIN (
    SELECT doc_id, MAX(event_id) AS max_event_id
    FROM events
    WHERE doc_id IN (?)
    GROUP BY doc_id
) latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id;

-- Get conflict events
-- key: get_conflict_events
SELECT * FROM events 
WHERE event_type = 'conflict_detected'
ORDER BY event_id DESC
LIMIT ?;
