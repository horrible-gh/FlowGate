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

      <div class="dfx-body" @contextmenu="onBlankContext">
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
//
// flowgate.v02.0060 T#2: inside the Designer a right click on a folder / file row, or on the blank tree area (the
// root folder), also goes to the Designer context menu with that node's real path and type and what this explorer
// knows of it now (expansion, canMutate, read permission, a virtual deleted node). Its 열기 unfolds the folder through
// the same expansion a click uses — open only, never closing. The rows keep the browser menu blocked as before.
import { computed, inject, ref, watch } from 'vue'
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
  openContextMenu(event, node)
}

// ── flowgate.v02.0060 T#2: the Designer context menu ───────────────────────────────────────────

/** The Designer context menu's File Explorer side, provided by the Designer Shell; null elsewhere. */
interface FileContextMenu {
  file(event: MouseEvent, entry: {
    target:
      | { kind: 'folder'; projectId: string; flowId: string; path: string; commit: string | null; root: boolean }
      | { kind: 'file'; projectId: string; flowId: string; path: string; commit: string | null; change: string | null }
    available: () => {
      expanded?: boolean
      canMutate: boolean
      virtualDeleted: boolean
      canRead: boolean
      git: 'linked' | 'unavailable'
      hasChanges?: boolean
    } | null
    expand?: () => boolean
    fallback?: HTMLElement | null
  }): boolean
}
const contextMenu = inject<FileContextMenu | null>(Symbol.for('flowgate.designer.contextMenu'), null)

/** The node as the tree shows it now (a refresh may have removed it), by path and type. */
function shownNode(path: string, type: FlowFileNode['type']): FlowFileNode | null {
  return visible.value.nodes.find((node) => normalizePath(node.path) === path && node.type === type) ?? null
}

/** What the menu needs to know of the node now; null once the tree no longer shows it. */
function menuAvailability(path: string, type: FlowFileNode['type']) {
  const node = shownNode(path, type)
  if (!node) return null
  return {
    expanded: expandedPaths.value.has(path) || visible.value.reveal.has(node.id),
    canMutate: canMutate.value,
    // A path the Flow deleted and committed is shown as a read-only virtual node (changedViewNodes).
    virtualDeleted: !nodes.value.some((real) => real.id === node.id),
    canRead: node.permissions.includes('read'),
    git: changes.value.kind === 'ready' ? 'linked' as const : 'unavailable' as const,
    hasChanges: !!statuses.value?.[path],
  }
}

function openContextMenu(event: MouseEvent, node: FlowFileNode) {
  const projectId = resolvedProjectId.value
  const flowId = props.flow?.groupId
  if (!contextMenu || !projectId || !flowId) return
  const path = normalizePath(node.path)
  const scope = { projectId, flowId, path, commit: commit.value }
  const target = node.type === 'folder'
    ? { ...scope, kind: 'folder' as const, root: false }
    : { ...scope, kind: 'file' as const, change: statuses.value?.[path] ?? null }
  const fallback = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  contextMenu.file(event, {
    target,
    available: () => (props.flow?.groupId === flowId ? menuAvailability(path, node.type) : null),
    expand: () => {
      const shown = shownNode(path, 'folder')
      if (!shown || expandedPaths.value.has(path) || visible.value.reveal.has(shown.id)) return false
      toggleFolder(shown)
      return true
    },
    fallback,
  })
}

/** The blank tree area is the root folder (no 열기 / 이름 변경 / 삭제 there). */
function onBlankContext(event: MouseEvent) {
  const projectId = resolvedProjectId.value
  const flowId = props.flow?.groupId
  if (!contextMenu || !projectId || !flowId) return
  contextMenu.file(event, {
    target: { kind: 'folder', projectId, flowId, path: '', commit: commit.value, root: true },
    available: () => (props.flow?.groupId === flowId
      ? { canMutate: canMutate.value, virtualDeleted: false, canRead: true, git: changes.value.kind === 'ready' ? 'linked' : 'unavailable' }
      : null),
    fallback: event.currentTarget instanceof HTMLElement ? event.currentTarget : null,
  })
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
