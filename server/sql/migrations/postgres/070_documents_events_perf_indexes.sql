-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 권고 P2 — 쿼리 단가.
--
-- SQLite 원본(sql/migrations/sqlite/070_documents_events_perf_indexes.sql)의 PostgreSQL
-- 판이다. 근거·컬럼 순서·각 인덱스가 어느 쿼리를 덮는지는 원본 주석에 있다.
--
-- 방언 차이는 BEGIN/COMMIT 제거 하나뿐이다(기존 생성물과 동일한 처리). PostgreSQL 은
-- 이 스키마의 문자열 컬럼을 전부 TEXT 로 두고, TEXT 인덱싱에 prefix 길이가 필요 없으며
-- (MySQL 판과 다른 점), 인덱스 컬럼의 DESC 방향과 CREATE INDEX IF NOT EXISTS 를 모두
-- 지원한다. 그래서 SQLite 원본의 DDL 을 그대로 쓴다.

CREATE INDEX IF NOT EXISTS idx_documents_group_seq
    ON documents(group_id, seq);

CREATE INDEX IF NOT EXISTS idx_documents_group_type_review
    ON documents(group_id, type_code, doc_review_status);

CREATE INDEX IF NOT EXISTS idx_documents_prj_group_updated
    ON documents(project_id, group_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type_status
    ON documents(type_code, status);

CREATE INDEX IF NOT EXISTS idx_events_type_doc_event
    ON events(event_type, doc_id, event_id);
