// flowgate.v02.0053 T#9 — the header ✎ 메모 (R0052 §2.6, v7 `.hd-memo`): on every card with a document, the
// memo of that document is written and opened inside v0.2 — no v0.1 link. The memo is a v0.1 memo document
// (type M) made with the v0.1 API (features/document memo.ts) and shown in the document window (MD Viewer ·
// ✎). Without a document the Frame keeps its dim, disabled ✎ 메모.
import type { InspectorRegistration } from '../../inspectorRegistry'
import MemoButton from './MemoButton.vue'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'memo',
  order: 300,
  requiresDocument: true,
  headerMemo: MemoButton,
  messages,
}

export default registration
