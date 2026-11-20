-- 130_git_concurrency_instance_gate.sql
-- flowgate.default.0669 (unit 1a) / 0666 0010-DB §2.4·§2.5·§3 (#129 → 130: 0665 already took 129).
-- server_instance       — server-process liveness signal; basis for stale/dead lock and legacy-lock decisions (L 2.8).
--                         touch_seq is a dedicated monotonic counter ensuring heartbeat CAS has matched=affected rows
--                         on MySQL/MariaDB as well (DB §2.0 matched-versus-changed pitfall).
-- legacy_admission_gate — mutual exclusion between legacy Project mutexes and new domain locks during transition (L 2.6).
-- Additive. Timestamps are fixed-width, 25-character now_iso() strings.

CREATE TABLE server_instance (
    instance_id        VARCHAR(37)  NOT NULL PRIMARY KEY,
    node_key           TEXT NOT NULL,
    pid                INTEGER      NOT NULL,
    process_started_at VARCHAR(25)  NULL,
    started_at         VARCHAR(25)  NOT NULL,
    heartbeat_at       VARCHAR(25)  NOT NULL,
    status             VARCHAR(8)   NOT NULL DEFAULT 'alive' CHECK (status IN ('alive', 'stopped', 'dead')),
    stopped_at         VARCHAR(25)  NULL,
    touch_seq          INTEGER      NOT NULL DEFAULT 0
);

CREATE INDEX idx_instance_liveness ON server_instance (status, heartbeat_at);

CREATE TABLE legacy_admission_gate (
    project_id         TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    legacy_holder      TEXT NULL,
    legacy_since       VARCHAR(25)  NULL,
    legacy_instance_id VARCHAR(37)  NULL REFERENCES server_instance(instance_id) ON DELETE RESTRICT,
    gate_version       INTEGER      NOT NULL DEFAULT 0,
    updated_at         VARCHAR(25)  NOT NULL
);

CREATE INDEX idx_gate_legacy_holder ON legacy_admission_gate (legacy_holder);
