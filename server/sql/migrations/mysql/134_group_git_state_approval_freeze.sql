-- 134_group_git_state_approval_freeze.sql
-- flowgate.default.0669 (단위 6a) / 0666 0010-DB §2.7·§3·§4(q) (설계 번호 133 → 134), 0009-L §2.11.
-- group_git_state 에 승인 동결 claim(approval_freeze_job_id/state/at)과 CAS 전용 touch_seq 를 더한다.
-- Group 당 claim 은 최대 1개(PK group_id 가 구조적으로 보장). state: freezing(F1~F4) / publish_wait(F5 이후).
-- FK 는 ON DELETE SET NULL: job 행이 운영 정리로 지워져도 claim 이 고아로 남지 않는다(DB §2.7).
-- 가산형, 백필 없음(NULL = 동결 claim 없음). touch_seq 는 NOT NULL DEFAULT 0 이라 기존 행은 0 으로 채워진다.

ALTER TABLE group_git_state ADD COLUMN approval_freeze_job_id VARCHAR(30) NULL;
ALTER TABLE group_git_state ADD COLUMN approval_freeze_state VARCHAR(16) NULL;
ALTER TABLE group_git_state ADD COLUMN approval_freeze_at VARCHAR(25) NULL;
ALTER TABLE group_git_state ADD COLUMN touch_seq INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_group_git_state_freeze ON group_git_state (approval_freeze_job_id);

ALTER TABLE group_git_state ADD CONSTRAINT fk_ggs_approval_freeze_job
    FOREIGN KEY (approval_freeze_job_id) REFERENCES operation_job(job_id) ON DELETE SET NULL;
ALTER TABLE group_git_state ADD CONSTRAINT chk_ggs_approval_freeze_state
    CHECK (approval_freeze_state IS NULL OR approval_freeze_state IN ('freezing', 'publish_wait'));
