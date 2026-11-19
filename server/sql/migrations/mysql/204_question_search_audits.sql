-- B1: includes prechecks which did not create a question. No FK to a rolled-back document.
CREATE TABLE IF NOT EXISTS question_search_audits (
    precheck_id VARCHAR(80) PRIMARY KEY,
    doc_id VARCHAR(255) NOT NULL,
    project_id VARCHAR(255) NOT NULL,
    asker_run_id VARCHAR(255),
    invocation_id VARCHAR(255),
    fingerprint VARCHAR(64) NOT NULL,
    query_text LONGTEXT NOT NULL,
    algorithm_version VARCHAR(80) NOT NULL,
    status VARCHAR(40) NOT NULL,
    found_count INTEGER,
    returned_count INTEGER NOT NULL,
    candidate_answer_ids LONGTEXT NOT NULL,
    reused_answer_id INTEGER,
    rejection_reason LONGTEXT,
    added_item_ids LONGTEXT NOT NULL,
    result_json LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL
);
CREATE INDEX idx_question_search_audits_doc ON question_search_audits (doc_id, created_at);
