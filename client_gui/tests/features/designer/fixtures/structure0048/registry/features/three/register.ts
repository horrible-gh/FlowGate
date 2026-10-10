import messages from './messages'
import ThreeTab from './ThreeTab.vue'

export default {
  key: 'three',
  order: 30,
  requiresDocument: true,
  cardKinds: ['test'],
  docTypes: ['TS', 'WP'],
  ownerships: ['legacy'],
  tab: { key: 'documents', component: ThreeTab },
  messages,
}
