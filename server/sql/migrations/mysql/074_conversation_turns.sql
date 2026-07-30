-- 074_conversation_turns.sql
-- flowgate.default.0351 T0007: append-only conversation turn storage.

CREATE TABLE IF NOT EXISTS conversation_turns (
    id               INTEGER PRIMARY KEY AUTO_INCREMENT,
    doc_id           VARCHAR(191)    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL CHECK (seq >= 1),
    speaker          TEXT    NOT NULL CHECK (speaker IN ('user', 'ai')),
    participant_key  VARCHAR(191)    NOT NULL,
    display_name     TEXT,
    locale           TEXT    CHECK (locale IS NULL OR locale IN ('ko', 'en', 'ja')),
    body             TEXT    NOT NULL,
    body_hash        TEXT    NOT NULL CHECK (length(body_hash) = 64),
    based_on_seq     INTEGER NOT NULL DEFAULT 0 CHECK (based_on_seq >= 0),
    stale_since_seq  INTEGER CHECK (stale_since_seq IS NULL OR stale_since_seq >= 1),
    source_run_id    VARCHAR(191),
    idempotency_key  TEXT    NOT NULL,
    idempotency_hash VARCHAR(191)    NOT NULL CHECK (length(idempotency_hash) = 64),
    created_at       VARCHAR(191)    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_turns_doc_seq ON conversation_turns(doc_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_turns_idem ON conversation_turns(doc_id, idempotency_hash);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_doc_participant ON conversation_turns(doc_id, participant_key);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_created_at ON conversation_turns(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_run ON conversation_turns(source_run_id);
CREATE TABLE IF NOT EXISTS conversation_participants (
    id               INTEGER PRIMARY KEY AUTO_INCREMENT,
    doc_id           VARCHAR(191)    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    participant_key  VARCHAR(191)    NOT NULL,
    kind             TEXT    NOT NULL CHECK (kind IN ('user', 'ai')),
    display_name     TEXT,
    first_seen_seq   INTEGER NOT NULL DEFAULT 0 CHECK (first_seen_seq >= 0),
    last_read_seq    INTEGER NOT NULL DEFAULT 0 CHECK (last_read_seq >= 0),
    last_viewed_seq  INTEGER NOT NULL DEFAULT 0 CHECK (last_viewed_seq >= 0),
    last_written_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_written_seq >= 0),
    last_seen_at     TEXT    NOT NULL,
    CHECK (last_viewed_seq <= last_read_seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_participants_doc_key ON conversation_participants(doc_id, participant_key);
CREATE TABLE IF NOT EXISTS conversation_docs (
    doc_id           VARCHAR(191) PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    migration_state  VARCHAR(191) NOT NULL DEFAULT ('pending') CHECK (migration_state IN ('pending', 'in_progress', 'migrated', 'failed')),
    intro            TEXT,
    failure_reason   TEXT,
    turns_migrated   INTEGER NOT NULL DEFAULT 0 CHECK (turns_migrated >= 0),
    lock_owner       TEXT,
    lock_acquired_at TEXT,
    migrated_at      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_docs_state ON conversation_docs(migration_state);