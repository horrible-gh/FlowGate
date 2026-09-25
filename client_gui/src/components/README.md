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

T0008에서 구현한 3차 상태/피드백 Primitive입니다: `Toast`(비차단 일시 피드백, `duration`이
지나면 자동으로 `dismiss`를 emit하거나 사용자가 직접 닫을 수 있음), `ToastHost`(여러 Toast를
겹치지 않게 쌓아 보여주는 공용 stacking region — 메시지 큐/전역 store는 호출 측이 소유),
`Alert`(화면에 남아 있는 inline/banner 경고·정보, AlertDialog와 달리 blocking이 아님),
`EmptyState`(정상 상태지만 표시할 데이터가 없음), `LoadingState`(결과를 기다리는 중),
`ErrorState`(화면/패널 단위 오류 상태). 다섯 컴포넌트 모두 문구·발생 조건·retry 로직을
전혀 모르며(Feature가 title/message/slot으로 채움), API·store·router를 직접 참조하지
않습니다. 색상은 Badge(T0004)가 이미 쓰던 info/success/warning/danger tone 값을
`--fg2-status-*` 공유 토큰으로 옮겨 Badge 자신과 Toast가 fg+border+bg 전체를, Alert가
border만, ErrorState가 fg만 함께 재사용합니다(`../app/styles/theme.css`, T0008 §11).
Badge의 neutral tone은 dashed/투명 칩 모양이 이 체계와 달라 토큰화하지 않았습니다.

SplitPane/ResizablePane/ContextMenu는 마지막 공통부품 T로 남아 있습니다.
