// flowgate.v02.0051 stage 2 — the 설계 card's own part: the v7 "설명 대기" note and the bound document
// as one slot line, in the documents tab. Design cards (DS · D · P · L · DB) in every ownership, with or
// without a bound document. What else the 설계 card holds is not decided (0035 v7 §4, 0051 turn 7), so
// nothing else is drawn; the common actions, run settings, Q&A and attachments stay their features'.
import type { InspectorRegistration } from '../../inspectorRegistry'
import DesignSlots from './DesignSlots.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'designCard',
  order: 404,
  cardKinds: ['design'],
  requiresDocument: false,
  tab: { key: 'documents', component: DesignSlots },
  messages,
}

export default registration
