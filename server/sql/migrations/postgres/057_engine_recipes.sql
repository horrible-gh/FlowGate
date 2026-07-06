-- 057_engine_recipes.sql
-- flowgate.default.0157 (R0001 → D0002 → P0003 → L0004 → DB0005 → T0006): global engine test
-- recipe registry. Backs the command help API, the TS-mention "Engine recipes" block, auto-learning
-- from passed remote runs, and the read-only Settings visualization.
-- DELETE is soft (status='suppressed', a tombstone); identity is UNIQUE(engine). Auto-recovery loop,
-- repair deliveries and tokens reuse existing tables (no schema change); attempts derive from test_runs.

CREATE TABLE IF NOT EXISTS engine_recipes (
    id                  BIGSERIAL PRIMARY KEY,
    engine              TEXT    NOT NULL UNIQUE,
    label               TEXT    NOT NULL DEFAULT '',
    setup               TEXT    NOT NULL,
    run_example         TEXT    NOT NULL DEFAULT '',
    notes               TEXT    NOT NULL DEFAULT '',
    origin              TEXT    NOT NULL DEFAULT 'worker'
                            CHECK (origin IN ('seed', 'auto', 'worker')),
    status              TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suppressed')),
    last_success_run_id TEXT,
    last_success_at     TEXT,
    updated_by          TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at          TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE INDEX IF NOT EXISTS idx_engine_recipes_lookup
    ON engine_recipes(status, engine);

INSERT INTO engine_recipes (engine, label, setup, run_example, notes, origin, updated_by)
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
     'seed', 'seed')
ON CONFLICT (engine) DO NOTHING;
