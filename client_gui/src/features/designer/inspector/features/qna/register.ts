// flowgate.v02.0048 T#7 — the Q&A tab with its tab number (D#1 §6.3 order 700, §10.1 Q&A column):
// every card kind and ownership, on a Node with a bound document — the questions belong to that
// document (D#1 §10.3). A Node without one shows no tab (D#1 §4.4).
// flowgate.v02.0150 — the ↗ windows are the Frame's windows host (QnaWindowHost): drawn whatever is selected, each on
// the document it was opened for, closed only by its own ×.
import type { InspectorRegistration } from '../../inspectorRegistry'
import QnaTab from './QnaTab.vue'
import QnaWindowHost from './QnaWindowHost.vue'
import messages from './messages'
import { useQnaCount } from './useQna'

const registration: InspectorRegistration = {
  key: 'qna',
  order: 700,
  requiresDocument: true,
  tab: { key: 'qna', component: QnaTab, count: useQnaCount },
  windows: QnaWindowHost,
  messages,
}

export default registration
