// flowgate.v02.0048 T#9 — the settings tab (D#1 §6.3 order 900, §10.1 설정 column, §10.2 T#9 row; D#2 §2.4
// rule 3): the run settings of a v0.1 sequence step, on a v0.1 Flow only, on the work cards that are
// sequence steps (N/NR · T… · DS… · TS/TSR/V). R/B (AI delegation), AC and WP have none. A step that has not
// started has no document yet, and it is the only one whose settings can change, so the tab does not
// require a document. It does require a sequence slot: a Node without a sequence row (a free document
// on a v0.1 Flow) has no run settings, so it gets no tab (D#2 §2.4 rule 3, D#1 §5.3 rule 6).
import type { InspectorRegistration } from '../../inspectorRegistry'
import SettingsTab from './SettingsTab.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'settings',
  order: 900,
  cardKinds: ['research', 'task', 'design', 'test'],
  ownerships: ['v01'],
  requiresDocument: false,
  requiresSlot: true,
  tab: { key: 'settings', component: SettingsTab },
  messages,
}

export default registration
