-- 203_question_loop_waits.sql
-- flowgate.v02test.0051 T0005 (A1, NR 0003 §2.1/§2.4): a document review loop that reaches a hop boundary while
-- questions its own AI run asked are still unanswered is parked here instead of starting its next hop. The worker
-- returns its run and the group lease; the card responder answers; once every question is answered the loop goes
-- on from the stored stage exactly once (a new execution of the same attempt / assignment, CAS on status).
--
-- wait_id               qlw_<hex>
-- loop_run_id           the AI run whose loop parked (unique: a parked run never runs another hop)
-- graph_exec_id         its ended execution; graph_attempt_id / graph_run_id / node_key the attempt it worked for
-- assignment_stage/seq  the review assignment the execution belonged to (NULL: none / pre-feature)
-- asked_hop_kind        the hop that ended with the questions open (review | rework)
-- asker_inv_id          the invocation whose actual AI asked (NULL: provenance unknown)
-- asked_round_no        the loop round of that hop
-- hop_outcome           that hop checkpoint outcome (succeeded | failed); a hop that only asked is not a failure
-- resume_stage/round_no/attempts_used  where the loop goes on (the checkpoint reserved successor stage)
-- question_item_ids     JSON array of question_items.id the hop left open
-- status                waiting -> resuming (successor requested: resume_exec_id) -> resumed (its run admitted:
--                       resumed_run_id) | cancelled (attempt gone; end_reason)
-- waited_at             wait start; answered_at when every question was found answered (the resume claim);
-- ended_at              resumed / cancelled. A2 reads waited_at..answered_at as the time excluded from the loop budget.
CREATE TABLE IF NOT EXISTS question_loop_waits (
    wait_id VARCHAR(64) NOT NULL PRIMARY KEY,
    group_id TEXT NOT NULL,
    doc_ref TEXT NOT NULL,
    loop_run_id TEXT NOT NULL,
    graph_exec_id TEXT,
    graph_attempt_id TEXT,
    graph_run_id TEXT,
    node_key TEXT,
    assignment_stage TEXT,
    assignment_seq INTEGER,
    asked_hop_kind TEXT NOT NULL CHECK (asked_hop_kind IN ('review', 'rework')),
    asker_inv_id TEXT,
    asked_round_no INTEGER NOT NULL,
    hop_outcome TEXT CHECK (hop_outcome IS NULL OR hop_outcome IN ('succeeded', 'failed')),
    resume_stage TEXT NOT NULL CHECK (resume_stage IN ('review', 'rework')),
    resume_round_no INTEGER NOT NULL,
    resume_attempts_used INTEGER NOT NULL DEFAULT 0,
    question_item_ids TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('waiting', 'resuming', 'resumed', 'cancelled')),
    waited_at TEXT NOT NULL,
    answered_at TEXT,
    resume_requested_at TEXT,
    resume_exec_id TEXT,
    resumed_at TEXT,
    resumed_run_id TEXT,
    ended_at TEXT,
    end_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_question_loop_waits_loop_run ON question_loop_waits (loop_run_id);
CREATE INDEX IF NOT EXISTS idx_question_loop_waits_group_status ON question_loop_waits (group_id, status);
CREATE INDEX IF NOT EXISTS idx_question_loop_waits_resume_exec ON question_loop_waits (resume_exec_id);
