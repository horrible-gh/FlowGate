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
primitive `shared/ExplorerRowFrame`·`TreeCaret`, 두 탐색기가 같이 쓰는 Project/Group·폴더/Flow
아이콘 `shared/ExplorerIcon`)입니다. designer는 자체 API/store를 두지 않고
`features/project`의 공개 경계(store와 타입)만 사용합니다. `designer/index.ts`가 공개
경계이며 `ProjectExplorer`, `DesignerFileExplorer`, `DesignerShell`을 내보냅니다.

flowgate.v02.0025에서 `designer/shell/`에 Designer 화면 골격(Designer Shell)을 추가했습니다.
`DesignerShell`이 `/app/designer` route 화면이며 `Top + Body(Left + Center + Right)`,
Center 안에 `Workspace + Bottom`을 둡니다(DesignerTop/DesignerBody/DesignerLeftDock/
ExplorerModeTabs/DesignerCenterColumn/DesignerWorkspace/DesignerBottomDock/
DesignerRightDock, Placeholder 내용 부품 ShellPlaceholder, Layout 상태 정의 designerLayout.ts).
좌측 Dock은 0023 Project Explorer와 0024 File Explorer를 그대로 재사용해 [프로젝트]/[파일]
탭과 접기/펼치기로 조립하고, Top/Workspace/Bottom/Right는 최종 컨테이너 안에 Placeholder만
둡니다. Layout 상태와 활성 Flow는 DesignerShell 인스턴스가 소유하며(Pinia/저장 없음), Shell은
API를 직접 호출하지 않습니다. Canvas, Flow Tabs, Inspector, Palette, Git, Resize 등은 후속 작업
대상입니다.

flowgate.v02.0025 T0006에서 Designer route는 `meta.fullscreen`으로 App Topbar/Sidebar 없이
전체 화면을 쓰는 전용 workspace가 되었고, 좌측 Dock 헤더는 `ExplorerModeTabs` 한 줄
([프로젝트] [파일] + 접기)로 합쳤습니다. 활성 Flow는 Badge 없이 행 스타일로만 표시하고,
정상 상태에서는 Base 비교 기준·현재 Flow 이름 같은 설명 문구를 노출하지 않습니다.

flowgate.v02.0026에서 `designer/center/`에 Designer Center 1단계(Flow Tabs + 읽기 전용
Canvas)를 추가하고 Workspace Placeholder를 `DesignerCenter`로 바꿨습니다. Center 상태(열린
탭·순서, 활성 Flow, Flow별 Graph 읽기 상태·Viewport·Selection)는 `designerCenterSession.ts`의
Center 세션이 단일 소유하며(Pinia 아님), DesignerShell 인스턴스가 세션을 한 번 만들어 수명을
같이 합니다. Project Explorer 활성 표시·File Explorer 대상 Flow·활성 탭은 모두 세션의 활성
FlowRef 하나를 봅니다. Graph는 `graph/flowGraphSource.ts` 경계로 받으며, 서버 Graph API가 생기기
전까지 기본 공급원은 샘플 공급원 `graph/fixtureFlowGraphSource.ts`입니다(Canvas에 "샘플 데이터"
배지). Canvas 엔진은 `@vue-flow/core`(1.48.2 정확 고정)이고 import는 `center/canvas/` 안에서만
하며, FlowGate Graph Model은 `canvas/vueFlowMapping.ts`로 단방향 변환해 그립니다. Node/Edge 모양은
FlowGate 전용 `FlowNodeCard`/`FlowEdgePath`이고 라이브러리 기본 테마는 쓰지 않습니다. 편집(이동·
연결·저장), Inspector, Minimap, Toolbar는 후속 작업 대상입니다.

flowgate.v02.0030에서 `designer/palette/`에 Designer 우측 Palette 부품(`PalettePanel`, 카드
`PaletteCard`, 종류 추가 다이얼로그 `PaletteAddDialog`, 순수 모델 `paletteCatalog.ts`, 글리프 표
`paletteIcons.ts`)을 추가했습니다. 기본 항목(설계 / 업무 플로우 / 제어)은 시안 03 기준이며, 항목의
`nodeKind`는 Canvas Graph Model의 Node kind 키(`center/canvas/flowNodeKinds.ts`)를, `docType`은
FlowGate 문서 타입 코드를 재사용합니다. Palette는 항목 선택(`select`)과 drag 시작(`drag-start`,
DataTransfer `application/x-flowgate-palette-item`) 의도만 내보내고 Canvas drop·노드 생성은 하지
않습니다. 사용자 정의 항목은 `v-model:custom-items`로 상위가 소유·영속화할 수 있으며(기존 설정 계약
없음), 바인딩하지 않으면 세션 동안 부품이 보관합니다. `팔레트 / 대화 / Git` 탭과 Tool Panel
Container는 후속 그룹이 조립하므로 Shell에는 아직 연결하지 않았고 공개 경계에도 내보내지 않습니다.

`conversation/`은 flowgate.v02.0031에서 추가된 Designer 우측 도구 영역용 Conversation 독립
부품입니다(시안 03 우측 대화 영역). `ConversationPanel`(`:project-id` + 활성 Flow의
`:group-id`, `@open-change`)이 공개 경계이며, 상단(채팅 목록 / 수정 가능한 제목 / 새 채팅) ·
하나의 시간순 타임라인(사용자/AI 메시지, 명령 실행 요청, 작업 변경, 상태 이벤트) · 참여자 ·
하단 입력(대화 설정, AI 소스 모드 읽기/편집/1회 편집, 멘션 복사, AI 호출, AI 선택, 전송/중지)을
담습니다. 상태는 패널 수명의 `conversationSession.ts` 세션이 단일 소유하고(Pinia 아님), 서버
호출은 `conversationSource.ts` 경계 뒤의 `api.ts`가 v0.1 ConversationView/MainPanel이 이미 쓰는
계약(CH 문서 목록·제목 PATCH, conversation turns/turn/read, ai-invoke, me/chat-settings,
token/issue)과 flowgate.v02.0032의 Conversation production 계약을 사용합니다. 0032에서 기본 HTTP
source를 이 계약에 연결했습니다: [새 채팅]은 workflow head와 무관한 standalone CH를
`POST /api/v1/conversations`로 만들고(next-empty 미사용), 명령 실행 요청·run 단위 작업 변경은
`GET /api/v1/chat-activity/{doc_id}`로 읽어 서버 anchor 위치에 놓으며, 승인/거절은
`POST /api/v1/chat-commands/{request_id}/decision`으로 보냅니다. 실시간 구독 계층은 아직 없어
AI 호출 polling tick(2.5초)마다, 호출이 끝난 직후, 결정 직후에 activity를 다시 읽습니다
(`subscribeActivities` 미구현). 작업 변경 카드의 파일을 누르면 서버가 저장한 run 시작/종료 내용의
diff를 카드 아래에 엽니다. 탭 전환·Tool Panel Container·Palette·Git·Designer 조립은 이 부품에
포함하지 않습니다.

`settings/`는 flowgate.v02.0020에서 추가된 Environment Settings(환경 설정) Feature입니다
(시안 11~14: 워크스페이스 목록/상세, 시스템 › 사용자 / 보안 — `features/settings/index.ts`가
라우팅되는 화면만 내보냅니다).

Git 등 나머지 기능 이식은 별도 그룹의 후속 작업 대상입니다.
