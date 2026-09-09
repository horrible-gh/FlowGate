-- 066_question_options.sql
-- Q 선택지 확장 (flowgate.default.0243: R0001 -> D0006 -> DB0007 -> L0008 -> T0009).
-- Two additive JSON-in-TEXT columns:
--   question_items.options   — 선택지 배열 [{"id","label"}]. 표시 순서 = 배열 순서.
--   answers.selected_options — 선택된 option id 배열. v1은 단일선택이라 원소 0~1개이나,
--                              복수선택 확장 시 스키마 무변경을 위해 배열형으로 둔다.
-- DB CHECK는 두지 않는다: JSON 유효성 검사에 3방언 공통 문법이 없다. 유효성은 서버 단일
-- 기입 창구(q_service)에서 강제한다. 기존 행은 DEFAULT '[]'로 자동 백필된다.

ALTER TABLE question_items ADD COLUMN options TEXT NOT NULL DEFAULT '[]';
ALTER TABLE answers ADD COLUMN selected_options TEXT NOT NULL DEFAULT '[]';
