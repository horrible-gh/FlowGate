<template>
  <section class="dfx" :aria-label="t('main.designer_explorer.title')" data-test="designer-file-explorer">
    <FileExplorerToolbar
      v-model:query="query"
      v-model:searchOpen="searchOpen"
      :refreshing="loading"
      :disabled="!projectId || !flow"
      @refresh="reload"
    />

    <ExplorerStateView v-if="!projectId" kind="empty" :message="t('main.designer_explorer.no_project')" />
    <ExplorerStateView v-else-if="!flow" kind="empty" :message="t('main.designer_explorer.no_flow')" />
    <template v-else>
      <FileScopeHeader :flow="flow" :group-label="groupLabel" :readonly="!canMutate" :busy="busy" />
      <FileFilter v-model="filter" :changes="changes" />

      <div class="dfx-body">
        <ExplorerStateView v-if="loading && !nodes.length" kind="loading" :message="t('main.designer_explorer.loading')" />
        <ExplorerStateView
          v-else-if="error"
          kind="error"
          :message="t('main.designer_explorer.load_failed')"
          :retry-label="t('main.designer_explorer.retry')"
          @retry="reload"
        />
        <template v-else>
          <div v-if="refreshError" class="dfx-strip" data-test="dfx-refresh-error" role="alert">
            <span>{{ t('main.designer_explorer.refresh_failed') }}</span>
            <button type="button" data-test="dfx-refresh-retry" @click="reload">{{ t('main.designer_explorer.retry') }}</button>
          </div>

          <ExplorerStateView
            v-if="filter === 'changed' && changes.kind === 'unresolved'"
            kind="error"
            :message="t('main.designer_explorer.base_unresolved_title')"
            :detail="unresolvedDetail"
            :retry-label="t('main.designer_explorer.retry')"
            @retry="reload"
          />
          <ExplorerStateView
            v-else-if="filter === 'changed' && changes.kind === 'error'"
            kind="error"
            :message="t('main.designer_explorer.changes_failed')"
            :retry-label="t('main.designer_explorer.retry')"
            @retry="reload"
          />
          <ExplorerStateView
            v-else-if="filter === 'changed' && changes.kind !== 'ready'"
            kind="loading"
            :message="t('main.designer_explorer.changes_loading')"
          />
          <ExplorerStateView v-else-if="!visible.nodes.length" kind="empty" :message="emptyMessage" />
          <FileTree
            v-else
            :nodes="visible.nodes"
            :statuses="statuses"
            :expanded-paths="expandedPaths"
            :reveal-ids="visible.reveal"
            :selected-path="selected"
            :label="t('main.designer_explorer.tree_label')"
            @toggle="toggleFolder"
            @select="selectNode"
            @open="openNode"
            @open-diff="openDiffNode"
            @context="onContext"
          />
        </template>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — Designer File Explorer container (0024-D §2). Receives the
// Project / Group / active-Flow context, owns filter / search / expansion / selection
// state, and forwards select / open / open-diff / context intents to the Designer.
// It does not open editors, resolve conflicts, merge, or switch Dock tabs.
//
// Mutating file actions are not offered here; a Designer that attaches them gets
// `canMutate` with every `context` event, which already folds in the readonly prop,
// the slot's live-worktree flag and the AI-busy lock — the same three gates the
// Dashboard explorer applies (FileExplorer.vue canMutate).
import { computed, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FileNode } from '../../../stores/explorer'
import FileExplorerToolbar from './FileExplorerToolbar.vue'
import FileScopeHeader from './FileScopeHeader.vue'
import FileFilter from './FileFilter.vue'
import FileTree from './FileTree.vue'
import ExplorerStateView from './ExplorerStateView.vue'
import { useFlowFileExplorer } from './useFlowFileExplorer'
import {
  changedViewNodes, normalizePath, searchNodes,
  type ChangeStatus, type DesignerFileIntent, type DesignerFlowScope, type ExplorerFilter,
} from './fileExplorerModel'

const props = withDefaults(defineProps<{
  projectId: string | null
  flow: DesignerFlowScope | null
  groupLabel?: string | null
  readonly?: boolean
  selectedPath?: string | null
  refreshToken?: number
}>(), {
  groupLabel: null,
  readonly: false,
  selectedPath: null,
  refreshToken: 0,
})

const emit = defineEmits<{
  select: [intent: DesignerFileIntent]
  open: [intent: DesignerFileIntent]
  'open-diff': [intent: DesignerFileIntent & { status: ChangeStatus; baseBranch: string }]
  context: [intent: DesignerFileIntent & { event: MouseEvent }]
}>()

const { t } = useI18n()
const projectIdRef = toRef(props, 'projectId')
const flowRef = toRef(props, 'flow')
const {
  nodes, commit, loading, error, refreshError, changes, slotWritable, busy, reload,
} = useFlowFileExplorer(projectIdRef, flowRef)

const filter = ref<ExplorerFilter>('all')
const query = ref('')
const searchOpen = ref(false)
const expandedPaths = ref<Set<string>>(new Set())
const selected = ref<string | null>(props.selectedPath ? normalizePath(props.selectedPath) : null)

watch(() => props.selectedPath, (path) => {
  selected.value = path ? normalizePath(path) : null
})

const canMutate = computed(() => !props.readonly && slotWritable.value && !busy.value)

const statuses = computed<Record<string, ChangeStatus> | null>(() =>
  changes.value.kind === 'ready' ? changes.value.statuses : null,
)

const visible = computed(() => {
  const base = filter.value === 'changed' && statuses.value
    ? changedViewNodes(nodes.value, statuses.value)
    : nodes.value
  return searchNodes(base, query.value)
})

const emptyMessage = computed(() => {
  if (query.value.trim()) return t('main.designer_explorer.empty_search', { query: query.value.trim() })
  if (filter.value === 'changed' && changes.value.kind === 'ready') {
    return t('main.designer_explorer.empty_changed', { branch: changes.value.baseBranch })
  }
  return t('main.designer_explorer.empty_tree')
})

const unresolvedDetail = computed(() => {
  const state = changes.value
  if (state.kind !== 'unresolved') return null
  return t(`main.designer_explorer.base_reason.${state.reason}`, {
    expected: state.expected ?? '-',
    actual: state.actual ?? '-',
  })
})

function intent(node: FileNode): DesignerFileIntent {
  return {
    projectId: props.projectId ?? '',
    groupId: props.flow?.groupId ?? '',
    node,
    commit: commit.value,
    canMutate: canMutate.value,
  }
}

function toggleFolder(node: FileNode) {
  const path = normalizePath(node.path)
  const next = new Set(expandedPaths.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedPaths.value = next
}

function selectNode(node: FileNode) {
  selected.value = normalizePath(node.path)
  emit('select', intent(node))
}

function openNode(node: FileNode) {
  selected.value = normalizePath(node.path)
  emit('open', intent(node))
}

function openDiffNode(node: FileNode, status: ChangeStatus) {
  // Only reachable from a row that has a status, i.e. while the comparison is `ready`.
  if (changes.value.kind !== 'ready') return
  selected.value = normalizePath(node.path)
  emit('open-diff', { ...intent(node), status, baseBranch: changes.value.baseBranch })
}

function onContext(node: FileNode, event: MouseEvent) {
  emit('context', { ...intent(node), event })
}

// A different Flow (or project) is a different tree: drop view state tied to the old one.
watch(
  () => [props.projectId, props.flow?.groupId ?? null] as const,
  (next, prev) => {
    if (prev && (next[0] !== prev[0] || next[1] !== prev[1])) {
      expandedPaths.value = new Set()
      query.value = ''
      searchOpen.value = false
      if (!props.selectedPath) selected.value = null
    }
    void reload()
  },
  { immediate: true },
)

watch(() => props.refreshToken, (next, prev) => {
  if (next !== prev) void reload()
})

// A Designer can also ask for a refresh imperatively (e.g. after its own save).
defineExpose({ reload })
</script>

<style scoped>
.dfx { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.dfx-body { flex: 1 1 auto; min-height: 0; overflow: auto; }
.dfx-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  font-size: 0.72rem;
  color: #fcd34d;
  background: rgba(245, 158, 11, 0.1);
}
.dfx-strip button {
  padding: 1px 8px;
  font-size: 0.7rem;
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 4px;
  cursor: pointer;
}
</style>
