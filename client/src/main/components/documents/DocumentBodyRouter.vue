<template>
  <FinalApprovalBody
    v-if="tab.typeCode === 'AC'"
    :completed="completed"
    :group-id="groupId"
    :read-only="readOnly"
    @open-archive="emit('open-archive', $event)"
    @archived="emit('archived', $event)"
  />
  <DiscardBody v-else-if="tab.typeCode === 'DC'" />
  <slot v-else-if="tab.typeCode === 'CH'" name="conversation" />
  <slot v-else-if="tab.typeCode === 'WP'" name="work-plan" />
  <slot v-else-if="tab.type === 'qtui' || (tab.type === 'md' && tab.typeCode === 'Q')" name="question" />
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
import DiscardBody from './DiscardBody.vue'
import FinalApprovalBody from './FinalApprovalBody.vue'
import GenericDocumentBody from './GenericDocumentBody.vue'

defineProps<{
  tab: Tab
  readOnly: boolean
  completed: boolean
  groupId: string
  canEdit: boolean
  editDropdownOpen: boolean
  textWrapEnabled: boolean
  downloadAvailable: boolean
  downloadBusy: boolean
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
  'open-archive': [groupId: string]
  archived: [groupId: string]
}>()
</script>