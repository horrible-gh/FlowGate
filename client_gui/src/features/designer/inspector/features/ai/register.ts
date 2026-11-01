import type { InspectorRegistration } from '../../inspectorRegistry'
import AiTab from './AiTab.vue'
import messages from './messages'
// flowgate.v02.0113 T#4 (0030-M §1, D 개정 §6.3): the AI tab sits between 문서 (500) and Q&A (700).
const registration: InspectorRegistration = {
  key: 'ai', order: 550, graphOnly: true, settingsKinds: ['run', 'test', 'plan'], requiresDocument: false,
  tab: { key: 'ai', component: AiTab }, messages,
}
export default registration
