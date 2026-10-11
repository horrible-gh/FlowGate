// flowgate.v02.0051 stage 2 — the 확인 card's settings tab (0035 v7 confirmSettings: 용도 · Git 반영).
// 0048 registered no settings for AC; v7 puts 용도 (under discussion) and the Git 반영 card here.
// flowgate.v02.0053 T#7 — every card with check settings (inspectorSettingsKind): a Graph gate (확인) card in
// any ownership keeps its values (T#2); a v0.1 Flow's 확인 (AC) Node is drawn without a store.
import type { InspectorRegistration } from '../../inspectorRegistry'
import CheckSettings from './CheckSettings.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'checkSettings',
  order: 903,
  settingsKinds: ['check'],
  requiresDocument: false,
  tab: { key: 'settings', component: CheckSettings },
  messages,
}

export default registration
