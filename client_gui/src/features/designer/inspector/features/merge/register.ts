// Merge follows the card document slots without changing the fixed tab strip.
import type { InspectorRegistration } from '../../inspectorRegistry'
import MergeRow from './MergeRow.vue'
import { useMergeRowVisible } from './useMerge'
import messages from './messages'
const registration: InspectorRegistration = {
  key: 'merge', order: 440, graphOnly: true, requiresDocument: false,
  tab: { key: 'documents', component: MergeRow, visible: useMergeRowVisible }, messages,
}
export default registration
