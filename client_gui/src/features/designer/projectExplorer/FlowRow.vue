<template>
  <!-- v0.2 Flow = v0.1 group. Single click selects; double click keeps that selection and
       adds the activate/open intent (Enter activates). Opening a tab or loading the Canvas is
       the Designer's job (D0002 §5.3), so nothing here touches the central workspace.
       The active (Designer-opened) state is row styling only — no badge or text
       (0025 T0006 §5); ExplorerRowFrame draws it and exposes it as aria-current. -->
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
    <ExplorerIcon class="row-icon" :class="{ 'is-active': active }" name="flow" />
    <span class="row-label" data-test="project-explorer-label">{{ flow.name }}</span>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import ExplorerIcon from '../shared/ExplorerIcon.vue'
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
</script>

<style scoped>
.row-icon {
  color: var(--fg2-cyan);
}

.row-icon.is-active {
  color: var(--fg2-blue);
}

.row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
