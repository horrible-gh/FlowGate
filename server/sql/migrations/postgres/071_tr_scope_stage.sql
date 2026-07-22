-- 071_tr_scope_stage.sql
-- TR 작업범위 검증 (flowgate.default.0299: R0001 → D0004 → NR0006 → T0007).
--
-- project_git_config.tr_scope_stage — 프로젝트 단위 적용 단계.
--   'observe' 관측 / 'warn' 경고 / 'enforce' 강제. 기본값 'observe'.
-- 상세 설명은 sqlite 대응 파일과 동일하다.

ALTER TABLE project_git_config ADD COLUMN tr_scope_stage TEXT DEFAULT 'observe';
