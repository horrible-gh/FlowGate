-- T391: add description + locale columns to document_types + load Korean descriptions
-- Idempotency: execute only after the Python runner checks whether the columns exist (follows the existing pattern)

ALTER TABLE document_types ADD COLUMN description TEXT;
ALTER TABLE document_types ADD COLUMN locale VARCHAR(8) NOT NULL DEFAULT 'ko';

-- ── requirements series ─────────────────────────────────────────────────────
UPDATE document_types SET description = '무엇을·왜 만들지를 정의하는 문서. 기능·비기능 요구사항을 정리한다.', locale = 'ko'
    WHERE type_code = 'R'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET description = '결정사항·아이디어·중간 기록용 자유 형식 문서.', locale = 'ko'
    WHERE type_code = 'M'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET description = '불명확한 사항에 대한 질문 문서. A로 응답받는다.', locale = 'ko'
    WHERE type_code = 'Q'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET description = 'Q에 대한 답변 문서. Q와 1:1 매핑된다.', locale = 'ko'
    WHERE type_code = 'A'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET description = '운영·세션·조사 진행 기록 문서.', locale = 'ko'
    WHERE type_code = 'L'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET description = '버그 보고 문서. 재현 절차·기대 동작·실제 동작을 포함한다.', locale = 'ko'
    WHERE type_code = 'B'  AND series = 'requirements' AND project_id IS NULL;

-- ── instruction series ─────────────────────────────────────────────────────
UPDATE document_types SET description = 'D(기본설계) 작성을 지시하는 문서.', locale = 'ko'
    WHERE type_code = 'DS' AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET description = '조사를 지시하는 문서. NR(조사레포트)로 결과를 받는다.', locale = 'ko'
    WHERE type_code = 'N'  AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET description = '구현 작업을 지시하는 문서. TR(작업레포트)로 결과를 받는다.', locale = 'ko'
    WHERE type_code = 'T'  AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET description = '테스트 시나리오 작성을 지시하는 문서. TSR로 결과를 받는다.', locale = 'ko'
    WHERE type_code = 'TS' AND series = 'instruction'  AND project_id IS NULL;

-- ── design series ──────────────────────────────────────────────────────────
UPDATE document_types SET description = '컴포넌트 역할·판단 흐름·모듈 관계·입출력 항목·화면 구성을 정의. PM 가독성용. SQL·의사코드 X (→ L·P·DB로 위임).', locale = 'ko'
    WHERE type_code = 'D'  AND series = 'design'       AND project_id IS NULL;
UPDATE document_types SET description = 'API 엔드포인트·요청/응답 JSON 포맷·메시지 스키마·헤더 규약을 정의.', locale = 'ko'
    WHERE type_code = 'P'  AND series = 'design'       AND project_id IS NULL;
UPDATE document_types SET description = '알고리즘·의사코드·수식·임계값·상태 전이표·결정 트리·경계 조건을 정의. 워커용.', locale = 'ko'
    WHERE type_code = 'L'  AND series = 'design'       AND project_id IS NULL;
UPDATE document_types SET description = 'CREATE TABLE·컬럼·인덱스·FK·마이그레이션 절차를 정의.', locale = 'ko'
    WHERE type_code = 'DB' AND series = 'design'       AND project_id IS NULL;

-- ── work series ────────────────────────────────────────────────────────────
UPDATE document_types SET description = 'N(조사지시)의 결과 보고서.', locale = 'ko'
    WHERE type_code = 'NR' AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET description = 'T(작업지시)의 결과 보고서.', locale = 'ko'
    WHERE type_code = 'TR' AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET description = 'TS(테스트시나리오지시)의 결과 보고서.', locale = 'ko'
    WHERE type_code = 'TSR'AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET description = '특정 산출물에 대한 검수·리뷰를 의뢰하는 문서. VR로 결과를 받는다.', locale = 'ko'
    WHERE type_code = 'V'  AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET description = '커밋 작업 지시 또는 기록.', locale = 'ko'
    WHERE type_code = 'C'  AND series = 'work'         AND project_id IS NULL;

-- ── action series ──────────────────────────────────────────────────────────
UPDATE document_types SET description = '산출물 승인 액션 기록.', locale = 'ko'
    WHERE type_code = 'AC' AND series = 'action'       AND project_id IS NULL;
UPDATE document_types SET description = '산출물 반려 액션 기록.', locale = 'ko'
    WHERE type_code = 'RJ' AND series = 'action'       AND project_id IS NULL;
