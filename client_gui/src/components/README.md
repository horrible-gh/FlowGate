# components

도메인 의미가 없는 공용 UI만 이 디렉터리에 둡니다 (T0005 §4).
특정 기능/화면에서 1회만 쓰이는 UI는 이곳이 아니라 해당 features/* 또는 app/*가 소유합니다.

## ui/

T0004에서 구현한 1차 App Shell 전역 UI Primitive입니다: `Button`, `IconButton`, `Input`,
`Textarea`, `Select`, `Toggle`, `Tabs`, `Badge`, `Tooltip`, `Dropdown`. 각 컴포넌트는 시각/
interaction 계약만 소유하며, API·store·라우터·도메인 상태를 직접 참조하지 않습니다. 도메인
상태 → 색상/라벨 매핑(예: Git 충돌 상태, 승인 상태)은 이 디렉터리가 아니라 그 상태를 아는
feature가 소유합니다.

Modal/Modeless/ConfirmDialog/AlertDialog/Toast/Alert/EmptyState/LoadingState/ErrorState/
SplitPane/ResizablePane 등 나머지 UI Primitive Global 후보는 후속 T에서 이 디렉터리에
추가됩니다.
