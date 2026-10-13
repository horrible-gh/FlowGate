-- 150_graph_node_memos_body_mediumtext.sql — flowgate.v02.0056 T#3 (v0.2 card memos) (MySQL/MariaDB)
-- A card memo body holds up to 20,000 characters (card_memos.memos.BODY_MAX_LEN): up to 80,000 bytes in utf8mb4,
-- over TEXT's 65,535, so the longest valid body fails (strict mode) or is cut. 149 now creates body as
-- MEDIUMTEXT; this widens a graph_node_memos that 149 built while body was TEXT (re-running it on MEDIUMTEXT
-- changes nothing). SQLite/PostgreSQL TEXT has no such ceiling.
ALTER TABLE graph_node_memos MODIFY COLUMN body MEDIUMTEXT NOT NULL;
