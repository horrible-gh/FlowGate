// flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §6.3 order 410, §10.1 WP summary column):
// work plan cards, every ownership, on a Node with a bound document. Stacked above the documents list
// (T#5, order 500). Editing the plan is ↗ 열기 into the work plan edit window.
// flowgate.v02.0051 stage 2 (0035 v7 plan.m.*): the documents tab is the two v7 slot lines — 작업계획 (WP)
// and the deployment result — above the 0048 Step summary; the header gets ⇉ deployment · ⇉ redeployment ▾. A plan Node without a
// document still shows the card's shape (requiresDocument false; the read waits for a document).
// flowgate.v02.0074 T#1 (R §1.2): no header badge (the human writer badge is gone). flowgate.v02.0074 T#5: the header
// buttons deploy the plan (WorkPlanActions, useWorkPlanDeploy; the T#4 deploy API).
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import WorkPlanTab from './WorkPlanTab.vue'

const registration: InspectorRegistration = {
  key: 'workPlan',
  order: 410,
  cardKinds: ['plan'],
  requiresDocument: false,
  tab: { key: 'documents', component: WorkPlanTab },
  messages,
}

export default registration
