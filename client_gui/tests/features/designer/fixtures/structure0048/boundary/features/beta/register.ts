import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import BetaTab from './BetaTab.vue'

export default {
  key: 'beta',
  order: 600,
  requiresDocument: true,
  tab: { key: 'documents', component: BetaTab },
  messages,
} satisfies InspectorRegistration
