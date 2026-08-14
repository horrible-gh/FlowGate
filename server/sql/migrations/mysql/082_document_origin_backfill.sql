-- 082_document_origin_backfill.sql
-- flowgate.default.0410 T0008 / TR0009 rev2 — 081 이 더한 두 스냅샷 열을 기존 행에도 채운다.
--
-- 081 은 "백필 없음"이었다. 그래서 열이 생긴 뒤에 만들어진 문서만 작성 AI 를 갖고,
-- 이미 있던 문서는 영원히 NULL 로 남아 화면에서는 계속 등록 계정 이름(예: test)만
-- 보였다 — "아무것도 변한게 없다"는 반려의 절반이 이것이다.
--
-- 값을 새로 추론하지 않는다. 출처는 단 하나, ai_invoke_runs.reached_doc_ids —
-- 그 실행이 실제로 만들어 낸 문서 목록(JSON 배열)이다. 그 행이 마감될 때 같이 저장된
-- provider_name 을, 같은 행의 run_id 와 함께 그대로 옮겨 적는다. 공급자 마스터를
-- 다시 조회하지 않으므로 이름이 바뀌거나 지워진 공급자도 그때 그 이름 그대로 남는다.
--
-- 안전 규칙:
--   * 두 열이 모두 NULL 인 행만 건드린다 — 이미 찍힌 스냅샷은 절대 덮어쓰지 않는다.
--   * 실행 기록이 없는 문서(사람이 만든 문서, 마감 기록이 없는 실행)는 NULL 로 남는다.
--   * provider_name 이 비어 있거나 공백뿐인 실행은 후보에서 제외한다(이름을 추측하지 않는다).
--   * 같은 문서를 여러 실행이 신고하면 먼저 마감된 실행이 이긴다(그 문서를 만든 실행).
--   * 두 번 돌려도 결과가 같다(첫 실행 뒤에는 WHERE 가 아무 행도 고르지 않는다).
--
-- LIKE 로 JSON 배열을 훑는 것은 세 방언이 모두 갖고 있는 유일한 수단이라서다. doc_id 는
-- 큰따옴표로 감싼 원소로만 일치시킨다. (doc_id 에 '_' 가 들어가면 LIKE 의 한 글자
-- 와일드카드가 되지만, 한 글자만 다른 다른 문서가 같은 실행의 목록에 함께 실려야만
-- 잘못 걸린다.)

UPDATE documents
   SET origin_provider_name = (
           SELECT r.provider_name
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE CONCAT('%"', documents.doc_id, '"%')
            ORDER BY r.finished_at ASC, r.run_id ASC
            LIMIT 1
       ),
       origin_ai_run_id = (
           SELECT r.run_id
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE CONCAT('%"', documents.doc_id, '"%')
            ORDER BY r.finished_at ASC, r.run_id ASC
            LIMIT 1
       )
 WHERE origin_provider_name IS NULL
   AND origin_ai_run_id IS NULL
   AND EXISTS (
           SELECT 1
             FROM ai_invoke_runs r
            WHERE r.provider_name IS NOT NULL
              AND TRIM(r.provider_name) <> ''
              AND r.reached_doc_ids LIKE CONCAT('%"', documents.doc_id, '"%')
       );
