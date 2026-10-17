// flowgate.v02.0064 T#4 rework (P0006 §3.3) — 「전환 전 문서」: the header link of a requirement / bug root made by
// an R↔B succession to the document it came from, which keeps the Q&A, the reviews and the runs. A read-only
// link, so it is not the switch's (kindSwitch, unassigned Flows only): it applies on every ownership — the new
// document's own workflow decision turns its Flow v01 — and draws nothing for a document that was not succeeded.
import type { InspectorRegistration } from '../../inspectorRegistry'
import KindSwitchOrigin from './KindSwitchOrigin.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'kindSwitchOrigin',
  order: 611,
  cardKinds: ['requirement'],
  docTypes: ['R', 'B'],
  requiresDocument: true,
  headerMeta: KindSwitchOrigin,
  messages,
}

export default registration
