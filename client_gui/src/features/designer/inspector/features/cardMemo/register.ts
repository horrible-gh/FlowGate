// flowgate.v02.0056 F2-2 — the card's memos in the documents tab (O3 0009-TR §4-5): v0.2 memos of a card, not the
// v0.1 M document of the header ✎ 메모 (features/memo). Every stored Graph card (a draft card too — a draft card and
// the confirmed card share their memos); a Node without a definition or a card not saved yet draws nothing. After
// the card's document lines, before the card approval bar. Only Flows with Graph cards (`unassigned` drafts,
// `graph`, and `conflict` — listed read only): a v0.1 Flow's Node keeps its documents tab as it was.
import type { InspectorRegistration } from '../../inspectorRegistry'
import CardMemoSection from './CardMemoSection.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'cardMemo',
  order: 580,
  ownerships: ['unassigned', 'graph', 'conflict'],
  requiresDocument: false,
  tab: { key: 'documents', component: CardMemoSection },
  messages,
}

export default registration
