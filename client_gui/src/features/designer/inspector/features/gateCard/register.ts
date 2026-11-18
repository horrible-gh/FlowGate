// 0148: the gate keeps its result details and contributes the shared approval header action.
import type { InspectorRegistration } from '../../inspectorRegistry'
import GateSlots from './GateSlots.vue'
import GateActions from './GateActions.vue'
import GateHeader from './GateHeader.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'gateCard',
  order: 405,
  cardKinds: ['other'],
  requiresDocument: false,
  tab: { key: 'documents', component: GateSlots },
  headerAction: GateActions,
  headerState: GateHeader,
  headerStateApplies: (target) => target.nodeType === 'gate',
  messages,
}

export default registration
