-- 154_graph_run_token_map_kind.sql — flowgate.v02.0064 T-B4d (DB0007 §2.2 / §3.1) (PostgreSQL)
-- graph_run_token_map.map_kind: what a node token was mapped as, written by run_store.map_token in the same INSERT
-- (a required argument there): dispatch (node_dispatch.dispatch_document, the node's dispatch token), ai_start
-- (ai_node_starter._map_if_open, an AI start's token: the persistent evidence of a card's first AI run, 0062 L0006
-- 2.3) or attempt (graph_run.execution.map_token, a review / rework / reissue token of an execution attempt).
-- NULL = mapped before this migration (legacy, judged by L0006 L1~L6). No backfill; the mark never changes after the
-- INSERT (mark_token_stage writes stage only). Ships with the run_store.map_token change in one deployment.
-- Additive; rollback is not advised (DB0007 §3.4: dropping it loses the first-run evidence).
ALTER TABLE graph_run_token_map ADD COLUMN map_kind TEXT NULL
    CHECK (map_kind IS NULL OR map_kind IN ('dispatch', 'ai_start', 'attempt'));
