# features

업무 기능 / 상태 / API 호출 / 기능 UI를 소유하는 경계입니다 (T0005 §4).

`project/`는 flowgate.v02.0008 T#1/T#2에서 추가된 Project Feature입니다
(Project 모델/type, `@shared/api` 기반 API adapter, Pinia store, current-project
localStorage persistence, ProjectDialog/ProjectList/ProjectRow/ProjectCreateDialog 화면 —
`features/project/index.ts`가 공개 경계이며 `useProjectStore`/타입/`ProjectDialog`만
내보냅니다). ProjectList/ProjectRow/ProjectCreateDialog는 이 경계 내부 구현이며 App은
직접 참조하지 않습니다. App Shell(Sidebar/Dashboard) 진입 연결(`openDialog()` 호출)은
아직 없으며 후속 T#3에서 추가됩니다.

`settings/`는 flowgate.v02.0020에서 추가된 Environment Settings(환경 설정) Feature입니다
(시안 11~14: 워크스페이스 목록/상세, 시스템 › 사용자 / 보안 — `features/settings/index.ts`가
라우팅되는 화면만 내보냅니다).

Designer / Git 등 나머지 기능 이식은 별도 그룹의 후속 작업 대상입니다.
