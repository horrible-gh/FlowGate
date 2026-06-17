-- T158: tv_scenarios category SQL queries
-- TV scenario CRUD and query operations

-- Get TV scenarios by tv_doc_id
-- key: get_tv_scenarios
SELECT * FROM tv_scenarios 
WHERE tv_doc_id = ?
ORDER BY scenario_idx ASC;

-- Insert TV scenario
-- key: insert_tv_scenario
INSERT INTO tv_scenarios
    (tv_doc_id, scenario_idx, source, title, result, note, disabled, disabled_reason, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);

-- Update TV scenario result
-- key: update_tv_scenario_result
UPDATE tv_scenarios
SET result = ?, note = ?, updated_at = ?
WHERE tv_doc_id = ? AND scenario_idx = ?;

-- Update TV scenario hold to skip
-- key: update_tv_scenario_hold_to_skip
UPDATE tv_scenarios
SET result = 'skip', note = ?, updated_at = ?
WHERE tv_doc_id = ? AND scenario_idx = ? AND result = 'hold';

-- Disable TV scenario
-- key: disable_tv_scenario
UPDATE tv_scenarios
SET disabled = 1, disabled_reason = ?, updated_at = ?
WHERE tv_doc_id = ? AND scenario_idx = ?;

-- Get max scenario_idx for tv_doc_id
-- key: get_max_scenario_idx
SELECT COALESCE(MAX(scenario_idx), 0) AS max_idx 
FROM tv_scenarios 
WHERE tv_doc_id = ?;
