-- 070_documents_events_perf_indexes.sql
-- flowgate.default.0291 (R0001 → N0002 → NR0003 → T0010): NR0003 권고 P2 — 쿼리 단가.
--
-- Background: NR0003 발견 2/6. 화면 갱신 경로에 있는 documents/events 조회 여러 건이
-- 인덱스를 못 타고 전체 스캔한다. 호출 "수"는 상수지만 비용은 누적 행수에 비례해
-- 계속 커지므로, 팬아웃(P1)을 막은 뒤에도 남는 장기 열화 요인이다.
--
-- 이 마이그레이션은 인덱스만 추가한다. 테이블/컬럼/데이터는 건드리지 않으므로
-- 되돌리기는 DROP INDEX 5회로 끝난다.
--
-- ── documents ────────────────────────────────────────────────────────────────
--
-- 기존 인덱스 중 group_id 를 선행 컬럼으로 갖는 것이 하나도 없다.
-- idx_documents_prj_mod_grp_status 는 (project_id, module, group_id, status) 라
-- group_id 는 세 번째 컬럼이고, project_id/module 없이 group_id 만 주는 쿼리는
-- 이 인덱스를 쓸 수 없다. 그런 쿼리가 화면 갱신 경로에 세 종류 있다.
--
-- 1) idx_documents_group_seq (group_id, seq)
--    db/workflow_return_points.py get_front_doc():
--        SELECT ... FROM documents WHERE group_id = ? AND seq = ?
--    _return_point_payload() 가 화면 갱신 1회에 2~3번 호출하므로 로그에서 3회 나온다.
--
-- 2) idx_documents_group_type_review (group_id, type_code, doc_review_status)
--    services/git_service.py 의 배치 조회 두 건을 함께 덮는다:
--        _groups_root_wf_done():  WHERE group_id IN (...) AND type_code IN ('R','B')
--                                   AND doc_review_status = 'wf_done'
--        _group_ac_doc_ids():     WHERE group_id IN (...) AND type_code = 'AC'
--                                   GROUP BY group_id
--    단일 그룹 버전(_group_root_wf_done / _group_ac_doc_id)도 같은 형태다.
--    컬럼 순서는 세 술어의 선택도 순서 그대로다 — group_id 로 좁히고, type_code 로
--    한 자리 수까지 줄이고, doc_review_status 는 인덱스 안에서 걸러 테이블 접근을 없앤다.
--
-- 3) idx_documents_prj_group_updated (project_id, group_id, updated_at DESC)
--    db/documents.py get_documents():
--        WHERE project_id = ? AND group_id = ? [AND type_code = ?]
--        ORDER BY updated_at DESC LIMIT ?
--    종전에는 project_id 프리픽스까지만 인덱스를 타고 정렬은 filesort 였다. 이 인덱스는
--    정렬 순서까지 담고 있어 LIMIT 이 조기 종료로 바뀐다 — 그룹 문서가 늘어도 읽는 행수가
--    LIMIT 근처에서 멈춘다. DESC 표기는 기존 idx_documents_updated 관례를 그대로 따랐다
--    (SQLite/PostgreSQL/MySQL 8 은 방향을 저장하고, MariaDB 는 파싱 후 무시한다 —
--     어느 쪽이든 정방향 스캔으로 같은 순서가 나오므로 동작 차이는 없다).
--
-- 4) idx_documents_doc_type_status (type_code, status) — 복구
--    이 인덱스는 015_phase3_qa.sql 이 만들었지만 **지금 어떤 DB 에도 존재하지 않는다.**
--    027_t487_workflow_review_status.sql 이 documents 를 DROP/RENAME 으로 재생성하면서
--    001 의 인덱스 7개만 다시 만들고 013·015 가 추가한 2개를 빠뜨렸다. 새로 만든 DB 에
--    실제로 남는 documents 인덱스는 001 의 7개뿐임을 마이그레이션 전량 적용 후
--    sqlite_master 조회로 확인했다. NR0003 발견 2의 "현재 인덱스" 목록은 마이그레이션
--    파일을 읽어 만든 것이라 이 두 개를 존재하는 것으로 잘못 적고 있다.
--    db/documents.py get_documents_by_status_and_types()
--        (WHERE status = ? AND type_code IN (...)) 가 이 인덱스의 원래 수요이므로
--    여기서 복구한다.
--    013 의 idx_documents_revision (doc_id, revision_no) 는 **복구하지 않는다** —
--    ux_documents_doc_id 가 doc_id UNIQUE 라 doc_id 당 행이 하나뿐이고, 뒤에 붙는
--    revision_no 가 걸러낼 것이 없다. 되살려 봐야 쓰기 비용만 늘어난다.
--
-- ── events ───────────────────────────────────────────────────────────────────
--
-- 5) idx_events_type_doc_event (event_type, doc_id, event_id)
--    NR0003 발견 6 / P2-2. db/events.py get_created_memo_files_map_by_project() 의
--    내부 집계
--        SELECT e2.doc_id, MAX(e2.event_id) FROM events e2
--        JOIN documents d ON d.doc_id = e2.doc_id
--        WHERE e2.event_type = 'created' AND ... GROUP BY e2.doc_id
--    는 트리 응답마다 실행되고, events 는 문서 수명 내내 단조 증가하므로 가장 빨리
--    나빠지는 쿼리다. 기존 idx_events_type 은 (event_type) 단일이라 GROUP BY/MAX 를
--    돕지 못하고 event_type='created' 행 전부를 테이블에서 다시 읽어야 했다. 이 인덱스는
--    doc_id 로 그룹이 이미 정렬돼 있고 각 그룹의 마지막 항목이 MAX(event_id) 라
--    집계 자체가 인덱스 스캔으로 끝난다.
--    memo_file 은 인덱스에 넣지 않았다 — MySQL 에서 TEXT 컬럼은 prefix 길이 없이
--    인덱싱할 수 없어 세 방언 공통으로 쓸 수 없다. NR0003 P2-2 의 다른 선택지인
--    documents 비정규화(latest_created_memo_file)는 백필과 이중 기록이 따라오므로
--    이번 라운드에서는 택하지 않았다. 인덱스만으로 스캔 폭이 event_type='created' 로
--    좁혀지면 그 전에 재측정할 여지가 생긴다.
--
-- 쓰기 비용: documents 에 인덱스 4개(3 신규 + 1 복구), events 에 1개가 늘어난다.
-- 두 테이블 모두 압도적으로 읽기 우위이고(쓰기는 문서 등록·상태 전이 시점의 단건),
-- 발견 1의 팬아웃 때문에 같은 읽기가 접속자 수만큼 반복된다. 읽기 쪽에 무게를 싣는 것이 맞다.

BEGIN;

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

COMMIT;
