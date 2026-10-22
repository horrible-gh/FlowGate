// 0083 WP-2: card recovery belongs to the Inspector documents tab.
import type { InspectorRegistration } from '../../inspectorRegistry'
import CardRecovery from './CardRecovery.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'cardRecovery', order: 595, ownerships: ['graph', 'conflict'], requiresDocument: false,
  tab: { key: 'documents', component: CardRecovery }, messages,
}
export default registration
