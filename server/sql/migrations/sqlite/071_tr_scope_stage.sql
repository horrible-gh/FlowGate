-- 071_tr_scope_stage.sql
-- TR 작업범위 검증 (flowgate.default.0299: R0001 → D0004 → NR0006 → T0007).
--
-- project_git_config.tr_scope_stage — 프로젝트 단위 적용 단계.
--   'observe' 관측: 판정은 하되 제출을 막지 않고 결과만 기록한다(기본값).
--   'warn'    경고: 제출은 통과시키되 응답/문서에 경고를 남긴다.
--   'enforce' 강제: 판정에 따라 제출을 거부한다.
--
-- 기본값이 'observe' 인 이유는 D0004 §3.6 그대로다. 이미 발급되어 돌고 있는
-- 작업들에는 `## 변경 파일` 섹션이 없으므로, 곧바로 강제로 켜면 전부 걸린다.
-- 운영자가 화면에서 단계를 올린다.
--
-- 이 검증은 워크트리 안에서만 성립하므로 git 연동이 꺼진 프로젝트에서는
-- 단계와 무관하게 아예 수행하지 않는다. 따라서 설정을 project_git_config 에
-- 두는 것이 자연스럽고, 행이 없는 프로젝트는 연동이 없는 프로젝트다.
-- 추가만 하는 마이그레이션이며 인덱스/CHECK 는 두지 않는다(값 검증은 앱 계층).

BEGIN;

ALTER TABLE project_git_config ADD COLUMN tr_scope_stage TEXT DEFAULT 'observe';

COMMIT;
