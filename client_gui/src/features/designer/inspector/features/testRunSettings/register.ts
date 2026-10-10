// flowgate.v02.0051 stage 2 — the 테스트 card's own settings (0035 v7 testSettings): 지시 (TS) 사용 /
// 생략, 검수 뒤 진행 and the per-Case 실행 block, in the settings tab after the settings feature's run
// settings (order 902 > 900). Test cards (TS / TSR / V) in every ownership, with or without a document.
// Drawn display only until these settings exist on the server (0051-R §2.3).
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import TestRunSettings from './TestRunSettings.vue'

const registration: InspectorRegistration = {
  key: 'testRunSettings',
  order: 902,
  cardKinds: ['test'],
  requiresDocument: false,
  tab: { key: 'settings', component: TestRunSettings },
  messages,
}

export default registration
