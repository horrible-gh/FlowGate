// flowgate.v02.0048 T#5 — the documents tab (D#1 §6.3 order 500, §10.1 문서 탭 column): every
// card kind and ownership, on a Node with a bound document. A Node without one shows no tab
// (D#1 §4.4); writing an empty slot is done in the v0.1 screen.
import type { InspectorRegistration } from '../../inspectorRegistry'
import DocumentsTab from './DocumentsTab.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'documents',
  order: 500,
  requiresDocument: true,
  tab: { key: 'documents', component: DocumentsTab },
  messages,
}

export default registration
