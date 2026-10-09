// flowgate.v02.0048 T#6 — the header actions (D#1 §6.3 order 600, §10.1 헤더 액션 column): cards with a
// review decision (R/B, N/NR, T…, DS…, TS/TSR/V, WP) in every ownership, on a Node with a bound
// document. AC's final approval is the check card's (T#12); other kinds have no header action.
// Which buttons a card shows (v0.1 run / rework on v01 work cards only) is actionsModel's.
import type { InspectorRegistration } from '../../inspectorRegistry'
import ActionsBar from './ActionsBar.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'actions',
  order: 600,
  cardKinds: ['requirement', 'research', 'task', 'design', 'test', 'plan'],
  requiresDocument: true,
  headerAction: ActionsBar,
  messages,
}

export default registration
