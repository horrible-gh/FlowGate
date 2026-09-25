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

T0010에서 구현한 4차(마지막) 공통부품입니다: `SplitPane`(두 영역 사이 경계를 드래그/화살표키로
움직이는 레이아웃 primitive, `#first`/`#second` 슬롯, `direction="horizontal|vertical"`),
`ResizablePane`(단일 패널의 한쪽 edge만 움직이는 독립 primitive, 상대 영역의 의미를 모름),
`ContextMenu`(우클릭/키보드로 포인터 위치에 여는 비차단 메뉴, Dropdown과 달리 trigger element가
아니라 pointer 좌표 기준). 셋 다 어떤 pane/menu 항목이 Designer Explorer인지 Git history인지
알지 못하며, 자기 크기/위치를 localStorage나 API에 저장하지 않습니다. SplitPane과
ResizablePane의 드래그+키보드 스텝+min/max clamp+listener 수명주기는 실제로 동일한 코드였으므로
`../core/interaction/axisResize.ts`로 뺐고(둘의 API 자체는 T0010 §7에 따라 하나로 합치지
않았습니다), 드래그 핸들의 두께/hover/active 색도 `--fg2-splitter-*` 공유 토큰으로 뺐습니다
(`../app/styles/theme.css`). ContextMenu는 `core/overlay/zIndex.ts`의 `allocateOverlayZIndex()`를
그대로 재사용합니다 — 이 과정에서 `ModalShell`이 surface z-index를 `overlayZIndex + 1`로
로컬 계산만 하고 공유 카운터를 실제로 두 번 소비하지 않던 것을 발견해, ModalShell도 surface에
대해 카운터를 한 번 더 호출하도록 최소 수정했습니다(그 전에는 Modal 내부에서 연 ContextMenu가
같은 값으로 계산되어 surface와 동일 z-index로 겹칠 수 있었습니다). 같은 이유로 ModalShell의
document `focusin` 트랩도, 포커스 대상이 이 모달보다 높은 z-index 아래에 있으면(=이 모달이
열린 뒤에 allocateOverlayZIndex()로 할당된 무언가, 즉 Modal 안에서 연 ContextMenu) 다시
끌어오지 않도록 예외를 추가했습니다.
