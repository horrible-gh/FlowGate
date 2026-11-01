// flowgate.v02.0048 T#6 — the header actions (D#1 §6.3 order 600, §10.1 헤더 액션 column), on a Node with a
// bound document. AC's final approval is the check card's (T#12).
// flowgate.v02.0056 F2-1 — the document approve / reject buttons are removed from the header (the user's request:
// no row of buttons there; a v0.2 card is approved with its own [승인] — the Canvas card's state chip and the
// documents tab's approval bar, cardApproval — and has no user [반려]). What stays is the v0.1 single run /
// rework of a v0.1 Flow's work cards (조사 · 작업 · 설계), so the feature applies to those only. The gate's
// ✓ 확인 · ✕ 반려 are the gate card's own (gateCard), untouched.
import type { InspectorRegistration } from '../../inspectorRegistry'
import ActionsBar from './ActionsBar.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'actions',
  order: 600,
  cardKinds: ['research', 'task', 'design'],
  ownerships: ['v01'],
  requiresDocument: true,
  headerAction: ActionsBar,
  messages,
}

export default registration
