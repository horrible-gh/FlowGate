<template>
  <!-- v0.2 Flow = v0.1 group. Single click selects; double click keeps that selection and
       adds the activate/open intent (Enter activates). Opening a tab or loading the Canvas is the
       Designer's job (D0002 §5.3), so nothing here touches the central workspace. -->
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
    <span class="tree-ico pe-flow-ico"><AppIcon name="flow-arrow" /></span>
    <span class="tree-lbl" data-test="project-explorer-label">{{ flow.name }}</span>
    <span
      v-if="active"
      class="pe-active-badge"
      data-test="project-explorer-active-badge"
    >{{ t('main.project_explorer.active_flow') }}</span>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerFlow } from '../../utils/projectExplorerTree'

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
.pe-flow-ico {
  color: #60a5fa;
}
.pe-active-badge {
  flex-shrink: 0;
  font-size: 0.62rem;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(96, 165, 250, 0.22);
  color: #bfdbfe;
}
</style>
