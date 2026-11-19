-- 200_question_loop_waits.sql
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
    group_id VARCHAR(191) NOT NULL,
    doc_ref VARCHAR(191) NOT NULL,
    loop_run_id VARCHAR(64) NOT NULL,
    graph_exec_id VARCHAR(64),
    graph_attempt_id VARCHAR(64),
    graph_run_id VARCHAR(64),
    node_key VARCHAR(191),
    assignment_stage VARCHAR(32),
    assignment_seq INTEGER,
    asked_hop_kind VARCHAR(16) NOT NULL CHECK (asked_hop_kind IN ('review', 'rework')),
    asker_inv_id VARCHAR(64),
    asked_round_no INTEGER NOT NULL,
    hop_outcome VARCHAR(16) CHECK (hop_outcome IS NULL OR hop_outcome IN ('succeeded', 'failed')),
    resume_stage VARCHAR(16) NOT NULL CHECK (resume_stage IN ('review', 'rework')),
    resume_round_no INTEGER NOT NULL,
    resume_attempts_used INTEGER NOT NULL DEFAULT 0,
    question_item_ids TEXT NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('waiting', 'resuming', 'resumed', 'cancelled')),
    waited_at VARCHAR(40) NOT NULL,
    answered_at VARCHAR(40),
    resume_requested_at VARCHAR(40),
    resume_exec_id VARCHAR(64),
    resumed_at VARCHAR(40),
    resumed_run_id VARCHAR(64),
    ended_at VARCHAR(40),
    end_reason VARCHAR(191),
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_question_loop_waits_loop_run (loop_run_id),
    KEY idx_question_loop_waits_group_status (group_id, status),
    KEY idx_question_loop_waits_resume_exec (resume_exec_id)
);
