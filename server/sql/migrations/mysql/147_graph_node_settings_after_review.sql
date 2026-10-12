-- 147_graph_node_settings_after_review.sql — flowgate.v02.0056 T#1 (card approval flow) (MySQL/MariaDB)
-- The card setting after review (follow / human) is gone; no code reads the stored value any more.
DELETE FROM graph_node_settings WHERE field = 'after_review';
