<template>
  <!-- v0.2 Group = v0.1 module. Single click selects the Group (reported upward);
       double click / Enter / the caret open or close its Flow list. -->
  <ExplorerRowFrame
    :row-id="group.id"
    :level="2"
    :label="group.name"
    :expandable="true"
    :expanded="expanded"
    :selected="selected"
    test-id="project-explorer-group"
    @select="emit('select', group)"
    @activate="emit('toggle', group.id)"
    @toggle="emit('toggle', group.id)"
  >
    <span class="tree-ico pe-group-ico"><AppIcon name="stack" /></span>
    <span class="tree-lbl" data-test="project-explorer-label">{{ group.name }}</span>
    <span class="pe-count" aria-hidden="true">{{ group.flows.length }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerGroup } from '../../utils/projectExplorerTree'

defineProps<{
  group: ExplorerGroup
  expanded: boolean
  selected: boolean
}>()

const emit = defineEmits<{
  select: [group: ExplorerGroup]
  toggle: [id: string]
}>()
</script>

<style scoped>
.pe-group-ico {
  color: #a78bfa;
}
.pe-count {
  flex-shrink: 0;
  font-size: 0.68rem;
  color: rgba(255, 255, 255, 0.35);
}
</style>
