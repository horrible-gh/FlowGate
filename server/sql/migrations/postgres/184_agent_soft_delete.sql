-- flowgate.v02test.0019: an Agent removed from the list keeps its row, ID and history
-- (credentials, enrollments, jobs). NULL = active; set = deleted at that time.
ALTER TABLE agents ADD COLUMN deleted_at TEXT;
