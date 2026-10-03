<template>
  <ExplorerRowFrame
    :depth="row.depth"
    :selected="selected"
    :muted="isDeleted"
    data-test="dfx-file-row"
    :data-path="row.node.path"
    @click="$emit('select', row.node)"
    @dblclick="open"
    @keydown.enter.prevent="open"
    @contextmenu.prevent="$emit('context', row.node, $event)"
  >
    <template #lead>
      <span class="dfx-caret-spacer" aria-hidden="true" />
      <AppIcon class="dfx-file-ico" :name="icon.name" :style="{ color: icon.color }" />
    </template>
    <span class="dfx-label" :class="{ 'dfx-label--deleted': isDeleted }">{{ row.node.label }}</span>
    <template v-if="row.status" #trail>
      <button
        type="button"
        class="dfx-diff-btn"
        data-test="dfx-open-diff"
        :title="t('main.designer_explorer.open_diff')"
        :aria-label="t('main.designer_explorer.open_diff')"
        @click.stop="openDiff"
        @dblclick.stop
      ><AppIcon name="git-diff" /></button>
      <StatusBadge :status="row.status" />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — file row (0024-D §7.2): icon, name, Base-relative status and
// the select / open / open-diff intents. It never opens an editor or a resolver itself —
// the container forwards the intent to the Designer. A file the Flow deleted cannot be
// opened (there is nothing to open), but its diff still can.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import type { FileNode } from '../../../stores/explorer'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import StatusBadge from './StatusBadge.vue'
import type { ChangeStatus, ExplorerRow } from './fileExplorerModel'

const props = defineProps<{ row: ExplorerRow; selected?: boolean }>()
const emit = defineEmits<{
  select: [node: FileNode]
  open: [node: FileNode]
  'open-diff': [node: FileNode, status: ChangeStatus]
  context: [node: FileNode, event: MouseEvent]
}>()
const { t } = useI18n()

const isDeleted = computed(() => props.row.status === 'D')

const icon = computed(() => {
  const name = props.row.node.name.toLowerCase()
  if (name.endsWith('.md') || name.endsWith('.markdown')) return { name: 'file-text', color: '#60a5fa' }
  if (/\.(ts|tsx|js|jsx|vue|py|go|rs|java|json|ya?ml|css|html)$/.test(name)) return { name: 'file-code', color: '#a78bfa' }
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(name)) return { name: 'file-image', color: '#34d399' }
  return { name: 'file', color: '#94a3b8' }
})

function open() {
  if (isDeleted.value) return
  emit('open', props.row.node)
}

function openDiff() {
  if (props.row.status) emit('open-diff', props.row.node, props.row.status)
}
</script>

<style scoped>
.dfx-caret-spacer { display: inline-block; width: 12px; }
.dfx-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dfx-label--deleted { color: var(--danger, #dc2626); text-decoration: line-through; }
.dfx-diff-btn {
  display: none;
  padding: 0 2px;
  font-size: 0.78rem;
  color: var(--text-m, rgba(255, 255, 255, 0.6));
  background: transparent;
  border: none;
  cursor: pointer;
}
.dfx-row:hover .dfx-diff-btn,
.dfx-row:focus-within .dfx-diff-btn,
.dfx-row--selected .dfx-diff-btn { display: inline-flex; }
</style>
