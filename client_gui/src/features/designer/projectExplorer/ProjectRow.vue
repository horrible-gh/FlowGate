<template>
  <!-- v0.2 Project root. Single click selects it; double click / Enter / the caret open or
       close its Group list. -->
  <ExplorerRowFrame
    :row-id="project.id"
    :level="1"
    :label="project.name"
    :expandable="true"
    :expanded="expanded"
    :selected="selected"
    test-id="project-explorer-project"
    @select="emit('select', project)"
    @activate="emit('toggle', project.id)"
    @toggle="emit('toggle', project.id)"
  >
    <svg class="row-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <rect x="2" y="2.5" width="12" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.4" />
      <path d="M2 6h12" stroke="currentColor" stroke-width="1.4" />
    </svg>
    <span class="row-label" data-test="project-explorer-label">{{ project.name }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerProject } from './projectExplorerTree'

defineProps<{
  project: ExplorerProject
  expanded: boolean
  selected: boolean
}>()

const emit = defineEmits<{
  select: [project: ExplorerProject]
  toggle: [id: string]
}>()
</script>

<style scoped>
.row-icon {
  flex: none;
  color: var(--fg2-blue);
}

.row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg2-text);
  font-weight: 650;
}
</style>
