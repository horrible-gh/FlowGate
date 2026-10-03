<template>
  <!-- flowgate.v02.0023 — Designer Project Explorer (R0001 / D0002 §3).
       Shows the current Project as Project > Group > Flow and reports selection /
       activation upward. It never opens tabs, touches the Canvas, or owns the left dock's
       tab switch / collapse / resize — those belong to the Designer around it. -->
  <section class="project-explorer" :aria-label="t('gui.designer.project_explorer.title')" data-test="project-explorer">
    <header class="project-explorer-head">
      <span class="project-explorer-title">{{ t('gui.designer.project_explorer.title') }}</span>
      <IconButton
        size="sm"
        :label="t('gui.designer.project_explorer.refresh')"
        :title="t('gui.designer.project_explorer.refresh')"
        :disabled="!projectId || loading || refreshing"
        data-test="project-explorer-refresh"
        @click="refresh"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" :class="{ 'is-spinning': refreshing }">
          <path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5v3h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </IconButton>
    </header>

    <div class="project-explorer-search">
      <Input
        v-model="query"
        type="search"
        :placeholder="t('gui.designer.project_explorer.search_placeholder')"
        :aria-label="t('gui.designer.project_explorer.search_placeholder')"
        :disabled="!projectId"
        data-test="project-explorer-search"
      />
      <IconButton
        v-if="isSearching"
        class="project-explorer-search-clear"
        size="sm"
        :label="t('gui.designer.project_explorer.search_clear')"
        :title="t('gui.designer.project_explorer.search_clear')"
        data-test="project-explorer-search-clear"
        @click="query = ''"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </IconButton>
    </div>

    <div class="project-explorer-body">
      <EmptyState
        v-if="!projectId"
        compact
        :title="t('gui.designer.project_explorer.no_project')"
        :description="t('gui.designer.project_explorer.no_project_desc')"
        data-test="project-explorer-no-project"
      />
      <LoadingState
        v-else-if="loading"
        size="sm"
        :label="t('gui.designer.project_explorer.loading')"
        data-test="project-explorer-loading"
      />
      <ErrorState
        v-else-if="error !== null"
        compact
        :title="t('gui.designer.project_explorer.load_failed')"
        :message="error"
        data-test="project-explorer-error"
      >
        <template #action>
          <Button size="sm" data-test="project-explorer-retry" @click="refresh">
            {{ t('gui.designer.project_explorer.retry') }}
          </Button>
        </template>
      </ErrorState>
      <template v-else-if="loaded">
        <!-- Same contract as the Dashboard explorer (0449 T0004 item 1): a failed refresh
             rides ABOVE the tree it could not replace; the last good tree stays usable. -->
        <Alert
          v-if="refreshError !== null"
          class="project-explorer-refresh-error"
          tone="warning"
          :title="t('gui.designer.project_explorer.refresh_failed')"
          data-test="project-explorer-refresh-error"
        >
          {{ refreshError }}
          <template #action>
            <Button size="sm" variant="ghost" data-test="project-explorer-refresh-retry" @click="refresh">
              {{ t('gui.designer.project_explorer.retry') }}
            </Button>
          </template>
        </Alert>
        <ProjectTree
          v-if="filtered.project"
          :project="filtered.project"
          :expanded-ids="expandedIds"
          :selection="selection"
          :active-flow-id="activeFlowId ?? null"
          :label="t('gui.designer.project_explorer.title')"
          @toggle="toggle"
          @select-project="onSelectProject"
          @select-group="onSelectGroup"
          @select-flow="onSelectFlow"
          @activate-flow="onActivateFlow"
        />
        <EmptyState
          v-else
          compact
          :title="t('gui.designer.project_explorer.no_results')"
          data-test="project-explorer-no-results"
        />
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../../components/ui/Alert.vue'
import Button from '../../../components/ui/Button.vue'
import EmptyState from '../../../components/ui/EmptyState.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import { useProjectStore, type GroupTreeNode } from '@/features/project'
import ProjectTree from './ProjectTree.vue'
import {
  buildExplorerProject,
  filterExplorerProject,
  projectNodeId,
  type ExplorerFlow,
  type ExplorerGroup,
  type ExplorerProject,
  type ExplorerSelection,
  type ProjectExplorerFlowEvent,
  type ProjectExplorerGroupEvent,
} from './projectExplorerTree'

const props = withDefaults(
  defineProps<{
    /** Project to show. Omitted (undefined) = follow the app's current Project. */
    projectId?: string | null
    /** The Flow the Designer currently has open. Owned by the Designer, not by this part. */
    activeFlowId?: string | null
    /** Bump to force a refresh from the parent (e.g. a tree change event). */
    refreshToken?: number
    /** Show final-approved / discarded Flows too (the Dashboard's "show" variant). */
    includeTerminal?: boolean
  }>(),
  {
    projectId: undefined,
    activeFlowId: null,
    refreshToken: 0,
    includeTerminal: false,
  },
)

const emit = defineEmits<{
  'select-group': [payload: ProjectExplorerGroupEvent]
  'select-flow': [payload: ProjectExplorerFlowEvent]
  'activate-flow': [payload: ProjectExplorerFlowEvent]
}>()

const { t } = useI18n()
const projectStore = useProjectStore()

// The Designer has no Project concept of its own (R0001 §2.1): by default this is the app's
// current Project from features/project.
const projectId = computed<string | null>(() =>
  props.projectId === undefined ? projectStore.currentProjectId : props.projectId,
)

// The canonical flat tree, exactly as the project store returns it. Everything the user
// sees is derived from this + the query; nothing else is stored.
const nodes = ref<GroupTreeNode[]>([])
const loaded = ref(false)
const loading = ref(false)
const refreshing = ref(false)
// `error` blocks because nothing is on screen yet; `refreshError` does not, because a tree
// is already rendered and stays usable (same split as the Dashboard explorer).
const error = ref<string | null>(null)
const refreshError = ref<string | null>(null)

const query = ref('')
const isSearching = computed(() => query.value.trim().length > 0)

const selection = ref<ExplorerSelection>(null)

const rootNodeId = computed(() => (projectId.value ? projectNodeId(projectId.value) : null))
const projectName = computed(() =>
  projectStore.projects.find((p) => p.id === projectId.value)?.name ?? projectId.value,
)

const displayProject = computed<ExplorerProject | null>(() =>
  loaded.value ? buildExplorerProject(nodes.value, rootNodeId.value, projectName.value) : null,
)

const filtered = computed(() => filterExplorerProject(displayProject.value, query.value))

// ── Expansion ────────────────────────────────────────────────────────────────
// Local to this instance: it is view state, not project context. The Project root starts
// open; Groups start closed.
const expanded = ref<Record<string, boolean>>({})
// While a query is active the matched rows' ancestors are opened automatically. A toggle
// made during the search is remembered only for that query, so clearing it brings back
// exactly the tree the user had before searching.
const searchOverrides = ref<Record<string, boolean>>({})

function baseExpanded(id: string): boolean {
  const value = expanded.value[id]
  return value === undefined ? id === displayProject.value?.id : value
}

function isExpanded(id: string): boolean {
  if (!isSearching.value) return baseExpanded(id)
  const override = searchOverrides.value[id]
  if (override !== undefined) return override
  return filtered.value.revealIds.has(id) || baseExpanded(id)
}

const expandedIds = computed(() => {
  const ids = new Set<string>()
  const project = filtered.value.project
  if (!project) return ids
  if (isExpanded(project.id)) ids.add(project.id)
  for (const group of project.groups) if (isExpanded(group.id)) ids.add(group.id)
  return ids
})

function toggle(id: string) {
  const next = !isExpanded(id)
  if (isSearching.value) searchOverrides.value = { ...searchOverrides.value, [id]: next }
  else expanded.value = { ...expanded.value, [id]: next }
}

watch(query, () => {
  searchOverrides.value = {}
})

// ── Selection / activation (reported upward only) ────────────────────────────

function onSelectProject(project: ExplorerProject) {
  selection.value = { kind: 'project', id: project.id }
}

function onSelectGroup(group: ExplorerGroup) {
  selection.value = { kind: 'group', id: group.id }
  if (projectId.value) emit('select-group', { projectId: projectId.value, groupId: group.id })
}

function flowEvent(flow: ExplorerFlow): ProjectExplorerFlowEvent | null {
  if (!projectId.value) return null
  return { projectId: projectId.value, groupId: flow.groupId, flowId: flow.id, flowName: flow.name }
}

function onSelectFlow(flow: ExplorerFlow) {
  selection.value = { kind: 'flow', id: flow.id }
  const payload = flowEvent(flow)
  if (payload) emit('select-flow', payload)
}

// Double click / Enter. The Flow is also selected (the user pointed at it), but whether it
// becomes the ACTIVE Flow is the Designer's call — it answers through `activeFlowId`.
function onActivateFlow(flow: ExplorerFlow) {
  selection.value = { kind: 'flow', id: flow.id }
  const payload = flowEvent(flow)
  if (payload) emit('activate-flow', payload)
}

// ── Loading ──────────────────────────────────────────────────────────────────
// Data comes from the project store's fetchGroupTree (cache + inflight join), the one tree
// request path v0.2 screens share. The first load may answer from the store cache;
// refresh/retry/refreshToken force a fresh read.

let requestSeq = 0

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('gui.designer.project_explorer.load_failed')
}

async function load(force: boolean) {
  const pid = projectId.value
  if (!pid) return
  const seq = ++requestSeq
  const silent = loaded.value
  if (silent) refreshing.value = true
  else {
    loading.value = true
    error.value = null
  }
  try {
    const next = await projectStore.fetchGroupTree(pid, { force, includeTerminal: props.includeTerminal })
    if (seq !== requestSeq || pid !== projectId.value) return
    nodes.value = next
    loaded.value = true
    error.value = null
    refreshError.value = null
  } catch (err) {
    if (seq !== requestSeq || pid !== projectId.value) return
    if (silent) refreshError.value = messageOf(err)
    else error.value = messageOf(err)
  } finally {
    if (seq === requestSeq) {
      loading.value = false
      refreshing.value = false
    }
  }
}

function refresh() {
  void load(true)
}

watch(
  [projectId, () => props.includeTerminal],
  ([pid], prev) => {
    const projectChanged = !prev || prev[0] !== pid
    if (projectChanged) {
      requestSeq += 1
      nodes.value = []
      loaded.value = false
      loading.value = false
      refreshing.value = false
      error.value = null
      refreshError.value = null
      selection.value = null
      expanded.value = {}
      searchOverrides.value = {}
      query.value = ''
    }
    if (pid) void load(!projectChanged)
  },
  { immediate: true },
)

watch(
  () => props.refreshToken,
  (next, prev) => {
    if (next !== prev) refresh()
  },
)

defineExpose({ refresh })
</script>

<style scoped>
.project-explorer {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  color: var(--fg2-text);
  font-family: var(--fg2-font);
}

.project-explorer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 8px 4px 12px;
}

.project-explorer-title {
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fg2-muted);
}

.project-explorer-head svg.is-spinning {
  animation: project-explorer-spin 0.8s linear infinite;
}

.project-explorer-search {
  position: relative;
  display: flex;
  align-items: center;
  padding: 4px 8px 8px;
}

.project-explorer-search :deep(.ui-input) {
  height: var(--fg2-control-h-sm);
  padding-right: 32px;
}

.project-explorer-search-clear {
  position: absolute;
  right: 10px;
  width: 24px;
  height: 24px;
}

.project-explorer-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 4px 8px;
}

.project-explorer-refresh-error {
  margin: 0 4px 6px;
}

@keyframes project-explorer-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
