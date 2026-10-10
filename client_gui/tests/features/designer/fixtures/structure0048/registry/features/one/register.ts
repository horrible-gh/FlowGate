import messages from './messages'
import OneTab from './OneTab.vue'

export default {
  key: 'one',
  order: 10,
  requiresDocument: true,
  tab: { key: 'documents', component: OneTab },
  messages,
}
