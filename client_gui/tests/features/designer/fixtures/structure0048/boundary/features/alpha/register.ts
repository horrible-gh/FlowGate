import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import AlphaTab from './AlphaTab.vue'

export default {
  key: 'alpha',
  order: 500,
  requiresDocument: true,
  tab: { key: 'documents', component: AlphaTab },
  messages,
} satisfies InspectorRegistration
