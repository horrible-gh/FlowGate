-- 168_ai_role_invocation_outputs.sql — flowgate.v02.0113 T#5 (0030-M §5, 0002-D 개정 §3.14-3: each launch's own message and full error) (MySQL/MariaDB)
-- Additive only.
--   ai_role_invocation_outputs  one row per role invocation record (inv_id, a launch or an admission refusal), added once
--                               when the launch ended: the AI's last message of that launch (body), its stderr / stdout
--                               tail, the registration errors it added and the classification detail. The command
--                               result shows each launch's own body and error from it; a launch recorded before this
--                               table has no row and is shown as "output not preserved". No REFERENCES (a history row,
--                               like ai_role_invocations): a run or Flow delete leaves it. There is no DELETE of it.
-- Rows are not touched; safe to run again.
CREATE TABLE IF NOT EXISTS ai_role_invocation_outputs (
    inv_id                VARCHAR(191) NOT NULL PRIMARY KEY,
    ai_run_id             VARCHAR(191),
    launch_no             INT,
    body                  LONGTEXT,
    stderr_tail           LONGTEXT,
    stdout_tail           LONGTEXT,
    register_errors_json  LONGTEXT,
    detail                LONGTEXT,
    created_at            VARCHAR(40)  NOT NULL,
    INDEX idx_ai_role_invocation_outputs_run (ai_run_id, launch_no)
);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the launches' own outputs (their messages show as not preserved). Default rollback = revert the
-- code only.
-- DROP TABLE IF EXISTS ai_role_invocation_outputs;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
