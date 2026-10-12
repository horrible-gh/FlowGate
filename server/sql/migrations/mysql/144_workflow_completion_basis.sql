-- 0428 T#3: durable measured Basis identity, independent of document revision.
-- No FK cascade: evidence observations are not restored by a document rewind.
CREATE TABLE workflow_completion_basis (
    doc_id VARCHAR(191) PRIMARY KEY,
    basis_token VARCHAR(512) NOT NULL
);
