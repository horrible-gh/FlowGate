// flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's settings tab (0035 v7 reqSettings: AI 위임).
// Requirement cards (R / B) in every ownership, with or without a document. 0048 registered no settings
// for R / B (D#2 §2.4 rule 3: AI delegation is not a v0.1 sequence setting); v7 puts the AI 위임 block
// here, so the block is drawn — disabled, with its reason — until a delegation API exists (0051-R §2.3).
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import RequirementSettings from './RequirementSettings.vue'

const registration: InspectorRegistration = {
  key: 'requirementSettings',
  order: 901,
  cardKinds: ['requirement'],
  requiresDocument: false,
  tab: { key: 'settings', component: RequirementSettings },
  messages,
}

export default registration
