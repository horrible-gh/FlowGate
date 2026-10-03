<template>
  <!-- flowgate.v02.0023 — Designer Project Explorer (R0001 / D0002 §3).
       Shows the current Project as Project > Group > Flow and reports selection /
       activation upward. It never opens tabs, touches the Canvas, or owns the left
       dock's tab switch / collapse / resize — those belong to the Designer around it. -->
  <section class="sdb-panel pe-panel" data-test="project-explorer">
    <div class="sdb-ph">
      <span class="sdb-ph-title">{{ t('main.project_explorer.title') }}</span>
      <div class="sdb-ph-acts">
        <button
          class="sdb-act-btn"
          type="button"
          data-test="project-explorer-refresh"
          :disabled="!projectId"
          :aria-label="t('main.project_explorer.refresh')"
          :title="t('main.project_explorer.refresh')"
          @click="refresh"
        >
          <AppIcon name="arrows-clockwise" :spin="refreshing" />
        </button>
      </div>
    </div>

    <div class="pe-search">
      <AppIcon name="magnifying-glass" class="pe-search-ico" />
      <input
        v-model="query"
        type="search"
        class="pe-search-input"
        data-test="project-explorer-search"
        :aria-label="t('main.project_explorer.search_placeholder')"
        :placeholder="t('main.project_explorer.search_placeholder')"
        :disabled="!projectId"
      />
      <button
        v-if="isSearching"
        type="button"
        class="pe-search-clear"
        data-test="project-explorer-search-clear"
        :aria-label="t('main.project_explorer.search_clear')"
        :title="t('main.project_explorer.search_clear')"
        @click="query = ''"
      >
        <AppIcon name="x" />
      </button>
    </div>

    <div class="sdb-scroll">
      <div v-if="!projectId" class="sdb-state" data-test="project-explorer-no-project">
        {{ t('main.project_explorer.no_project') }}
      </div>
      <div v-else-if="loading" class="sdb-state" data-test="project-explorer-loading">
        {{ t('main.project_explorer.loading') }}
      </div>
      <div v-else-if="error" class="sdb-state sdb-state--error" data-test="project-explorer-error">
        <span>{{ t('main.project_explorer.load_failed') }}</span>
        <button type="button" data-test="project-explorer-retry" @click="refresh">
          {{ t('main.project_explorer.retry') }}
        </button>
      </div>
      <template v-else-if="loaded">
        <!-- Same contract as the Dashboard explorer (0449 T0004 item 1): a failed refresh
             rides ABOVE the tree it could not replace; the last good tree stays usable. -->
        <div
          v-if="refreshError"
          class="sdb-state sdb-state--refresh-error"
          data-test="project-explorer-refresh-error"
        >
          <span>{{ t('main.project_explorer.refresh_failed') }}</span>
          <button type="button" data-test="project-explorer-refresh-retry" @click="refresh">
            {{ t('main.project_explorer.retry') }}
          </button>
        </div>
        <ProjectTree
          v-if="filtered.project"
          :project="filtered.project"
          :expanded-ids="expandedIds"
          :selection="selection"
          :active-flow-id="activeFlowId ?? null"
          :label="t('main.project_explorer.title')"
          @toggle="toggle"
          @select-project="onSelectProject"
          @select-group="onSelectGroup"
          @select-flow="onSelectFlow"
          @activate-flow="onActivateFlow"
        />
        <div v-else class="sdb-state" data-test="project-explorer-no-results">
          {{ t('main.project_explorer.no_results') }}
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import ProjectTree from './ProjectTree.vue'
import { useExplorerStore, type GroupNode } from '../../stores/explorer'
import { useProjectStore } from '../../stores/project'
import {
  buildExplorerProject,
  filterExplorerProject,
  type ExplorerFlow,
  type ExplorerGroup,
  type ExplorerProject,
  type ExplorerSelection,
  type ProjectExplorerFlowEvent,
  type ProjectExplorerGroupEvent,
} from '../../utils/projectExplorerTree'

const props = withDefaults(
  defineProps<{
    /** Project to show. Omitted (undefined) = follow the app's current Project. */
    projectId?: string | null
    /** The Flow the Designer currently has open. Owned by the Designer, not by this part. */
    activeFlowId?: string | null
    /** Bump to force a refresh from the parent (e.g. an SSE tree change). */
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
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()

const projectId = computed<string | null>(() =>
  props.projectId === undefined ? projectStore.currentProjectId : props.projectId,
)

// The canonical flat tree, exactly as the explorer store returns it. Everything the
// user sees is derived from this + the query; nothing else is stored.
const nodes = ref<GroupNode[]>([])
const loaded = ref(false)
const loading = ref(false)
const refreshing = ref(false)
// Same split as GroupExplorer (0449 T0004 item 1): `error` blocks because nothing is on
// screen yet; `refreshError` does not, because a tree is already rendered.
const error = ref(false)
const refreshError = ref(false)

const query = ref('')
const isSearching = computed(() => query.value.trim().length > 0)

const selection = ref<ExplorerSelection>(null)

const projectNodeId = computed(() => (projectId.value ? `project:${projectId.value}` : null))
const projectName = computed(() =>
  projectStore.projects.find((p) => p.project_id === projectId.value)?.project_name ?? projectId.value,
)

const displayProject = computed<ExplorerProject | null>(() =>
  loaded.value ? buildExplorerProject(nodes.value, projectNodeId.value, projectName.value) : null,
)

const filtered = computed(() => filterExplorerProject(displayProject.value, query.value))

// ── Expansion ────────────────────────────────────────────────────────────────
// Kept local to this instance on purpose: the Dashboard tree persists its own expansion
// per v0.1 node id, and sharing it would make opening a Group here re-shape the Dashboard
// explorer (R0001 §3.8). The Project root starts open; Groups start closed.
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
// Data comes from the explorer store's fetchGroupTree — the same fetch/cache/inflight-join
// /retry path the Dashboard explorer uses, so the Designer adds no second tree request
// path. The first load may answer from the store cache; refresh/retry force a fresh read.

let requestSeq = 0

async function load(force: boolean) {
  const pid = projectId.value
  if (!pid) return
  const seq = ++requestSeq
  const silent = loaded.value
  if (silent) refreshing.value = true
  else {
    loading.value = true
    error.value = false
  }
  try {
    const next = await explorerStore.fetchGroupTree(pid, force, props.includeTerminal)
    if (seq !== requestSeq || pid !== projectId.value) return
    nodes.value = next
    loaded.value = true
    error.value = false
    refreshError.value = false
  } catch {
    if (seq !== requestSeq || pid !== projectId.value) return
    if (silent) refreshError.value = true
    else error.value = true
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
      error.value = false
      refreshError.value = false
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
.pe-search {
  position: relative;
  display: flex;
  align-items: center;
  padding: 6px 8px;
}
.pe-search-ico {
  position: absolute;
  left: 16px;
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.45);
  pointer-events: none;
}
.pe-search-input {
  flex: 1;
  min-width: 0;
  padding: 5px 26px;
  font-size: 0.8rem;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.92);
}
.pe-search-input::placeholder {
  color: rgba(255, 255, 255, 0.4);
}
.pe-search-input:focus {
  outline: none;
  border-color: var(--primary, #2563eb);
  background: rgba(255, 255, 255, 0.12);
}
.pe-search-input:disabled {
  opacity: 0.5;
}
.pe-search-clear {
  position: absolute;
  right: 14px;
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
}
.sdb-act-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
