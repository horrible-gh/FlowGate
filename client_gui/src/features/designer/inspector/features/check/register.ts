// flowgate.v02.0048 T#12 — the check card (D#1 §6.3 order 430, §10.1 헤더 액션 · 확인·Git columns): AC
// cards in every ownership, on a Node with a bound document (an AC Node exists only once its document
// does). Two places with the same order: the final approval in the header and the check section of
// the documents tab (check state, Git reflection, the Git panel). Graph gate confirm / reject and
// other uses of the check card are not registered (D#1 §8.4).
import type { InspectorRegistration } from '../../inspectorRegistry'
import CheckAction from './CheckAction.vue'
import CheckTab from './CheckTab.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'check',
  order: 430,
  cardKinds: ['check'],
  requiresDocument: true,
  tab: { key: 'documents', component: CheckTab },
  headerAction: CheckAction,
  messages,
}

export default registration
