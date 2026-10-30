import type { InspectorRegistration } from '../../inspectorRegistry'
import AiTab from './AiTab.vue'
import messages from './messages'
const registration: InspectorRegistration = {
  key: 'ai', order: 910, graphOnly: true, settingsKinds: ['run', 'test', 'plan'], requiresDocument: false,
  tab: { key: 'ai', component: AiTab }, messages,
}
export default registration
