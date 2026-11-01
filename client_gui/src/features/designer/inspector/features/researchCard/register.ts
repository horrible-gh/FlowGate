// flowgate.v02.0051 stage 2 — the 조사 card's own parts (0035 v7 research.*): the 지시서 (N) + 레포트 (NR)
// slot lines in the documents tab and the run-card items of the ⋯ menu. Research cards in every
// ownership, with or without a bound document (0051-R §2.3: a Graph Node without a document still
// shows the card's structure — empty lines, disabled items). No header run button: a run starts from the Run
// menu (다음 단계 · 전체 실행) only; the run settings stay the settings feature's.
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import ResearchMenu from './ResearchMenu.vue'
import ResearchSlots from './ResearchSlots.vue'

const registration: InspectorRegistration = {
  key: 'researchCard',
  order: 402,
  cardKinds: ['research'],
  requiresDocument: false,
  tab: { key: 'documents', component: ResearchSlots },
  headerMenu: ResearchMenu,
  messages,
}

export default registration
