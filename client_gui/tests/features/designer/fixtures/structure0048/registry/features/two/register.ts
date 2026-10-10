import messages from './messages'
import TwoTab from './TwoTab.vue'

export default {
  key: 'one',
  order: 10,
  requiresDocument: true,
  tab: { key: 'documents', component: TwoTab },
  messages,
}
