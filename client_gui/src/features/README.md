# features

업무 기능 / 상태 / API 호출 / 기능 UI를 소유하는 경계입니다 (T0005 §4).

`project/`는 flowgate.v02.0008 T#1에서 추가된 Project Feature의 기반 계층입니다
(Project 모델/type, `@shared/api` 기반 API adapter, Pinia store, current-project
localStorage persistence — `features/project/index.ts`가 공개 경계입니다). Project
Dialog/Create Dialog 화면과 App Shell(Sidebar/Dashboard) 진입 연결은 아직 없으며
후속 T(T#2/T#3)에서 이 경계 안에 추가됩니다.

Designer / Git / AI Settings / Environment Settings 등 나머지 기능 이식은 별도 그룹의
후속 작업 대상입니다.
