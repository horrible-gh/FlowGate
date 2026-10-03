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
    <ExplorerIcon class="row-icon" name="project" />
    <span class="row-label" data-test="project-explorer-label">{{ project.name }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import ExplorerIcon from '../shared/ExplorerIcon.vue'
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
