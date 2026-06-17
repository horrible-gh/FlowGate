-- 027_t487_workflow_review_status.sql
-- T487: add workflow progress status values to the doc_review_status CHECK
-- New values: wf_in_progress (in progress), wf_done (done)
-- SQLite CHECK changes require recreating the table

PRAGMA foreign_keys = OFF;
BEGIN;

-- ── Temporarily remove views that reference documents (before recreating the table) ──
DROP VIEW IF EXISTS v_tv_open;
DROP VIEW IF EXISTS v_tv_progress;

-- ── Recreate the documents table (expand the doc_review_status CHECK) ───────
CREATE TABLE documents_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT    NOT NULL UNIQUE,
    project_id   TEXT    NOT NULL REFERENCES projects(project_id),
    module       TEXT    NOT NULL DEFAULT '__ALL__',
    group_id     TEXT    REFERENCES groups(group_id),
    sub_group_id TEXT    REFERENCES sub_groups(sub_group_id),
    type_code    TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    file_path    TEXT,
    filename     TEXT,
    status       TEXT    NOT NULL DEFAULT 'draft'
                     CHECK (status IN (
                         'draft','open','in_review','approved','rejected',
                         'cancelled','closed','archived','answered'
                     )),
    owner_id     TEXT    REFERENCES users(user_id),
    priority     TEXT,
    due_date     TEXT,
    direction    TEXT,
    review_required INTEGER NOT NULL DEFAULT 0,
    tv_type      TEXT,
    pass_criteria TEXT   DEFAULT 'all',
    worker_tier  TEXT,
    target_id    TEXT,
    triggered_by TEXT,
    superseded_by TEXT,
    previous_tv  TEXT,
    previous_t   TEXT,
    previous_ds  TEXT,
    created_at   TEXT    NOT NULL,
    meta         TEXT,
    updated_at   TEXT    NOT NULL,
    workflow_steps TEXT,
    revision_no  INTEGER NOT NULL DEFAULT 0,
    doc_review_status TEXT
                     CHECK (doc_review_status IN (
                         'pending_review','approved','rejected','revised',
                         'wf_in_progress','wf_done'
                     )),
    rejection_reason TEXT
);

INSERT INTO documents_new
SELECT
    id, doc_id, project_id, module, group_id, sub_group_id,
    type_code, seq, title, file_path, filename, status, owner_id,
    priority, due_date, direction, review_required,
    tv_type, pass_criteria, worker_tier,
    target_id, triggered_by, superseded_by,
    previous_tv, previous_t, previous_ds,
    created_at, meta, updated_at,
    workflow_steps, revision_no,
    doc_review_status, rejection_reason
FROM documents;

DROP TABLE documents;
ALTER TABLE documents_new RENAME TO documents;

-- ── Recreate indexes ─────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_doc_id
    ON documents(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_prj_mod_grp_status
    ON documents(project_id, module, group_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_prj_type_status
    ON documents(project_id, type_code, status);
CREATE INDEX IF NOT EXISTS idx_documents_owner
    ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_documents_sub_group
    ON documents(sub_group_id);
CREATE INDEX IF NOT EXISTS idx_documents_target
    ON documents(target_id, type_code);
CREATE INDEX IF NOT EXISTS idx_documents_updated
    ON documents(updated_at DESC);

-- ── Recreate views ───────────────────────────────────────────────────────────
CREATE VIEW v_tv_progress AS
SELECT
    d.doc_id,
    d.project_id,
    d.module,
    d.group_id,
    d.title,
    d.status,
    d.tv_type,
    d.pass_criteria,
    d.worker_tier,
    json_extract(d.meta, '$.tv_clear_scope')     AS tv_clear_scope,
    json_extract(d.meta, '$.tv_progress')        AS tv_progress_note,
    json_extract(d.meta, '$.tv_baseline_doc_id') AS tv_baseline_doc_id,
    json_extract(d.meta, '$.tv_re_run_reason')   AS tv_re_run_reason,
    COUNT(ts.id)                                  AS scenario_total,
    SUM(CASE WHEN ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_active,
    SUM(CASE WHEN ts.result IS NOT NULL AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_done,
    SUM(CASE WHEN ts.result = 'pass' AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_pass,
    SUM(CASE WHEN ts.result = 'fail' AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_fail,
    d.owner_id,
    d.created_at,
    d.updated_at
FROM documents d
LEFT JOIN tv_scenarios ts ON ts.tv_doc_id = d.doc_id
WHERE d.tv_type IS NOT NULL
GROUP BY d.doc_id;

CREATE VIEW v_tv_open AS
SELECT *
FROM v_tv_progress
WHERE status NOT IN ('approved', 'closed', 'archived');

-- ── Existing data migration ───────────────────────────────────────────────────
-- Documents where the workflow sequence has been determined but doc_review_status is still NULL
-- -> set to wf_in_progress (in progress)
UPDATE documents
SET    doc_review_status = 'wf_in_progress'
WHERE  doc_review_status IS NULL
  AND  EXISTS (
           SELECT 1 FROM workflow_sequences ws
           WHERE  ws.doc_id = documents.doc_id
             AND  ws.head_advanced_at IS NULL
       );

-- Documents where the workflow sequence is complete (head_advanced_at exists) and doc_review_status is NULL
-- -> set to wf_done (done)
UPDATE documents
SET    doc_review_status = 'wf_done'
WHERE  doc_review_status IS NULL
  AND  EXISTS (
           SELECT 1 FROM workflow_sequences ws
           WHERE  ws.doc_id = documents.doc_id
             AND  ws.head_advanced_at IS NOT NULL
       );

COMMIT;
PRAGMA foreign_keys = ON;
