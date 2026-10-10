import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import DeltaTab from './DeltaTab.vue'

export default {
  key: 'delta',
  order: 700,
  requiresDocument: true,
  tab: { key: 'documents', component: DeltaTab },
  messages,
} satisfies InspectorRegistration
