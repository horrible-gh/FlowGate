// flowgate.v02.0048 T#8 — the attachments tab with its tab number (D#1 §6.3 order 800, §10.1 attachments
// column): every card kind and ownership, on a Node with a bound document — the attachments belong to
// that document (D#1 §10.3). A Node without one shows no tab (D#1 §4.4).
import type { InspectorRegistration } from '../../inspectorRegistry'
import AttachmentsTab from './AttachmentsTab.vue'
import messages from './messages'
import { useAttachmentsCount } from './useAttachments'

const registration: InspectorRegistration = {
  key: 'attachments',
  order: 800,
  requiresDocument: true,
  tab: { key: 'attachments', component: AttachmentsTab, count: useAttachmentsCount },
  messages,
}

export default registration
