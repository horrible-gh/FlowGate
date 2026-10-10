import type { InspectorRegistration } from '../../inspectorRegistry'
import { getRequest } from '@shared/api'
import messages from './messages'
import ZetaTab from './ZetaTab.vue'

void getRequest

export default {
  key: 'zeta',
  order: 910,
  requiresDocument: true,
  tab: { key: 'documents', component: ZetaTab },
  messages,
} satisfies InspectorRegistration
