-- 133_git_project_lock_owner_instance.sql
-- flowgate.default.0669 (단위 1a) / 0666 0010-DB §2.6·§3 (설계 번호 132 → 133).
-- git_project_lock.owner_instance_id — 구 잠금 행을 삽입한 서버 인스턴스. 비어 있으면 DEAD 로 본다
-- (L 2.6.5 전제 P-L1). 다른 FK 와 달리 ON DELETE SET NULL: instance 행이 지워져도 같은 의미(DEAD)로 수렴.
-- 가산형, 백필 없음(기존 행 NULL = 브리지 이전 프로세스의 잔재).

ALTER TABLE git_project_lock ADD COLUMN owner_instance_id VARCHAR(37) NULL;

CREATE INDEX idx_git_project_lock_owner ON git_project_lock (owner_instance_id);

ALTER TABLE git_project_lock ADD CONSTRAINT fk_gpl_owner_instance
    FOREIGN KEY (owner_instance_id) REFERENCES server_instance(instance_id) ON DELETE SET NULL;
