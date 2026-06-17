-- 038 Q/A/V 개편 ② — questions 컨테이너를 documents(doc_id) 에 매단다.
-- Basis: group 0022 DB0006 §3.3/§4.1, L0007 §3.1
-- (가) questions.doc_id FK + UNIQUE(doc_id) → 문서당 컨테이너 1개
-- (나) question_items.title / asker_kind (제목·질의주체)
-- (다) AI 질의 컨테이너용 예약 시스템 user 'u-system' (created_by FK·NOT NULL 충족)
-- 백필: 레거시 프로젝트 단위 questions 를 다대일 가드로 doc_id 표준화(대표 1행만 승격).
--
-- 재실행 안전성은 migrations 추적 테이블 + 파일 단일 트랜잭션 롤백에서 온다(035 등과 동일).
-- ALTER TABLE ADD COLUMN 은 SQLite 에 IF NOT EXISTS 가 없어 문 단위로 idempotent 하지 않다.

BEGIN;

-- (가) questions: 문서 FK 부여 (기본값 없음 → ALTER ADD COLUMN 허용)
ALTER TABLE questions ADD COLUMN doc_id TEXT
    REFERENCES documents(doc_id) ON DELETE CASCADE;

-- (나) question_items: 제목·질의주체
ALTER TABLE question_items ADD COLUMN title TEXT;
ALTER TABLE question_items ADD COLUMN asker_kind TEXT NOT NULL DEFAULT 'human'
    CHECK (asker_kind IN ('human','ai'));

-- (다) AI 질의 컨테이너용 예약 시스템 user (로그인 불가: is_active=0)
INSERT OR IGNORE INTO users
    (user_id, username, email, password, is_active, is_admin,
     first_login_required, created_at, updated_at)
VALUES
    ('u-system', 'system', 'system@flowgate.local', '!', 0, 0, 0,
     datetime('now'), datetime('now'));

-- ── 백필 (다대일 가드) ─────────────────────────────────────────────────────────
-- 레거시 questions 는 프로젝트 단위 독립 채번이라 같은 related_doc 를 가리키는 행이
-- 복수일 수 있다(다대일). 모든 행을 doc_id=q_id=related_doc 로 만들면 q_id 전역
-- UNIQUE(idx_questions_q_id) 와 신규 ux_questions_doc 부분 UNIQUE 를 동시에 위반해
-- 트랜잭션 전체가 실패한다. → 문서당 "대표 1행"만 승격.

-- 1) 이미 q_id 가 실제 doc_id(=문서 연동 관행)인 행: q_id 재설정 없이 doc_id 만 채움.
--    (q_id 는 전역 UNIQUE 라 doc 당 최대 1행 → 충돌 없음)
UPDATE questions
   SET doc_id = q_id
 WHERE doc_id IS NULL
   AND q_id IN (SELECT doc_id FROM documents);

-- 2) 미표준화 + related_doc 가 실제 문서 + 그 문서에 아직 컨테이너 없음 → 대표 1행만 승격.
--    대표는 MIN(id). 다대일 잔여 행은 doc_id IS NULL 로 남아 §6 비노출 대상.
UPDATE questions
   SET doc_id = related_doc,
       q_id   = related_doc
 WHERE doc_id IS NULL
   AND related_doc IN (SELECT doc_id FROM documents)
   AND related_doc NOT IN (SELECT doc_id FROM questions WHERE doc_id IS NOT NULL)
   AND id = (SELECT MIN(q2.id) FROM questions q2
              WHERE q2.doc_id IS NULL AND q2.related_doc = questions.related_doc);

-- 문서당 컨테이너 1개 (백필 후 NULL/잔존 전역행은 부분 인덱스로 제외)
CREATE UNIQUE INDEX IF NOT EXISTS ux_questions_doc
    ON questions(doc_id) WHERE doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_questions_doc_status
    ON questions(doc_id, status, created_at);

COMMIT;
