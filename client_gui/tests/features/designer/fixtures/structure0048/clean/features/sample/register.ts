import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import SampleTab from './SampleTab.vue'

export default {
  key: 'sample',
  order: 410,
  requiresDocument: true,
  cardKinds: ['test'],
  docTypes: ['TS', 'TSR'],
  ownerships: ['v01', 'graph'],
  tab: { key: 'documents', component: SampleTab },
  messages,
} satisfies InspectorRegistration
