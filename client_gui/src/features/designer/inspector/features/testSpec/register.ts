// flowgate.v02.0048 T#11 — the test section of the documents tab (D#1 §6.3 order 420, §10.1 테스트 column):
// test cards of type TS and TSR, every ownership, on a Node with a bound document. V shares the `test`
// card kind but is left out here by `docTypes` (D#1 §4.3, §5.3 rule 6) — never hidden inside the
// section. The Graph engine's TS Node run is not registered (Graph 실행 연동, D#1 §8.4).
// flowgate.v02.0053 T#5 — also on a test card without a bound document: its 지시서 · 시나리오 · 레포트 lines are
// the card's documents (made when it was placed), so the card never shows an empty documents tab.
import type { InspectorRegistration } from '../../inspectorRegistry'
import TestSpecTab from './TestSpecTab.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'testSpec',
  order: 420,
  cardKinds: ['test'],
  docTypes: ['TS', 'TSR'],
  requiresDocument: false,
  tab: { key: 'documents', component: TestSpecTab },
  messages,
}

export default registration
