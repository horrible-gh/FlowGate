-- 0072: extend existing messages without moving or deleting legacy rows.
ALTER TABLE project_messages ADD COLUMN message_usage VARCHAR(16) NOT NULL DEFAULT 'document';
ALTER TABLE project_messages ADD COLUMN title VARCHAR(12) NOT NULL DEFAULT '';
ALTER TABLE project_messages ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'once';
ALTER TABLE project_messages ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_project_messages_message_usage_order ON project_messages(project, message_usage, sort_order, id);
