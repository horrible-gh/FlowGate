import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import SampleTab from './SampleTab.vue'

export default {
  key: 'sample',
  order: 900,
  requiresDocument: true,
  tab: { key: 'documents', component: SampleTab },
  messages,
} satisfies InspectorRegistration
