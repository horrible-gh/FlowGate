-- 076_ai_invoke_runs.sql
-- flowgate.default.0359 (B0001 → NR0003 → P0006 → L0007 → DB0008):
-- 연속 실행이 마지막 홉에서 조용히 끝나도 아무 데도 남지 않던 문제.
--
-- 두 가지를 더한다.
--  1) ai_invoke_runs — 마감된 실행 1건 = 1행. 서버가 재시작해도 남는 유일한 기록.
--     쓰기는 마감 때 upsert 한 번뿐이고(살아 있는 실행은 메모리가 진실이다),
--     읽기는 상세(run_id 1건)와 목록(group_id | project_id + 최신순 LIMIT) 둘뿐이다.
--  2) ai_invoke_paused_chains 의 stop_* 4열 — 시스템이 멈춘 체인도 사용자가 멈춘
--     체인과 같은 모양으로 미니플레이어 목록에 실려 [이어서 진행]이 그대로 동작한다.
--     NULL 인 stop_kind 는 읽을 때 'user' 로 접는다(기존 행 = 전부 사용자 정지행).
--
-- 가산 전용: 기존 표를 재생성하지 않고, 기존 열을 지우거나 이름을 바꾸지 않는다.
-- 백필 없음. tokens 는 건드리지 않는다(ai_run_id 는 075 에 이미 있다).

BEGIN;

CREATE TABLE IF NOT EXISTS ai_invoke_runs (
    run_id               TEXT    PRIMARY KEY,
    group_id             TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    project_id           TEXT    NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    -- doc_ref 에는 FK 를 걸지 않는다: 문서는 되돌려 지울 수 있고(타임머신),
    -- 척추 문서가 사라졌다고 사고 기록까지 같이 사라지면 안 된다.
    doc_ref              TEXT    NOT NULL,
    mode                 TEXT    NOT NULL CHECK (mode IN ('single', 'continuous')),
    -- 저장은 마감 때 한 번뿐이라는 불변식의 강제 수단.
    status               TEXT    NOT NULL DEFAULT 'finished' CHECK (status = 'finished'),
    outcome              TEXT    CHECK (outcome IS NULL OR outcome IN ('complete', 'partial', 'none')),
    docs_reached         INTEGER NOT NULL DEFAULT 0 CHECK (docs_reached >= 0),
    docs_target          INTEGER CHECK (docs_target IS NULL OR docs_target >= 0),
    reached_doc_ids      TEXT,
    -- end_reason / stop_code 에는 CHECK 를 두지 않는다: 어휘가 늘어나는 값인데
    -- SQLite 는 CHECK 변경에 표 재생성을 요구한다(059 ai_mode 와 같은 판단).
    end_reason           TEXT,
    stop_code            TEXT,
    stop_reason          TEXT,
    resumable            INTEGER NOT NULL DEFAULT 0 CHECK (resumable IN (0, 1)),
    exit_code            INTEGER,
    last_message         TEXT,
    last_message_excerpt TEXT,
    provider_id          TEXT    REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    provider_name        TEXT,
    attempt_no           INTEGER NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
    attempts_used        INTEGER NOT NULL DEFAULT 0 CHECK (attempts_used >= 0),
    attempts_max         INTEGER CHECK (attempts_max IS NULL OR attempts_max >= 1),
    fallback_history     TEXT,
    register_errors      TEXT,
    tool_call_misses     INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_misses >= 0),
    turn_limit_exhausted INTEGER NOT NULL DEFAULT 0 CHECK (turn_limit_exhausted IN (0, 1)),
    oracle_mismatch      INTEGER NOT NULL DEFAULT 0 CHECK (oracle_mismatch IN (0, 1)),
    source_dirty         INTEGER CHECK (source_dirty IS NULL OR source_dirty IN (0, 1)),
    scratch_retained     TEXT,
    hop_item_seq         INTEGER CHECK (hop_item_seq IS NULL OR hop_item_seq >= 1),
    -- token_id 에도 FK 를 걸지 않는다: 작업권은 만료 30일 뒤 하드 삭제되고
    -- 이 기록은 90일 남는다. 수명이 다른 둘을 묶으면 짧은 쪽이 이긴다.
    token_id             TEXT,
    issued_to            TEXT    REFERENCES users(user_id) ON DELETE SET NULL,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT    NOT NULL,
    duration_ms          INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    timeout_sec          INTEGER CHECK (timeout_sec IS NULL OR timeout_sec > 0),
    deadline_at          TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

-- 목록 조회: WHERE <축> = ? ORDER BY started_at DESC, run_id DESC LIMIT ?
-- 정렬 순서를 인덱스가 담고 있어 LIMIT 이 조기 종료가 된다(070 idx_documents_prj_group_updated 선례).
-- run_id 꾬리는 같은 초에 시작한 두 실행의 순서를 고정한다.
CREATE INDEX IF NOT EXISTS idx_air_group_started
    ON ai_invoke_runs(group_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_project_started
    ON ai_invoke_runs(project_id, started_at DESC, run_id DESC);

-- 90일 보존 정리: DELETE ... WHERE finished_at < <기준시각>
CREATE INDEX IF NOT EXISTS idx_air_finished_at
    ON ai_invoke_runs(finished_at);

-- 시스템 정지행 표시. 기본값도 CHECK 도 두지 않는다:
-- SQLite 는 ADD COLUMN 에 CHECK 를 붙일 수 없고, 기존 행의 NULL 은
-- 읽는 쪽이 'user' 로 접는다(L0007 2.8) — 그래서 백필이 필요 없다.
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_kind TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_code TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_run_id TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_last_message_excerpt TEXT;

COMMIT;
