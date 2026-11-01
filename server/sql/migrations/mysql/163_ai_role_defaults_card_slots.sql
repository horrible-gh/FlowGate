-- 163_ai_role_defaults_card_slots.sql — flowgate.v02.0113 DB-1 (account role defaults, card role slots, card init) (MySQL/MariaDB)
-- Additive only; nothing is backfilled. No *provider_id column references ai_providers: a deleted AI keeps its
-- original id and name snapshot here (an AI delete is a hard delete; SET NULL would lose the id, RESTRICT would
-- block the delete). Every CHECK is NULL-safe: a nullable column is compared only after IS NOT NULL in the same
-- AND group, so a wrong combination evaluates to FALSE, never UNKNOWN. No CHECK names an FK column (MySQL refuses
-- a CHECK on a column with a referential action).
--   user_ai_role_defaults  one row per account = setup completed; three role values (assigned / not_used).
--   graph_node_role_slots  one row per (Flow, card, role) = the role's explicit marker. Never deleted except the
--                          WP re-deploy's wp_plan rows (DB-1 §5 I-4).
--   graph_node_role_init   card init state (pending_init -> initialized only). No row = LEGACY card.

CREATE TABLE IF NOT EXISTS user_ai_role_defaults (
    user_id                    VARCHAR(191) NOT NULL PRIMARY KEY,
    instruction_kind           VARCHAR(16) NOT NULL CHECK (instruction_kind IN ('assigned', 'not_used')),
    instruction_provider_id    VARCHAR(191) NULL,
    instruction_provider_name  VARCHAR(100) NULL,
    execution_kind             VARCHAR(16) NOT NULL CHECK (execution_kind IN ('assigned', 'not_used')),
    execution_provider_id      VARCHAR(191) NULL,
    execution_provider_name    VARCHAR(100) NULL,
    review_kind                VARCHAR(16) NOT NULL CHECK (review_kind IN ('assigned', 'not_used')),
    review_provider_id         VARCHAR(191) NULL,
    review_provider_name       VARCHAR(100) NULL,
    setup_completed_at         VARCHAR(40) NOT NULL,
    updated_at                 VARCHAR(40) NOT NULL,
    CHECK ((instruction_kind = 'assigned' AND instruction_provider_id IS NOT NULL AND TRIM(instruction_provider_id) <> '')
        OR (instruction_kind = 'not_used' AND instruction_provider_id IS NULL AND instruction_provider_name IS NULL)),
    CHECK ((execution_kind = 'assigned' AND execution_provider_id IS NOT NULL AND TRIM(execution_provider_id) <> '')
        OR (execution_kind = 'not_used' AND execution_provider_id IS NULL AND execution_provider_name IS NULL)),
    CHECK ((review_kind = 'assigned' AND review_provider_id IS NOT NULL AND TRIM(review_provider_id) <> '')
        OR (review_kind = 'not_used' AND review_provider_id IS NULL AND review_provider_name IS NULL)),
    CONSTRAINT fk_uard_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_node_role_slots (
    group_id       VARCHAR(191) NOT NULL,
    node_key       VARCHAR(200) NOT NULL,
    role           VARCHAR(16) NOT NULL CHECK (role IN ('instruction', 'execution', 'review')),
    kind           VARCHAR(16) NOT NULL CHECK (kind IN ('assigned', 'not_used')),
    provider_id    VARCHAR(191) NULL,
    provider_name  VARCHAR(100) NULL,
    source         VARCHAR(16) NOT NULL CHECK (source IN ('creation_copy', 'wp_plan', 'pending_fill', 'card_explicit')),
    set_by         VARCHAR(191) NULL,
    set_at         VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, node_key, role),
    CHECK ((kind = 'assigned' AND provider_id IS NOT NULL AND TRIM(provider_id) <> '')
        OR (kind = 'not_used' AND provider_id IS NULL AND provider_name IS NULL)),
    CHECK ((source = 'pending_fill' AND set_by IS NULL) OR (source <> 'pending_fill' AND set_by IS NOT NULL)),
    CONSTRAINT fk_gnrs_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_node_role_init (
    group_id        VARCHAR(191) NOT NULL,
    node_key        VARCHAR(200) NOT NULL,
    state           VARCHAR(16) NOT NULL CHECK (state IN ('pending_init', 'initialized')),
    creator_id      VARCHAR(191) NULL,
    created_at      VARCHAR(40) NOT NULL,
    initialized_at  VARCHAR(40) NULL,
    PRIMARY KEY (group_id, node_key),
    CHECK ((state = 'pending_init' AND initialized_at IS NULL) OR (state = 'initialized' AND initialized_at IS NOT NULL)),
    CONSTRAINT fk_gnri_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

-- ── DOWN (rollback, DB-1 §3.5) ────────────────────────────────────────────────
-- Not advised: loses account role defaults and card role values. Default rollback = revert the code only.
-- Run after 165 and 164 are rolled back. No trigger here (the FKs clean up).
-- DROP TABLE IF EXISTS graph_node_role_init;
-- DROP TABLE IF EXISTS graph_node_role_slots;
-- DROP TABLE IF EXISTS user_ai_role_defaults;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
