-- 086_ai_invoke_run_diagnostics.sql
-- flowgate.default.0446 (B0001 -> CH0012 -> WP0013 -> T0016 §2):
-- AI 실행이 왜 끊겼는지를 프로세스 밖으로 내보낸다.
--
-- T0014 가 붙인 무진전 감시는 판정을 run["watchdog_kill"] 에만 남겼고, stdout/stderr
-- tail 과 미완 변경 파일 목록도 완료 응답이나 메모리 run 에서만 보였다. 서버가 다시
-- 뜨면 두 번째 세션은 직전 실행이 일하다 시간초과된 것인지, 어떤 파일이 미완 상태로
-- 남았는지 알 방법이 없다. 반려 재작업을 다시 부를 때 그 사실을 워커에게 인계하려면
-- 완료행에 남아 있어야 한다.
--
-- 더하는 열은 다섯이다. 모두 ai_invoke_runs 한 표에 붙으므로 run_id 로 조회된다.
--   timeout_kind        무진전(no_progress) 인지 4시간 절대 상한(absolute_cap) 인지.
--                       감시 표식이 없는 기존/legacy timeout 행은 NULL 로 남아 구별된다.
--                       다음 단계가 "무진전으로 끊긴 직전 실행"만 골라야 하므로 사람이
--                       읽는 문장을 파싱하게 만들지 않는다.
--   timeout_diagnosis   사람이 읽는 종료 진단 한 줄. 총 경과와 마지막 무진전 구간을
--                       함께 담는다. 같은 표의 stop_reason 과 같은 성격의 열이라 같은
--                       관례(영문 한 문장)를 따른다.
--   stdout_tail         _cli_execute() 가 이미 OUTPUT_TAIL_BYTES 로 자른 값 그대로.
--   stderr_tail         같음. 전체 출력이나 프롬프트를 새로 저장하지 않는다.
--   source_dirty_files  finalize 가 계산한 최대 20개 경로의 JSON 배열.
--
-- **원문 전체는 저장하지 않는다.** 잘림 상한을 늘리지 않고 90일 보존 정책을 그대로 따른다.
--
-- 가산 전용: 기존 열을 지우거나 이름을 바꾸지 않고, 백필도 하지 않는다. 이 마이그레이션
-- 이전에 끝난 실행은 NULL 로 읽히고, 그것이 "그때는 기록하지 않았다"는 사실이다.

-- MySQL 의 TEXT 는 65,535 바이트다. tail 은 8,192자 상한이라 다국어(1자 3~4바이트)로도
-- 들어가고, JSON 배열도 20개 경로라 여유가 크다. 다섯 열 모두 NULL 허용, 기본값 없음.
ALTER TABLE ai_invoke_runs ADD COLUMN timeout_kind VARCHAR(32) NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN timeout_diagnosis TEXT NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN stdout_tail TEXT NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN stderr_tail TEXT NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN source_dirty_files TEXT NULL;
