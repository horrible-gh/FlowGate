-- 080a_ai_invoke_prompt_audit.sql
-- flowgate.default.0406 (B0001 -> M0019 -> N0020 -> NR0021 -> T0022 작업 3·5):
-- "연속 작업(무인)에 멘트가 똑바로 안 들어간다"를 사후에 판정할 수 없던 공백을 메운다.
--
-- NR0021 §8 이 확정한 것: 연속 실행 창의 세션형 [전달멘트] 공통값·단계 override 와
-- 최종 워커 프롬프트는 어디에도 보존되지 않는다. tokens 에도 ai_invoke_runs 에도
-- 그 열이 없다. 그래서 "사용자가 입력했다고 기억하는 문구가 실제로 프롬프트에
-- 들어갔는가"는 재현도 반증도 불가능했다. 같은 이유로 T0013·T0017 처럼 서버가 N/T 를
-- 대신 작성·승인해 AI 워커가 아예 붙지 않은 홉도, 끝난 뒤에는 "그 단계가 사라졌다"와
-- 구분되지 않았다.
--
-- 더하는 것은 두 묶음이다. 모두 ai_invoke_runs 한 표에 붙으므로 run_id 로 조회된다.
--   1) 누가 이 홉을 수행했나 — worker_document_type, auto_handled_item_seqs,
--      continuation_instruction_mode_{requested,normalized,fallback_applied}.
--      요청 원값과 정규화값을 나눠 둔 것이 핵심이다: 사용자가 auto_approved 를 고른
--      것과, 어떤 진입점이 모드를 빠뜨려 서버가 대신 골라 준 것은 다른 사건이다.
--   2) 전달멘트가 들어갔나 — prompt_message_source(단계 override / 공통 기본값 /
--      저장된 시퀀스 note 폴백 / 없음), 적용 문자열과 최종 프롬프트의 길이·sha256.
--
-- **원문은 저장하지 않는다.** 길이와 해시, 결정 결과만으로 이 종류의 신고는 판정된다.
-- 원문을 남기면 그것대로 새는 정보가 되고, 90일 보존 정책과도 어울리지 않는다.
--
-- 가산 전용: 기존 열을 지우거나 이름을 바꾸지 않고, 백필도 하지 않는다. 이 마이그레이션
-- 이전에 끝난 실행은 NULL/0 으로 읽히고, 그것이 "그때는 기록하지 않았다"는 사실이다.

BEGIN;

ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS worker_document_type TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_requested TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_normalized TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_fallback_applied SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS auto_handled_item_seqs TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_message_source TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_common_default_applied SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_user_message_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_user_message_sha256 TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_final_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_final_sha256 TEXT;

COMMIT;
