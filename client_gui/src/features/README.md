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
Project Explorer와 File Explorer(0024)가 같은 프로젝트 문맥을 함께 씁니다.

`designer/`는 v0.2 Designer 부품을 둡니다. `designer/projectExplorer/`가 Project > Group >
Flow 탐색 부품(ProjectExplorer/ProjectTree/ProjectRow/GroupRow/FlowRow, 공통 행 프레임
ExplorerRowFrame, 표시 트리 파생 순수 로직 projectExplorerTree.ts)이며
`designer/index.ts`가 공개 경계입니다. Designer 화면 조립(좌측 Dock, 중앙 Canvas 등)은
후속 작업 대상입니다.

Git / AI Settings / Environment Settings 등 나머지 기능 이식은 별도 그룹의 후속 작업
대상입니다.
