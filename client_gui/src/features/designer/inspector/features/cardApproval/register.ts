// flowgate.v02.0056 F2-1 — the card approval bar at the bottom of the documents tab (0056 CH turn 5~10, O1
// 0005-TR §4-1): a Graph card's state (작업 중 · 승인 대기 · 승인됨) and, while it waits for its approval,
// [승인]. Not in the Inspector header (the user's request: no row of buttons there); the Canvas card's state
// chip offers the same [승인]. Every card kind but the v0.1 check (AC) — a gate draws nothing (✓ 확인 · ✕ 반려
// is its own), nor does a Node without a Graph run. No document needed: the approval is the card's.
// Only Graph-owned Flows (`graph`, and `conflict`, where it is drawn read only) have card runs; a v0.1 Flow's Node
// keeps its documents tab as it was (a part of the tab would hide the v0.1 document metadata there).
import type { InspectorRegistration } from '../../inspectorRegistry'
import CardApprovalBar from './CardApprovalBar.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'cardApproval',
  order: 590,
  cardKinds: ['requirement', 'research', 'task', 'design', 'test', 'plan', 'other'],
  ownerships: ['graph', 'conflict'],
  requiresDocument: false,
  tab: { key: 'documents', component: CardApprovalBar },
  messages,
}

export default registration
