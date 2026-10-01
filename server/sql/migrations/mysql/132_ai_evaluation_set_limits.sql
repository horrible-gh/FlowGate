-- 132_ai_evaluation_set_limits.sql
-- flowgate.v02.0017 T0004 §5: an evaluation set now holds up to 100 constraints of up to 4000
-- characters each (instruction up to 4000). ai_evaluation_sets.constraints stores that list as
-- one JSON array; TEXT (64 KiB) cannot hold the largest valid list in utf8mb4, so it becomes
-- MEDIUMTEXT like ai_evaluation_sets.instruction. SQLite/PostgreSQL TEXT has no such ceiling.
ALTER TABLE ai_evaluation_sets MODIFY COLUMN constraints MEDIUMTEXT NULL;
