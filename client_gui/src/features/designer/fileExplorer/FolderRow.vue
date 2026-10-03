<template>
  <ExplorerRowFrame
    :depth="row.depth"
    :selected="selected"
    :aria-expanded="row.expanded"
    data-test="dfx-folder-row"
    :data-path="row.node.path"
    @click="$emit('toggle', row.node)"
    @keydown.enter.prevent="$emit('toggle', row.node)"
    @keydown.space.prevent="$emit('toggle', row.node)"
    @keydown.right.prevent="!row.expanded && $emit('toggle', row.node)"
    @keydown.left.prevent="row.expanded && $emit('toggle', row.node)"
    @contextmenu.prevent="$emit('context', row.node, $event)"
  >
    <template #lead>
      <TreeCaret :expanded="row.expanded" :has-children="row.hasChildren" @toggle="$emit('toggle', row.node)" />
      <ExplorerIcon class="dfx-folder-ico" :name="row.expanded ? 'folder-open' : 'folder'" />
    </template>
    <span class="dfx-label">{{ row.node.label }}</span>
    <template v-if="row.changedCount > 0" #trail>
      <span
        class="dfx-folder-changed"
        data-test="dfx-folder-changed"
        :title="t('gui.designer.file_explorer.folder_changed', { n: row.changedCount })"
      >{{ row.changedCount }}</span>
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — folder row (0024-D §7.1): caret, folder icon, name and a
// changed-descendant count. Expansion itself is owned by the container; this row only
// reports the intent, so a collapsed folder still shows that something changed inside.
import { useI18n } from 'vue-i18n'
import type { FlowFileNode } from '@/features/project'
import ExplorerIcon from '../shared/ExplorerIcon.vue'
import ExplorerRowFrame from '../shared/ExplorerRowFrame.vue'
import TreeCaret from '../shared/TreeCaret.vue'
import type { ExplorerRow } from './fileExplorerModel'

defineProps<{ row: ExplorerRow; selected?: boolean }>()
defineEmits<{ toggle: [node: FlowFileNode]; context: [node: FlowFileNode, event: MouseEvent] }>()
const { t } = useI18n()
</script>

<style scoped>
.dfx-folder-ico {
  color: var(--fg2-orange);
}

.dfx-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dfx-folder-changed {
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--fg2-control-radius-pill);
  font-size: 10.5px;
  line-height: 16px;
  text-align: center;
  color: var(--fg2-status-warning-fg);
  background: var(--fg2-status-warning-bg);
}
</style>
