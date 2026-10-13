-- 0061 T#6: Flow process capacity; running executions, not open cards.
ALTER TABLE groups ADD COLUMN graph_execution_limit INTEGER NOT NULL DEFAULT 4 CHECK (graph_execution_limit > 0);
