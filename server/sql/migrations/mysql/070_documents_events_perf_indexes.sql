-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 권고 P2 — 쿼리 단가.
--
-- SQLite 원본(sql/migrations/sqlite/070_documents_events_perf_indexes.sql)의 MySQL/MariaDB
-- 판이다. 근거·컬럼 순서·각 인덱스가 어느 쿼리를 덮는지는 원본 주석에 있다.
--
-- 방언 차이 두 가지만 여기서 다룬다.
--
-- 1. BEGIN/COMMIT 제거 — DDL 이 암묵적으로 커밋되므로 명시적 트랜잭션은 의미가 없다.
--    069/068 등 기존 생성물과 같은 처리다.
--
-- 2. TEXT 컬럼 prefix 길이 — documents.doc_review_status 는 이 방언에서 TEXT 라
--    prefix 길이 없이는 인덱싱할 수 없다(1170 에러). 067_auth_sessions.sql 의
--    `revoked_at(191)` 과 같은 방식으로 191 을 붙였다. 191 은 이 스키마가 문자열 키에
--    쓰는 VARCHAR 폭이며, doc_review_status 의 실제 값은 가장 긴 것이
--    'wf_in_progress'(14자)라 잘림이 발생하지 않는다.
--    나머지 컬럼은 모두 VARCHAR/INTEGER 라 그대로 쓴다:
--      documents.group_id VARCHAR(191) / type_code VARCHAR(191) / project_id VARCHAR(191)
--      documents.updated_at VARCHAR(191) / status VARCHAR(64) / seq INTEGER
--      events.event_type VARCHAR(191) / doc_id VARCHAR(191) / event_id INTEGER
--    최대 키 길이는 idx_documents_group_type_review 의 (764+764+764)=2292 바이트로
--    InnoDB DYNAMIC 행 포맷 상한 3072 안에 든다.
--
-- ORDER BY 방향 표기(DESC)는 MariaDB 가 파싱 후 무시한다 — 정방향 스캔으로 같은 순서가
-- 나오므로 동작 차이는 없고, 001 의 idx_events_doc_created / 027 의 idx_documents_updated
-- 가 이미 같은 표기를 쓰고 있어 표기를 맞췄다.

CREATE INDEX IF NOT EXISTS idx_documents_group_seq
    ON documents(group_id, seq);

CREATE INDEX IF NOT EXISTS idx_documents_group_type_review
    ON documents(group_id, type_code, doc_review_status(191));

CREATE INDEX IF NOT EXISTS idx_documents_prj_group_updated
    ON documents(project_id, group_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type_status
    ON documents(type_code, status);

CREATE INDEX IF NOT EXISTS idx_events_type_doc_event
    ON events(event_type, doc_id, event_id);
