// D0007 §6: the gate owns the document result box. No confirm/reject header action.
import type { InspectorRegistration } from '../../inspectorRegistry'
import GateSlots from './GateSlots.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'gateCard',
  order: 405,
  cardKinds: ['other'],
  requiresDocument: false,
  tab: { key: 'documents', component: GateSlots },
  messages,
}

export default registration
