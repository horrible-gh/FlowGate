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
    <svg class="row-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 2l6 3-6 3-6-3 6-3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
      <path d="M2 8l6 3 6-3M2 11l6 3 6-3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
    </svg>
    <span class="row-label" data-test="project-explorer-label">{{ group.name }}</span>
    <span class="row-count" aria-hidden="true">{{ group.flows.length }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerGroup } from './projectExplorerTree'

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
.row-icon {
  flex: none;
  color: var(--fg2-purple);
}

.row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-count {
  flex: none;
  font-size: 11px;
  color: var(--fg2-muted-2);
}
</style>
