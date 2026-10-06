-- 130_git_concurrency_instance_gate.sql
-- flowgate.default.0669 (단위 1a) / 0666 0010-DB §2.4·§2.5·§3 (#129 → 130: 129 는 0665 가 선점).
-- server_instance       — 서버 프로세스 생존 신호. 잠금·구 잠금 행의 stale/dead 판정 근거(L 2.8).
--                         touch_seq 는 heartbeat CAS 가 MySQL/MariaDB 에서도 매치=영향 행이 되게
--                         하는 전용 단조 카운터(DB §2.0 matched 대 changed 함정).
-- legacy_admission_gate — 전환기 동안 구 Project mutex 경로와 신규 domain 잠금 경로의 상호 배제(L 2.6).
-- 가산형. 시각은 now_iso() 25자 고정폭 문자열.

CREATE TABLE server_instance (
    instance_id        VARCHAR(37)  NOT NULL PRIMARY KEY,
    node_key           VARCHAR(191) NOT NULL,
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
    project_id         VARCHAR(191) NOT NULL PRIMARY KEY,
    legacy_holder      VARCHAR(191) NULL,
    legacy_since       VARCHAR(25)  NULL,
    legacy_instance_id VARCHAR(37)  NULL,
    gate_version       INTEGER      NOT NULL DEFAULT 0,
    updated_at         VARCHAR(25)  NOT NULL,
    CONSTRAINT fk_gate_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_gate_instance FOREIGN KEY (legacy_instance_id) REFERENCES server_instance(instance_id) ON DELETE RESTRICT
);

CREATE INDEX idx_gate_legacy_holder ON legacy_admission_gate (legacy_holder);
