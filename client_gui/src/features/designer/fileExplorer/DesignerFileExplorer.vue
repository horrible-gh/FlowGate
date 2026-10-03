<template>
  <section class="dfx" :aria-label="t('gui.designer.file_explorer.title')" data-test="designer-file-explorer">
    <FileExplorerToolbar
      v-model:query="query"
      v-model:searchOpen="searchOpen"
      :refreshing="loading"
      :disabled="!resolvedProjectId || !flow"
      @refresh="reload"
    >
      <FileFilter v-if="resolvedProjectId && flow" v-model="filter" :changes="changes" />
    </FileExplorerToolbar>

    <EmptyState v-if="!resolvedProjectId" compact :title="t('gui.designer.file_explorer.no_project')" data-test="dfx-no-project" />
    <EmptyState v-else-if="!flow" compact :title="t('gui.designer.file_explorer.no_flow')" data-test="dfx-no-flow" />
    <template v-else>
      <FileScopeHeader :group-label="groupLabel" :branch="flow.branch" :readonly="!canMutate" :busy="busy" />

      <div class="dfx-body">
        <LoadingState v-if="loading && !nodes.length" size="sm" :label="t('gui.designer.file_explorer.loading')" />
        <ErrorState v-else-if="error" compact :title="t('gui.designer.file_explorer.load_failed')" data-test="dfx-load-error">
          <template #action>
            <Button variant="secondary" size="sm" data-test="dfx-retry" @click="reload">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>
        <template v-else>
          <Alert v-if="refreshError" class="dfx-refresh-alert" tone="warning" data-test="dfx-refresh-error">
            {{ t('gui.designer.file_explorer.refresh_failed') }}
            <template #action>
              <Button variant="secondary" size="sm" data-test="dfx-refresh-retry" @click="reload">{{ t('gui.common.retry') }}</Button>
            </template>
          </Alert>

          <ErrorState
            v-if="filter === 'changed' && changes.kind === 'unresolved'"
            compact
            tone="warning"
            :title="t('gui.designer.file_explorer.base_unresolved_title')"
            :message="unresolvedDetail"
            data-test="dfx-base-unresolved"
          >
            <template #action>
              <Button variant="secondary" size="sm" data-test="dfx-retry" @click="reload">{{ t('gui.common.retry') }}</Button>
            </template>
          </ErrorState>
          <ErrorState
            v-else-if="filter === 'changed' && changes.kind === 'error'"
            compact
            :title="t('gui.designer.file_explorer.changes_failed')"
            data-test="dfx-changes-error"
          >
            <template #action>
              <Button variant="secondary" size="sm" data-test="dfx-retry" @click="reload">{{ t('gui.common.retry') }}</Button>
            </template>
          </ErrorState>
          <LoadingState
            v-else-if="filter === 'changed' && changes.kind !== 'ready'"
            size="sm"
            :label="t('gui.designer.file_explorer.changes_loading')"
          />
          <EmptyState v-else-if="!visible.nodes.length" compact :title="emptyMessage" data-test="dfx-empty" />
          <FileTree
            v-else
            :nodes="visible.nodes"
            :statuses="statuses"
            :expanded-paths="expandedPaths"
            :reveal-ids="visible.reveal"
            :selected-path="selected"
            :label="t('gui.designer.file_explorer.tree_label')"
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
// flowgate.v02.0024 T0005 — Designer File Explorer container (0024-D §2). Receives the
// Project / Group / active-Flow context, owns filter / search / expansion / selection
// state, and forwards select / open / open-diff / context intents to the Designer. It does
// not open editors, resolve conflicts, merge, or switch Dock tabs.
//
// Project context comes from the `projectId` prop, or — when the prop is omitted — the
// current Project of features/project. The active Flow is always handed in by the Designer
// (`flow`), because which Flow is active is the Designer's decision.
//
// Mutating file actions are not offered here; a Designer that attaches them gets
// `canMutate` with every intent. It folds in the readonly/busy props, the slot's
// live-worktree flag and the Flow's AI activity, and is false whenever any of them could
// not be confirmed for the current scope (fail closed).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProjectStore, type FlowFileNode, type FlowRef } from '@/features/project'
import Alert from '../../../components/ui/Alert.vue'
import Button from '../../../components/ui/Button.vue'
import EmptyState from '../../../components/ui/EmptyState.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import FileExplorerToolbar from './FileExplorerToolbar.vue'
import FileScopeHeader from './FileScopeHeader.vue'
import FileFilter from './FileFilter.vue'
import FileTree from './FileTree.vue'
import { useFlowFileExplorer } from './useFlowFileExplorer'
import {
  changedViewNodes, normalizePath, searchNodes,
  type ChangeStatus, type DesignerFileIntent, type ExplorerFilter,
} from './fileExplorerModel'

const props = withDefaults(defineProps<{
  /** Omit to follow the current Project of features/project. */
  projectId?: string | null
  flow: FlowRef | null
  groupLabel?: string | null
  readonly?: boolean
  /** Extra busy signal from the Designer (e.g. a save in progress); AI activity is read here. */
  busy?: boolean
  selectedPath?: string | null
  refreshToken?: number
}>(), {
  projectId: undefined,
  groupLabel: null,
  readonly: false,
  busy: false,
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
const projectStore = useProjectStore()

const resolvedProjectId = computed<string | null>(() =>
  props.projectId !== undefined ? props.projectId : (projectStore.currentProject?.id ?? null),
)
const flowRef = computed(() => props.flow)
const {
  nodes, commit, loading, error, refreshError, changes, slotWritable, aiBusy, aiActivityKnown, reload,
} = useFlowFileExplorer(resolvedProjectId, flowRef)

const filter = ref<ExplorerFilter>('all')
const query = ref('')
const searchOpen = ref(false)
const expandedPaths = ref<Set<string>>(new Set())
const selected = ref<string | null>(props.selectedPath ? normalizePath(props.selectedPath) : null)

watch(() => props.selectedPath, (path) => {
  selected.value = path ? normalizePath(path) : null
})

const busy = computed(() => props.busy || aiBusy.value)
const canMutate = computed(() => !props.readonly && slotWritable.value && aiActivityKnown.value && !busy.value)

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
  if (query.value.trim()) return t('gui.designer.file_explorer.empty_search', { query: query.value.trim() })
  if (filter.value === 'changed' && changes.value.kind === 'ready') {
    return t('gui.designer.file_explorer.empty_changed')
  }
  return t('gui.designer.file_explorer.empty_tree')
})

const unresolvedDetail = computed(() => {
  const state = changes.value
  if (state.kind !== 'unresolved') return undefined
  return t(`gui.designer.file_explorer.base_reason.${state.reason}`, {
    expected: state.expected ?? '-',
    actual: state.actual ?? '-',
  })
})

function intent(node: FlowFileNode): DesignerFileIntent {
  return {
    projectId: resolvedProjectId.value ?? '',
    groupId: props.flow?.groupId ?? '',
    node,
    commit: commit.value,
    canMutate: canMutate.value,
  }
}

function toggleFolder(node: FlowFileNode) {
  const path = normalizePath(node.path)
  const next = new Set(expandedPaths.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedPaths.value = next
}

function selectNode(node: FlowFileNode) {
  selected.value = normalizePath(node.path)
  emit('select', intent(node))
}

function openNode(node: FlowFileNode) {
  selected.value = normalizePath(node.path)
  emit('open', intent(node))
}

function openDiffNode(node: FlowFileNode, status: ChangeStatus) {
  // Only reachable from a row that has a status, i.e. while the comparison is `ready`.
  if (changes.value.kind !== 'ready') return
  selected.value = normalizePath(node.path)
  emit('open-diff', { ...intent(node), status, baseBranch: changes.value.baseBranch })
}

function onContext(node: FlowFileNode, event: MouseEvent) {
  emit('context', { ...intent(node), event })
}

// A different Flow (or project) is a different tree: drop view state tied to the old one.
watch(
  () => [resolvedProjectId.value, props.flow?.groupId ?? null] as const,
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
.dfx {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: var(--fg2-text);
}

.dfx-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.dfx-refresh-alert {
  margin: 4px 8px 6px;
  padding: 8px 10px;
  font-size: 12px;
}
</style>
