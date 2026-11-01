// flowgate.v02.0051 stage 2 — the 작업 card's own parts (0035 v7 work.*): the 지시서 (T) + 레포트 (TR)
// slot lines in the documents tab and the run-card items of the ⋯ menu. Task cards in every
// ownership, with or without a bound document (0051-R §2.3: a Graph Node without a document still
// shows the card's structure — empty lines, disabled items). The common header actions (승인 · 반려 ·
// ▶ 실행 · ↻ 재작업) stay the actions feature's; the run settings stay the settings feature's.
import type { InspectorRegistration } from '../../inspectorRegistry'
import messages from './messages'
import TaskMenu from './TaskMenu.vue'
import TaskSlots from './TaskSlots.vue'

const registration: InspectorRegistration = {
  key: 'taskCard',
  order: 403,
  cardKinds: ['task'],
  requiresDocument: false,
  tab: { key: 'documents', component: TaskSlots },
  headerMenu: TaskMenu,
  messages,
}

export default registration
