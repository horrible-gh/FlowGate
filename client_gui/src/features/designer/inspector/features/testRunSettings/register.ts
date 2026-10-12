// flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings) in the settings tab after
// the settings feature's (order 902 > 900).
// flowgate.v02.0053 T#7 — the 실행 block (per-Case execution mode and the Case counts) of every card with test
// settings (inspectorSettingsKind): a Graph test card in any ownership and a v0.1 Flow's test step, with or
// without a document.
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import TestRunSettings from './TestRunSettings.vue'

const registration: InspectorRegistration = {
  key: 'testRunSettings',
  order: 902,
  settingsKinds: ['test'],
  requiresDocument: false,
  tab: { key: 'settings', component: TestRunSettings },
  messages,
}

export default registration
