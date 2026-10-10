// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's own part (0035 v7 req.*): the one document
// slot line in the documents tab. Requirement cards (R / B) in every ownership, with or without a bound
// document. The 종류 배지 ▾ is the kindSwitch feature's, ✓ 승인 the actions feature's, and the AI 위임
// settings the requirementSettings feature's.
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import RequirementSlots from './RequirementSlots.vue'

const registration: InspectorRegistration = {
  key: 'requirementCard',
  order: 401,
  cardKinds: ['requirement'],
  requiresDocument: false,
  tab: { key: 'documents', component: RequirementSlots },
  messages,
}

export default registration
