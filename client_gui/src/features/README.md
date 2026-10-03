# features

업무 기능 / 상태 / API 호출 / 기능 UI를 소유하는 경계입니다 (T0005 §4).

`project/`는 flowgate.v02.0008 T#1/T#2에서 추가된 Project Feature입니다
(Project 모델/type, `@shared/api` 기반 API adapter, Pinia store, current-project
localStorage persistence, ProjectDialog/ProjectList/ProjectRow/ProjectCreateDialog 화면 —
`features/project/index.ts`가 공개 경계이며 `useProjectStore`/타입/`ProjectDialog`만
내보냅니다). ProjectList/ProjectRow/ProjectCreateDialog는 이 경계 내부 구현이며 App은
직접 참조하지 않습니다. App Shell(Sidebar/Dashboard) 진입 연결(`openDialog()` 호출)은
아직 없으며 후속 T#3에서 추가됩니다.

`designer/`는 flowgate.v02.0024 T0005에서 추가된 Designer 좌측 File Explorer 부품입니다
(`fileExplorer/`의 DesignerFileExplorer와 Toolbar/ScopeHeader/Filter/Tree/FolderRow/FileRow,
좌측 Dock 탐색기들이 같이 쓰는 얇은 행 primitive `shared/ExplorerRowFrame`·`TreeCaret`).
자체 API/store를 두지 않고 `features/project`의 공개 경계(store의 Flow 파일 로더와 타입)만
사용합니다. `features/designer/index.ts`는 `DesignerFileExplorer`만 내보냅니다. 좌측 Dock 조립,
Project Explorer, Canvas는 아직 없습니다.

Git / AI Settings / Environment Settings 등 나머지 기능 이식과 Designer 화면 조립은 별도
그룹의 후속 작업 대상입니다.
