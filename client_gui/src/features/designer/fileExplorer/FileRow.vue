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
    @keydown.space.prevent="$emit('select', row.node)"
    @contextmenu="$emit('context', row.node, $event); $event.preventDefault()"
  >
    <template #lead>
      <span class="dfx-caret-spacer" aria-hidden="true" />
      <svg class="dfx-file-ico" :class="`dfx-file-ico--${kind}`" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M3.5 1.5h6l3 3v10h-9z M9.5 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
      </svg>
    </template>
    <span class="dfx-label" :class="{ 'dfx-label--deleted': isDeleted }">{{ row.node.label }}</span>
    <template v-if="row.status" #trail>
      <button
        type="button"
        class="dfx-diff-btn"
        data-test="dfx-open-diff"
        :title="t('gui.designer.file_explorer.open_diff')"
        :aria-label="t('gui.designer.file_explorer.open_diff')"
        @click.stop="openDiff"
        @dblclick.stop
        @keydown.enter.stop
      >
        <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
          <path d="M4 2v8M4 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM12 14V6M12 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" fill="none" stroke="currentColor" stroke-width="1.3" />
        </svg>
      </button>
      <StatusBadge :status="row.status" />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — file row (0024-D §7.2): icon, name, Base-relative status and
// the select / open / open-diff intents. It never opens an editor or a resolver itself —
// the container forwards the intent to the Designer. A file the Flow deleted cannot be
// opened (there is nothing to open), but its diff still can.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FlowFileNode } from '@/features/project'
import ExplorerRowFrame from '../shared/ExplorerRowFrame.vue'
import StatusBadge from './StatusBadge.vue'
import type { ChangeStatus, ExplorerRow } from './fileExplorerModel'

const props = defineProps<{ row: ExplorerRow; selected?: boolean }>()
const emit = defineEmits<{
  select: [node: FlowFileNode]
  open: [node: FlowFileNode]
  'open-diff': [node: FlowFileNode, status: ChangeStatus]
  context: [node: FlowFileNode, event: MouseEvent]
}>()
const { t } = useI18n()

const isDeleted = computed(() => props.row.status === 'D')

const kind = computed(() => {
  const name = props.row.node.name.toLowerCase()
  if (/\.(md|markdown|txt)$/.test(name)) return 'doc'
  if (/\.(ts|tsx|js|jsx|vue|py|go|rs|java|json|ya?ml|css|html|sql|sh)$/.test(name)) return 'code'
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(name)) return 'image'
  return 'plain'
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
.dfx-caret-spacer {
  display: inline-block;
  width: 12px;
}

.dfx-file-ico {
  flex: none;
  color: var(--fg2-muted);
}

.dfx-file-ico--doc {
  color: var(--fg2-blue);
}

.dfx-file-ico--code {
  color: var(--fg2-purple);
}

.dfx-file-ico--image {
  color: var(--fg2-cyan);
}

.dfx-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dfx-label--deleted {
  color: var(--fg2-status-danger-fg);
  text-decoration: line-through;
}

.dfx-diff-btn {
  display: none;
  align-items: center;
  padding: 0 2px;
  color: var(--fg2-muted);
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.dfx-diff-btn:hover {
  color: var(--fg2-text);
}

/* The frame's root carries this component's scope id too (it is FileRow's root element),
   so its row states can reveal the diff button without a deep selector. */
.dx-row:hover .dfx-diff-btn,
.dx-row:focus-within .dfx-diff-btn,
.dx-row--selected .dfx-diff-btn {
  display: inline-flex;
}
</style>
