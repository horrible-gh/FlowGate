<template>
  <div class="dfx-tree" role="tree" :aria-label="label" data-test="dfx-tree">
    <template v-for="row in rows" :key="row.node.id">
      <FolderRow
        v-if="row.node.type === 'folder'"
        :row="row"
        :selected="row.node.path === selectedPath"
        @toggle="$emit('toggle', $event)"
        @context="(node, event) => $emit('context', node, event)"
      />
      <FileRow
        v-else
        :row="row"
        :selected="row.node.path === selectedPath"
        @select="$emit('select', $event)"
        @open="$emit('open', $event)"
        @open-diff="(node, status) => $emit('open-diff', node, status)"
        @context="(node, event) => $emit('context', node, event)"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — FileTree (0024-D §6): turns the (already filtered) node set
// into depth-ordered rows and assembles FolderRow / FileRow. Which nodes are visible is
// decided by the container (전체 / 변경됨 / 찾기); expansion is passed in as data.
import { computed } from 'vue'
import type { FlowFileNode } from '@/features/project'
import FolderRow from './FolderRow.vue'
import FileRow from './FileRow.vue'
import { flattenRows, normalizePath, type ChangeStatus } from './fileExplorerModel'

const props = defineProps<{
  nodes: FlowFileNode[]
  /** Base-relative statuses, or null while the comparison is not established. */
  statuses: Record<string, ChangeStatus> | null
  expandedPaths: Set<string>
  /** Folders forced open (ancestors of a search hit) on top of expandedPaths. */
  revealIds?: Set<string>
  selectedPath: string | null
  label: string
}>()

defineEmits<{
  toggle: [node: FlowFileNode]
  select: [node: FlowFileNode]
  open: [node: FlowFileNode]
  'open-diff': [node: FlowFileNode, status: ChangeStatus]
  context: [node: FlowFileNode, event: MouseEvent]
}>()

const rows = computed(() =>
  flattenRows(
    props.nodes,
    (node) => props.expandedPaths.has(normalizePath(node.path)) || !!props.revealIds?.has(node.id),
    props.statuses,
  ),
)
</script>

<style scoped>
.dfx-tree {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 4px;
}
</style>
