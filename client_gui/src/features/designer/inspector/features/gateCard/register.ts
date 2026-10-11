// flowgate.v02.0051 stage 2 — the Graph gate Node's own parts (0035 v7 확인 cards): the 확인 결과 result
// line in the documents tab and ✓ 확인 · ✕ 반려 in the header, drawn disabled (no gate confirm / reject
// API, D#1 §8.4; the gate's role is under discussion, v7 §5). Registered for the `other` card kind — a
// gate has no document type — in every ownership, without a document; the part draws nothing on an
// `other` Node that is neither a gate nor a document Node.
// flowgate.v02.0053 T#5 — a Custom card (Graph `custom` node, also `other`) gets its 지시서 · 레포트 lines here.
import type { InspectorRegistration } from '../../inspectorRegistry'
import GateActions from './GateActions.vue'
import GateSlots from './GateSlots.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'gateCard',
  order: 405,
  cardKinds: ['other'],
  requiresDocument: false,
  tab: { key: 'documents', component: GateSlots },
  headerAction: GateActions,
  messages,
}

export default registration
