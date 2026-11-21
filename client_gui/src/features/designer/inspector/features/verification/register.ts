// flowgate.v02test.0053 1-D — the work card's `검증` line in the documents tab, right after the instruction · report lines
// (taskCard 403) and the '병합' line (merge 440). Work cards of a run Flow only (a draft Flow runs nothing); the server
// answers hidden for any other card. The visibility supplier reads the line, so a hidden line leaves no section.
import type { InspectorRegistration } from '../../inspectorRegistry'
import VerificationRow from './VerificationRow.vue'
import { useVerificationVisible } from './useVerification'
import messages from './messages'

const registration: InspectorRegistration = {
  key: 'verification',
  order: 445,
  graphOnly: true,
  cardKinds: ['task'],
  ownerships: ['graph'],
  requiresDocument: false,
  tab: { key: 'documents', component: VerificationRow, visible: useVerificationVisible },
  messages,
}

export default registration
