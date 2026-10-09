import { inspectorRegistry } from '../../inspectorRegistry'
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import EpsilonTab from './EpsilonTab.vue'

export default {
  key: 'epsilon',
  order: 800,
  requiresDocument: true,
  tab: { key: 'documents', component: EpsilonTab },
  messages,
} satisfies InspectorRegistration

void inspectorRegistry
