import messages from './messages'
import TscRunnerTab from './TscRunnerTab.vue'

export default {
  key: 'tscRunner',
  order: 40,
  requiresDocument: true,
  tab: { key: 'documents', component: TscRunnerTab },
  messages,
}
