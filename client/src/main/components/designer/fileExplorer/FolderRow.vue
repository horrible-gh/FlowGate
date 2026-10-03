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
      <AppIcon class="dfx-folder-ico" :name="row.expanded ? 'folder-open' : 'folder'" />
    </template>
    <span class="dfx-label">{{ row.node.label }}</span>
    <template v-if="row.changedCount > 0" #trail>
      <span
        class="dfx-folder-changed"
        data-test="dfx-folder-changed"
        :title="t('main.designer_explorer.folder_changed', { n: row.changedCount })"
      >{{ row.changedCount }}</span>
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — folder row (0024-D §7.1): caret, folder icon, name and a
// changed-descendant count. Expansion itself is owned by the container; this row only
// reports the intent, so a collapsed folder still shows that something changed inside.
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import type { FileNode } from '../../../stores/explorer'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import TreeCaret from './TreeCaret.vue'
import type { ExplorerRow } from './fileExplorerModel'

defineProps<{ row: ExplorerRow; selected?: boolean }>()
defineEmits<{ toggle: [node: FileNode]; context: [node: FileNode, event: MouseEvent] }>()
const { t } = useI18n()
</script>

<style scoped>
.dfx-folder-ico { color: #f59e0b; }
.dfx-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dfx-folder-changed {
  min-width: 16px;
  padding: 0 4px;
  border-radius: 8px;
  font-size: 0.66rem;
  text-align: center;
  color: var(--git-modified, #e2c08d);
  background: rgba(226, 192, 141, 0.14);
}
</style>
