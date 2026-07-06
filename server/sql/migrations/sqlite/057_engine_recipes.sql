-- 057_engine_recipes.sql
-- flowgate.default.0157 (R0001 → D0002 → P0003 → L0004 → DB0005 → T0006): global engine test
-- recipe registry. Backs the command help API (GET /test-commands/help), the TS-mention
-- "Engine recipes" block, auto-learning from passed remote test runs, and the read-only
-- Settings visualization (GET /projects/{id}/engine-recipes).
--
-- Layer above project_test_commands (055): 055 answers "WHAT to run" per project; this answers
-- "HOW to make the run possible" globally per test engine (pytest, npm, …). They coexist.
--
-- Physical delete never happens (L §2-2): a DELETE flips status to 'suppressed', a tombstone that
-- keeps the (engine) slot so auto-learning cannot re-register it; a manual re-add of the same engine
-- revives the same row. Identity is the normalized engine string (trim + lowercase + collapse ws,
-- L §2-1), enforced by UNIQUE(engine) — the tombstone re-uses the row, so engine-per-row is 1 forever.
--
-- The auto-recovery loop, repair deliveries and repair tokens reuse existing tables (test_runs 052,
-- tokens 050) with NO schema change; attempt counts are derived from test_runs history (L §2-6).

BEGIN;

CREATE TABLE IF NOT EXISTS engine_recipes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    engine              TEXT    NOT NULL UNIQUE,            -- normalized (trim + lower + collapse ws)
    label               TEXT    NOT NULL DEFAULT '',
    setup               TEXT    NOT NULL,
    run_example         TEXT    NOT NULL DEFAULT '',
    notes               TEXT    NOT NULL DEFAULT '',
    origin              TEXT    NOT NULL DEFAULT 'worker'
                            CHECK (origin IN ('seed', 'auto', 'worker')),
    status              TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suppressed')),
    last_success_run_id TEXT,                               -- soft ref to test_runs.run_id (no FK)
    last_success_at     TEXT,
    updated_by          TEXT    NOT NULL DEFAULT '',        -- 'seed' | 'auto-learn' | token id | user id
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_engine_recipes_lookup
    ON engine_recipes(status, engine);

-- Seed (P §help list literals; DB §3). Idempotent: INSERT OR IGNORE keeps operator edits on re-run.
INSERT OR IGNORE INTO engine_recipes (engine, label, setup, run_example, notes, origin, updated_by)
VALUES
    ('pytest', 'Python pytest (venv)',
     'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install pytest',
     '.venv/bin/pytest server/tests -q',
     'Do not pip-install into the system interpreter (PEP668). Never swallow setup failures with ''|| true'' — a failed setup must stay failed so the auto-recovery loop can run.',
     'seed', 'seed'),
    ('npm', 'Node npm/vitest (login shell)',
     'bash -lc ''cd client && npm install''',
     'bash -lc ''cd client && npx vitest run''',
     'The runner executes under a non-login /bin/sh with no nvm PATH — always wrap npm-family commands in bash -lc. Do NOT use npm ci (it locks the esbuild binary).',
     'seed', 'seed');

COMMIT;
