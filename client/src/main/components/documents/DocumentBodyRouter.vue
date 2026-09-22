<template>
  <FinalApprovalBody
    v-if="tab.typeCode === 'AC'"
    :completed="completed"
    :group-id="tab.id.split('.').slice(0, -1).join('.')"
  />
  <DiscardBody v-else-if="tab.typeCode === 'DC'" />
  <!-- CH keeps a single ConversationView instance across the full-view move, so everything
       the chat's state depends on is handed down as a prop and every seam MainPanel drives
       (the instance ref, the mention copy, the manual-copy dismissal) is relayed back up.
       The router selects and wires the body; it owns none of that orchestration. -->
  <ConversationDocumentView
    v-else-if="tab.typeCode === 'CH'"
    :tab="tab"
    :read-only="conversationReadOnly"
    :manual-copy-text="conversationManualCopyText"
    :full-view-host="conversationFullViewHost"
    :full-view-on="conversationFullViewOn"
    @open-full-view="emit('open-full-view')"
    @copy-mention="emit('copy-mention', $event)"
    @manual-copy-dismiss="emit('manual-copy-dismiss')"
    @bind-conversation-view="emit('bind-conversation-view', $event)"
  />
  <!-- WP reuses the existing WorkPlanEditor unchanged; only the choice and the wiring moved
       here. The instance is relayed to MainPanel, which keeps the two seams that need it:
       DocWorkflow's `sequence-updated` -> fetchPlan() (no F5 after a pour, 0434) and
       ensureSaved() before a WP approval. -->
  <WorkPlanEditor
    v-else-if="tab.typeCode === 'WP'"
    :ref="(el) => emit('bind-work-plan-editor', el)"
    :doc-id="tab.id"
    :project-id="tab.projectId ?? null"
    :read-only="readOnly"
  />
  <QuestionDocumentBody
    v-else-if="tab.type === 'qtui' || (tab.type === 'md' && tab.typeCode === 'Q')"
    :tab="tab"
    :read-only="readOnly"
    @status-changed="emit('q-status-changed', $event)"
  />
  <GenericDocumentBody
    v-else
    :tab="tab"
    :read-only="readOnly"
    :can-edit="canEdit"
    :edit-dropdown-open="editDropdownOpen"
    :text-wrap-enabled="textWrapEnabled"
    :download-available="downloadAvailable"
    :download-busy="downloadBusy"
    @close="emit('close')"
    @edit-direct="emit('edit-direct')"
    @edit-mention="emit('edit-mention')"
    @invoke-command="emit('invoke-command')"
    @invoke-ai="emit('invoke-ai')"
    @open-full-view="emit('open-full-view')"
    @toggle-edit-dropdown="emit('toggle-edit-dropdown')"
    @download-markdown="emit('download-markdown')"
    @update:text-wrap-enabled="emit('update:text-wrap-enabled', $event)"
    @bind-md-viewer="emit('bind-md-viewer', $event)"
    @bind-text-viewer="emit('bind-text-viewer', $event)"
  />
</template>

<script setup lang="ts">
import type { Tab } from '../../stores/tabs'
import WorkPlanEditor from '../WorkPlanEditor.vue'
import ConversationDocumentView from './ConversationDocumentView.vue'
import DiscardBody from './DiscardBody.vue'
import FinalApprovalBody from './FinalApprovalBody.vue'
import GenericDocumentBody from './GenericDocumentBody.vue'
import QuestionDocumentBody from './QuestionDocumentBody.vue'

defineProps<{
  tab: Tab
  readOnly: boolean
  completed: boolean
  canEdit: boolean
  editDropdownOpen: boolean
  textWrapEnabled: boolean
  downloadAvailable: boolean
  downloadBusy: boolean
  // CH-specific inputs. They are separate props rather than being folded into the shared
  // ones because MainPanel decides them: the chat's own AI run is exempt from the document
  // lock, and the full-view target belongs to MainPanel's dialog.
  conversationReadOnly: boolean
  conversationManualCopyText: string | null
  conversationFullViewHost: string | null
  conversationFullViewOn: boolean
}>()

const emit = defineEmits<{
  close: []
  'edit-direct': []
  'edit-mention': []
  'invoke-command': []
  'invoke-ai': []
  'open-full-view': []
  'toggle-edit-dropdown': []
  'download-markdown': []
  'update:text-wrap-enabled': [enabled: boolean]
  'bind-md-viewer': [instance: unknown]
  'bind-text-viewer': [instance: unknown]
  'copy-mention': [opts?: { auto?: boolean }]
  'manual-copy-dismiss': []
  'bind-conversation-view': [instance: unknown]
  'bind-work-plan-editor': [instance: unknown]
  'q-status-changed': [payload: { qId: string; status: string; done: boolean }]
}>()
</script>
