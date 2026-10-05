-- 135_operation_job_group_index.sql
-- flowgate.default.0669 (단위 6b): Group 단위 job 조회용 인덱스.
-- 승인 진행 여부 조회(finalize 상태의 approval_in_flight)와 approval-retry 경로가
-- 같은 Group의 미종단 final_approval_publish job을 찾는다. 가산형, 데이터 변경 없음.

CREATE INDEX idx_job_group ON operation_job (group_id, kind, status);
