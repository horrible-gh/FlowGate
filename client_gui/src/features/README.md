# features

업무 기능 / 상태 / API 호출 / 기능 UI를 소유하는 경계입니다 (T0005 §4).

`project/`는 flowgate.v02.0008 T#1/T#2에서 추가된 Project Feature입니다
(Project 모델/type, `@shared/api` 기반 API adapter, Pinia store, current-project
localStorage persistence, ProjectDialog/ProjectList/ProjectRow/ProjectCreateDialog 화면 —
`features/project/index.ts`가 공개 경계이며 `useProjectStore`/타입/`ProjectDialog`만
내보냅니다). ProjectList/ProjectRow/ProjectCreateDialog는 이 경계 내부 구현이며 App은
직접 참조하지 않습니다. App Shell(Sidebar/Dashboard) 진입 연결(`openDialog()` 호출)은
아직 없으며 후속 T#3에서 추가됩니다.

flowgate.v02.0023에서 `project/`에 프로젝트 그룹 트리(`/api/v1/projects/{id}/groups/tree`)
조회 API(`fetchProjectGroupTree`)와 store 캐시(`fetchGroupTree`/`getCachedGroupTree`,
읽기 전용 `currentProjectId`)를 추가했습니다. Designer 전용 store를 따로 두지 않고 Designer의
Project Explorer와 File Explorer(0024)가 같은 프로젝트 문맥을 함께 씁니다. flowgate.v02.0024
T0005에서는 File Explorer용 읽기 전용 Flow 파일 문맥(프로젝트 Git status, Flow tree, Base 기준
changes, 단일 파일 diff, AI activity) API와 store 로더(`loadProjectGitStatus`/`loadFlowFileTree`/
`loadFlowChanges`/`loadFlowFileDiff`/`loadFlowAiActivity`)를 추가했습니다.

`designer/`는 v0.2 Designer 부품을 둡니다. `designer/projectExplorer/`가 Project > Group >
Flow 탐색 부품(ProjectExplorer/ProjectTree/ProjectRow/GroupRow/FlowRow, 공통 행 프레임
ExplorerRowFrame, 표시 트리 파생 순수 로직 projectExplorerTree.ts)이고, `designer/fileExplorer/`가
flowgate.v02.0024 T0005에서 추가된 좌측 File Explorer 부품(DesignerFileExplorer와
Toolbar/ScopeHeader/Filter/Tree/FolderRow/FileRow, 좌측 Dock 탐색기들이 같이 쓰는 얇은 행
primitive `shared/ExplorerRowFrame`·`TreeCaret`)입니다. designer는 자체 API/store를 두지 않고
`features/project`의 공개 경계(store와 타입)만 사용합니다. `designer/index.ts`가 공개
경계이며 `ProjectExplorer`, `DesignerFileExplorer`, `DesignerShell`을 내보냅니다.

flowgate.v02.0025에서 `designer/shell/`에 Designer 화면 골격(Designer Shell)을 추가했습니다.
`DesignerShell`이 `/app/designer` route 화면이며 `Top + Body(Left + Center + Right)`,
Center 안에 `Workspace + Bottom`을 둡니다(DesignerTop/DesignerBody/DesignerLeftDock/
ExplorerHeader/ExplorerModeTabs/DesignerCenterColumn/DesignerWorkspace/DesignerBottomDock/
DesignerRightDock, Placeholder 내용 부품 ShellPlaceholder, Layout 상태 정의 designerLayout.ts).
좌측 Dock은 0023 Project Explorer와 0024 File Explorer를 그대로 재사용해 [프로젝트]/[파일]
탭과 접기/펼치기로 조립하고, Top/Workspace/Bottom/Right는 최종 컨테이너 안에 Placeholder만
둡니다. Layout 상태와 활성 Flow는 DesignerShell 인스턴스가 소유하며(Pinia/저장 없음), Shell은
API를 직접 호출하지 않습니다. Canvas, Flow Tabs, Inspector, Palette, Git, Resize 등은 후속 작업
대상입니다.

Git / AI Settings / Environment Settings 등 나머지 기능 이식은 별도 그룹의 후속 작업
대상입니다.
