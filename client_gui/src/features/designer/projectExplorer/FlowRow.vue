<template>
  <!-- v0.2 Flow = v0.1 group. Single click selects; double click keeps that selection and
       adds the activate/open intent (Enter activates). Opening a tab or loading the Canvas is
       the Designer's job (D0002 §5.3), so nothing here touches the central workspace. -->
  <ExplorerRowFrame
    :row-id="flow.id"
    :level="3"
    :label="flow.name"
    :expandable="false"
    :expanded="false"
    :selected="selected"
    :active="active"
    test-id="project-explorer-flow"
    @select="emit('select', flow)"
    @activate="emit('activate', flow)"
  >
    <svg class="row-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <circle cx="3.5" cy="8" r="1.8" fill="none" stroke="currentColor" stroke-width="1.3" />
      <circle cx="12.5" cy="4" r="1.8" fill="none" stroke="currentColor" stroke-width="1.3" />
      <circle cx="12.5" cy="12" r="1.8" fill="none" stroke="currentColor" stroke-width="1.3" />
      <path d="M5.2 7.2l5.6-2.4M5.2 8.8l5.6 2.4" fill="none" stroke="currentColor" stroke-width="1.3" />
    </svg>
    <span class="row-label" data-test="project-explorer-label">{{ flow.name }}</span>
    <Badge v-if="active" tone="info" size="sm" data-test="project-explorer-active-badge">
      {{ t('gui.designer.project_explorer.active_flow') }}
    </Badge>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Badge from '../../../components/ui/Badge.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerFlow } from './projectExplorerTree'

defineProps<{
  flow: ExplorerFlow
  selected: boolean
  active: boolean
}>()

const emit = defineEmits<{
  select: [flow: ExplorerFlow]
  activate: [flow: ExplorerFlow]
}>()

const { t } = useI18n()
</script>

<style scoped>
.row-icon {
  flex: none;
  color: var(--fg2-cyan);
}

.row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
