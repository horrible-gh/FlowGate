// flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings: 용도 · Git 반영).
// Check cards (AC) in every ownership, with or without a document. 0048 registered no settings for AC;
// v7 puts 용도 (under discussion) and the Git 반영 card here, so they are drawn — display only, with the
// 토론 중 tag and the reason nothing can change (the Git choice is asked by the final approval, T#12).
import type { InspectorRegistration } from '../../inspectorRegistry'
import CheckSettings from './CheckSettings.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'checkSettings',
  order: 903,
  cardKinds: ['check'],
  requiresDocument: false,
  tab: { key: 'settings', component: CheckSettings },
  messages,
}

export default registration
