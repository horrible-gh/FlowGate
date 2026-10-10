# client_gui/preview — UI 단독 확인용 미리보기 (fixture)

flowgate.v02.0051 — Inspector 부품을 **실제 렌더링**으로 확인하기 위한 개발용 페이지입니다
(0051-R §3.5: fixture/preview는 실제 데이터·기능과 명확히 구분).

- 서버에 접속하지 않습니다. 모든 값은 0035 v7 시안의 예시 값(fixture)이며 실제 프로젝트 데이터가
  아닙니다. 버튼은 보이기만 하고 동작하지 않습니다(⋯ → ID 복사만 실제 클립보드).
- 운영 번들(`vite build`)에는 들어가지 않습니다. 빌드 입력은 `index.html` 하나뿐입니다.
- 보기: `npm run dev` 뒤 `http://127.0.0.1:3001/app/preview/inspector/index.html`
  - `?locale=en|ja` 로 언어를 바꿀 수 있습니다.
  - `?only=<section id>` 로 한 구역만 봅니다 (header · chips · slots · review · origin · blocks ·
    run-settings · pane · frame).

| 경로 | 내용 |
| --- | --- |
| `inspector/index.html` | 진입 HTML |
| `inspector/main.ts` | i18n(공유 인스턴스 + gui 문자열) + 테마만 올리고 마운트 |
| `inspector/fixtures.ts` | v7 시안 예시 값으로 만든 InspectorTarget / 슬롯 / 설정 fixture |
| `inspector/PreviewApp.vue` | 구역별 갤러리 (공용 부품 → 이후 단계에서 Node별 부품·조립 화면 추가) |
