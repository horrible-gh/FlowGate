// flowgate.v02test.0042 — the '병합 · 충돌' tab (시안 v7, 0005-CH turns 38-40): a card's result merge is handled inside the
// card, never in the Git panel. The tab is the last one and shows only while the card has merge work (its node run's
// integration is pending / integrating / conflict / failed, or the tab is open on work that just ended — ④ 병합 완료);
// blue while it only shows progress, red while a person must choose. Nothing is added to the Documents tab.
import type { InspectorRegistration } from '../../inspectorRegistry'
import MergeTab from './MergeTab.vue'
import messages from './messages'
import { useMergeTabTone, useMergeTabVisible } from './useMerge'

const registration: InspectorRegistration = {
  key: 'merge',
  order: 1500,
  graphOnly: true,
  requiresDocument: false,
  tab: { key: 'merge', component: MergeTab, visible: useMergeTabVisible, tone: useMergeTabTone },
  messages,
}

export default registration
