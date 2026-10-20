# v0.2 카드 내부 단계 계약 — T0017 / D0006 개정

최신 반려 rej_01M5C0BVVSBAWARX에 따른 구현 계약이다. D0006 §3.3·3.4·7.7의 설계 single step, 설계 결과의 사전 지시를 독립 DS 구조로 해석하는 부분과 note 미전달 유보를 아래 내용으로 대체한다. 기존 전개 트랜잭션·문서 보호·승인 권한 계약은 유지한다.

## 저장

- v0.2 WP는 `card_stage_model: paired`를 사용한다. 설계 D/P/L/DB 카드마다 공통 card_id를 가진 instruction/result 두 step을 저장한다. 설계 지시의 key는 `DS{설계종류}#{ordinal}`, type은 기존 문서 저장 호환용 DS이며 pair_key는 결과 key다. 결과의 key/type은 기존 값을 보존한다. 독립 DS 카드는 만들지 않는다.
- 지시서와 결과는 provider_id, reviewer_provider_id, review_count, note, 편집 텍스트·첨부를 각각 보유한다. 설계서 연필은 결과 step의 입력을 편집하고 지시서 연필과 공유하지 않는다.
- 기존 single 설계 step은 첫 v0.2 편집·저장 시 pair로 전환한다. card_id와 결과 key, 기존 데이터는 보존한다. 이전 공유 사전 지시는 지시서에 한 번 복사하되 결과 편집 값도 보존하며 이후 수정은 서로 영향을 주지 않는다. 기존 독립 DS 기록은 호환 이력으로 보존하고 새 카드의 지시서로 순번 연결하지 않는다.

## 전개와 실행

- 지시서 AI·검수 AI·검수 횟수·멘트는 instruction_ai / instruction_review_ai / instruction_review_count / instruction_note에, 결과는 run_ai / review_ai / review_count / run_note에 연결한다. 설계 결과의 편집 텍스트·첨부는 run_pre_instruction으로 전달한다.
- 재전개는 시작 전 단계 설정의 변경과 해제를 반영한다. 시작된 카드 설정 변경은 전개를 차단한다. 지시서 생략 변경은 저장·AI 저장·버전 복원 단계에서도 기존 Graph 시작 잠금을 적용한다.
- 지시서에 본문이 없으면 지시 AI로 작성하고, 본문이 있으면 기존 본문을 보존한다. 검수 횟수가 0이 아니면 지시서 자체의 검수·재작업 loop를 실행한다. 검수 반려·실패·소진 시 결과 작성으로 넘어가지 않는다. 검수 통과 후 기존 승인 권한 처리와 결과 실행을 이어간다. 0은 기존 자동 승인 경로다.
- 검수 실행도 같은 Graph attempt에 귀속한다. Stop/오래된 attempt 보호 및 실행 lease를 유지한다. 독립 DS workflow나 별도 카드 엔진은 추가하지 않는다.
- 한줄 멘트는 해당 단계의 AI 입력에만 넣는다. 결과 입력에 지시서 한줄 멘트를 중복 적용하지 않는다.

## 생략과 UI

- D/P/L/DB 및 N/T 모두 instruction step의 instruction_mode=use/omit을 저장한다. use는 두 단계, omit은 결과 한 단계다. 생략해도 지시서·설계서/레포트의 ID·본문·첨부·설정은 삭제하지 않는다. omit 실행은 지시 작성·지시 검수·지시 참조 없이 결과부터 시작한다.
- 이전 omit 선택을 삭제하거나 null로 저장하면 use로 정규화하며 재조회·재전개·실행이 같은 값을 사용한다.
- 설계지시 행은 일반 지시서와 동일한 AI·검수·한줄 멘트·편집 컨트롤을 사용한다. 설계서 연필, 기존 승인 기능 및 지시 작성 바로가기(✓✓)를 유지한다. ✓✓와 실행 단계 생략 체크박스는 기존 의미가 다르므로 통합하지 않는다.

## 검증 경계

`test_work_plan_paired_stages_0074.py`는 실제 저장 라우트·DB·전개·Graph 실행 및 검수 후속 처리와 외부 AI 실행 대역을 사용한다. Vue 컴포넌트 시험은 API fixture를 사용한다. 실제 브라우저+서버 검증 및 외부 AI CLI 성공을 이 시험들의 통과로 대체하지 않는다. 실행 결과와 브라우저 환경 차단은 TR0018에 기록한다.
