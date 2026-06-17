-- 040 Q/A/V 개편 ② — Q/A/V 도큐타입 은퇴.
-- Basis: group 0022 DB0006 §4.3, D0005 §3.6, R0001-3,4
-- 시스템 타입은 삭제 불가 → 비활성화(은퇴)로 처리. 행은 보존(과거 문서 FK 정합).
-- is_system 필터 제거: 전역 시스템 사본(is_system=1)과 향후 프로젝트 오버라이드
-- (통상 is_system=0)를 모두 덮는다. 현재 Q/A/V 는 전역 시스템 타입뿐이라 실커버리지
-- 영향은 없으나 주석-실제 불일치를 방지한다.
-- 효과: 생성 다이얼로그·타입 범례에서 자동 제외(활성 타입만 노출), 신규 생성 차단.

BEGIN;

UPDATE document_types
   SET is_active = 0, updated_at = datetime('now')
 WHERE type_code IN ('Q','A','V');

COMMIT;
