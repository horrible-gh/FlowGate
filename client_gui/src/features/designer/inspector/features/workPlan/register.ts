// flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §6.3 order 410, §10.1 WP 요약 column):
// work plan cards, every ownership, on a Node with a bound document. Stacked above the documents list
// (T#5, order 500). Editing the plan is ↗ 열기 into the v0.1 WorkPlanEditor; ⇉ / 재전개 / ⟳ 재계획 and their
// applied state are not registered (WP 전개, D#1 §8.4).
import type { InspectorRegistration } from '../../inspectorRegistry'
import WorkPlanTab from './WorkPlanTab.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'workPlan',
  order: 410,
  cardKinds: ['plan'],
  requiresDocument: true,
  tab: { key: 'documents', component: WorkPlanTab },
  messages,
}

export default registration
