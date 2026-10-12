// flowgate.v02.0051 stage 2 — the 설계 card's own part in the documents tab. Design cards (DS · D · P · L ·
// DB) in every ownership, with or without a bound document; the common actions, run settings, Q&A and
// attachments stay their features'.
// flowgate.v02.0053 T#5 (0053 CH) — its lines are 지시서 · 레포트 like 조사 · 작업 (the "설명 대기" note is gone).
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
