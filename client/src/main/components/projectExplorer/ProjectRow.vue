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
    <span class="tree-ico proj"><AppIcon name="tree-structure" /></span>
    <span class="tree-lbl pe-project-lbl" data-test="project-explorer-label">{{ project.name }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerProject } from '../../utils/projectExplorerTree'

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
.pe-project-lbl {
  color: rgba(255, 255, 255, 0.85);
  font-weight: 600;
}
</style>
