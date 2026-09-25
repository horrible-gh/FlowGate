# components

도메인 의미가 없는 공용 UI만 이 디렉터리에 둡니다 (T0005 §4).
특정 기능/화면에서 1회만 쓰이는 UI는 이곳이 아니라 해당 features/* 또는 app/*가 소유합니다.

## ui/

T0004에서 구현한 1차 App Shell 전역 UI Primitive입니다: `Button`, `IconButton`, `Input`,
`Textarea`, `Select`, `Toggle`, `Tabs`, `Badge`, `Tooltip`, `Dropdown`. 각 컴포넌트는 시각/
interaction 계약만 소유하며, API·store·라우터·도메인 상태를 직접 참조하지 않습니다. 도메인
상태 → 색상/라벨 매핑(예: Git 충돌 상태, 승인 상태)은 이 디렉터리가 아니라 그 상태를 아는
feature가 소유합니다.

T0006에서 구현한 2차 Overlay Shell입니다: `ModalShell`(차단형, Teleport+backdrop+ESC+focus
trap/return), `ModelessShell`(비차단형, drag/resize/minimize, backdrop 없음, focus trap
없음), `ConfirmDialog`와 `AlertDialog`(둘 다 ModalShell 위에 구축). 네 컴포넌트 모두 Git/AI/
Project/Workflow 같은 실제 기능 Dialog가 아니라 domain-agnostic 기반만 제공하며, 업무
문구는 호출 측(title/message/label prop)이 소유합니다. `../core/overlay/`에는 둘 이상이
실제로 중복하는 z-index 할당과 focus 복귀만 공유 기반으로 뺐습니다(focus trap은
ModalShell만 쓰므로 내부에 남아있습니다).

Toast/Alert/EmptyState/LoadingState/ErrorState/SplitPane/ResizablePane/ContextMenu 등 나머지
UI Primitive Global 후보는 후속 T에서 이 디렉터리에 추가됩니다.
