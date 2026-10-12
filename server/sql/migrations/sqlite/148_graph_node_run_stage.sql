-- 148_graph_node_run_stage.sql — flowgate.v02.0056 T#2 (instruction AI, two-stage card run)
-- An AI card with its instruction used runs in two stages (0002-CH turn 5 / 10 O2): its instruction AI writes the
-- card instruction (no review, approved at once; a written instruction is only approved), then its run AI writes
-- the node result with that instruction attached. A node run records the stage it is in and the instruction its
-- result stage was given.
--   stage               instruction | result; NULL for a node no AI starts (a person, a gate) and for the rows
--                       before this migration (they ran one stage: the result). An open key, like status.
--   instruction_doc_id  the instruction document of the card run (documents.doc_id, no FK as result_doc_id).
--   result_event_floor  the last workflow_events id when the instruction stage ended (the move to the result stage,
--                       or the stage's failure): a document the node's tokens registered at or before it was
--                       registered during the instruction stage and is never the node's result. NULL: no boundary.
-- graph_run_token_map:
--   stage               the card run stage a node token belongs to: an AI start's token is mapped with the stage its
--                       AI was started for (instruction | result); the node's dispatch token is mapped NULL and is
--                       marked instruction when a registration with it reaches the result binder during the node's
--                       instruction stage (a token is spent once). A document registered with an instruction token
--                       is never the node's result, whenever its registration is consumed or committed.
-- Additive only; nothing is backfilled. A row opened before this migration keeps stage NULL: it runs the one stage
-- its AI was told then (the card's result type of that time), and the result binder keeps that type for it.
BEGIN;

ALTER TABLE graph_node_runs ADD COLUMN stage TEXT;
ALTER TABLE graph_node_runs ADD COLUMN instruction_doc_id TEXT;
ALTER TABLE graph_node_runs ADD COLUMN result_event_floor INTEGER;
ALTER TABLE graph_run_token_map ADD COLUMN stage TEXT;

COMMIT;
