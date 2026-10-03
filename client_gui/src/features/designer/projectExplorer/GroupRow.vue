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
    <ExplorerIcon class="row-icon" :name="expanded ? 'folder-open' : 'folder'" />
    <span class="row-label" data-test="project-explorer-label">{{ group.name }}</span>
    <span class="row-count" aria-hidden="true">{{ group.flows.length }}</span>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import ExplorerIcon from '../shared/ExplorerIcon.vue'
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
