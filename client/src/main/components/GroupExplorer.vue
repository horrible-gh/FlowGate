<template>
  <div class="sdb-panel" role="tree" :aria-label="t('main.explorer.groups')">
    <div class="sdb-ph">
      <span class="sdb-ph-title">{{ t('main.explorer.groups') }}</span>
      <div class="sdb-ph-acts">
        <button
          class="sdb-act-btn"
          type="button"
          :title="`${t('main.requirement.create.nav_label')} (Alt+N)`"
          @click="$emit('create-requirement')"
        >
          <AppIcon name="plus" />
        </button>
        <button
          class="sdb-act-btn"
          type="button"
          :aria-pressed="!showFinalApprovedGroups"
          :aria-label="finalApprovedToggleLabel"
          :title="finalApprovedToggleLabel"
          @click="toggleFinalApproved"
        >
          <AppIcon :name="showFinalApprovedGroups ? 'eye' : 'eye-slash'" />
        </button>
        <button
          class="sdb-act-btn"
          type="button"
          data-test="explorer-search-toggle"
          :class="{ active: showSearch }"
          :aria-pressed="showSearch"
          :aria-label="t('main.explorer.search_toggle')"
          :title="t('main.explorer.search_toggle')"
          @click="toggleSearch"
        >
          <AppIcon name="funnel" />
        </button>
      </div>
    </div>
    <!-- In-explorer document search (group 0123 R0001). Hidden by default; the
         filter button above reveals this box (rev2). Typing filters the explorer to
         the matched documents directly — no separate header page. By default it
         matches title + doc_id; the "search content" checkbox switches to the
         backend body-search endpoint. Scoped to the current project. -->
    <div v-if="showSearch" class="sdb-search">
      <div class="sdb-search-row">
        <AppIcon name="magnifying-glass" class="sdb-search-ico" />
        <input
          ref="searchInputEl"
          v-model="searchQuery"
          type="search"
          class="sdb-search-input"
          data-test="explorer-search-input"
          :placeholder="t('main.explorer.search_placeholder')"
          :disabled="!projectId"
        />
        <button
          v-if="isSearching"
          type="button"
          class="sdb-search-clear"
          data-test="explorer-search-clear"
          :aria-label="t('main.explorer.search_clear')"
          :title="t('main.explorer.search_clear')"
          @click="clearSearch"
        >
          <AppIcon name="x" />
        </button>
      </div>
      <label class="sdb-search-opt" data-test="explorer-search-content-label">
        <input
          v-model="searchContent"
          type="checkbox"
          data-test="explorer-search-content"
          :disabled="!projectId"
        />
        <span>{{ t('main.explorer.search_content_label') }}</span>
      </label>
    </div>
    <div v-if="!isSearching" class="sdb-filter">
      <button
        class="sdb-filt"
        :class="{ active: activeFilter === 'all' }"
        @click="activeFilter = 'all'"
      >{{ t('main.explorer.filter_all') }}</button>
      <button
        v-for="type in displayTypes"
        :key="type"
        class="sdb-filt"
        :class="{ active: activeFilter === type }"
        @click="activeFilter = type"
      >{{ type }}</button>
    </div>
    <div class="sdb-scroll">
      <!-- Search results take over the explorer body while a query is active. -->
      <template v-if="isSearching">
        <div v-if="searchLoading" class="sdb-state" data-test="explorer-search-loading">⏳ ...</div>
        <div v-else-if="searchError" class="sdb-state sdb-state--error" data-test="explorer-search-error">
          <span>{{ searchError }}</span>
        </div>
        <div
          v-else-if="searchSearched && searchResultRows.length === 0"
          class="sdb-state"
          data-test="explorer-search-empty"
        >
          {{ t('main.explorer.search_no_results') }}
        </div>
        <ul v-else class="sdb-results" data-test="explorer-search-results">
          <li
            v-for="row in searchResultRows"
            :key="row.docId"
            class="sdb-result"
            data-test="explorer-search-result"
            @click="openSearchResult(row)"
          >
            <div class="sdb-result-head">
              <span v-if="row.typeCode" class="doc-tag" :class="`c-${row.typeCode}`">{{ row.typeCode }}</span>
              <span class="sdb-result-id" data-test="explorer-search-result-id">{{ row.docId }}</span>
            </div>
            <div v-if="row.title" class="sdb-result-title" data-test="explorer-search-result-title">{{ row.title }}</div>
            <p v-if="row.snippet" class="sdb-result-snippet" data-test="explorer-search-snippet">{{ row.snippet }}</p>
          </li>
        </ul>
      </template>
      <template v-else>
        <div v-if="loading" class="sdb-state">⏳ ...</div>
        <div v-else-if="error" class="sdb-state sdb-state--error">
          <span>{{ t('main.error.tree_load_failed') }}</span>
          <button @click="() => reload()">{{ t('main.explorer.retry') }}</button>
        </div>
        <div v-else-if="!projectId" class="sdb-state">
          {{ t('main.state.no_project') }}
        </div>
        <ul v-else class="tree-ul">
          <GroupTreeNode
            v-for="node in rootNodes"
            :key="node.id"
            :node="node"
            :all-nodes="filteredNodes"
            :tree-nodes="nodes"
            :project-id="projectId ?? ''"
            @open="openDocument"
            @tree-changed="handleTreeChanged"
            @create-requirement="$emit('create-requirement', $event)"
          />
        </ul>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore, type GroupNode } from '../stores/explorer'
import { useTabsStore } from '../stores/tabs'
import { useDocumentSearch } from '../composables/useDocumentSearch'
import GroupTreeNode from './GroupTreeNode.vue'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{ projectId: string | null }>()
defineEmits<{ 'create-requirement': [payload?: { groupId?: string }] }>()
const { t } = useI18n()
const explorerStore = useExplorerStore()
const tabsStore = useTabsStore()

const nodes = ref<GroupNode[]>([])
const loading = ref(false)
const error = ref(false)
const activeFilter = ref<string>('all')
const showFinalApprovedGroups = ref(false)

// --- In-explorer document search (group 0123 R0001) -------------------------
// The reactive query/results live in the shared useDocumentSearch composable so
// the explorer and any future surface match the same backend semantics. Typing
// debounces a content-mode search scoped to the current project; results replace
// the tree in the explorer body until the box is cleared.
const {
  query: searchQuery,
  results: searchResults,
  loading: searchLoading,
  error: searchError,
  searched: searchSearched,
  search: runSearch,
  reset: resetSearch,
} = useDocumentSearch()

// The search box is hidden until the filter button is pressed (rev2 — the user
// asked for search to be off by default and revealed on demand).
const showSearch = ref(false)
const searchInputEl = ref<HTMLInputElement | null>(null)
// "내용까지 검색" checkbox: off → title/doc_id only (meta endpoint), on → body
// full-text (content endpoint). Default off so the cheap meta search is the norm.
const searchContent = ref(false)

const isSearching = computed(() => searchQuery.value.trim().length > 0)

let searchDebounce: ReturnType<typeof setTimeout> | null = null

function toggleSearch() {
  showSearch.value = !showSearch.value
  if (!showSearch.value) {
    // Hiding the box returns the explorer to the tree — drop any active query.
    clearSearch()
  } else {
    void nextTick(() => searchInputEl.value?.focus())
  }
}

interface SearchRow {
  docId: string
  typeCode: string | null
  title: string
  snippet: string | null
  node: GroupNode | null
}

// Resolve each backend hit against the already-loaded project tree so opening a
// result reuses the node's real md_path (correct viewer load). Hits not present
// in the loaded tree (e.g. a hidden final-approved group) still render and fall
// back to an id-based open. The row shows the document's full id and title
// (rev4 — the user asked for the full doc id + title name, not number:filename).
const searchResultRows = computed<SearchRow[]>(() =>
  searchResults.value.map((r) => {
    const node = nodes.value.find((n) => n.node_type === 'document' && n.id === r.doc_id) ?? null
    return {
      docId: r.doc_id,
      typeCode: node?.type_code ?? r.type ?? null,
      title: r.title ?? '',
      snippet: r.snippet ?? null,
      node,
    }
  }),
)

function triggerSearch() {
  const mode = searchContent.value ? 'content' : 'meta'
  void runSearch({ project: props.projectId || undefined }, 100, 0, mode)
}

watch(searchQuery, () => {
  if (searchDebounce) clearTimeout(searchDebounce)
  if (!searchQuery.value.trim()) {
    // Cleared box → drop results immediately, no round-trip.
    resetSearch()
    return
  }
  searchDebounce = setTimeout(triggerSearch, 250)
})

// Flipping "내용까지 검색" re-runs the active query right away against the new
// endpoint (meta ↔ content) so the result set reflects the scope immediately.
watch(searchContent, () => {
  if (searchDebounce) clearTimeout(searchDebounce)
  if (searchQuery.value.trim()) triggerSearch()
})

function clearSearch() {
  if (searchDebounce) clearTimeout(searchDebounce)
  resetSearch()
}

function openSearchResult(row: SearchRow) {
  if (row.node) {
    openDocument(row.node)
    return
  }
  // Fallback for a hit outside the loaded tree: open by id; MdViewer resolves the
  // body from typeCode (same pattern as GroupExplorer's file-less doc open).
  tabsStore.openTab({
    id: row.docId,
    title: row.title || row.docId,
    path: '',
    type: 'unsupported',
    typeCode: row.typeCode ?? undefined,
    projectId: props.projectId ?? undefined,
  })
}

// Single visibility toggle: hides/reveals BOTH terminal group kinds — final-approved
// (AC) and discarded (DC). A separate discard toggle was rejected (r4): pressing
// "show groups" surfaced only AC because DC needed a second control. One toggle now
// governs both. The storage key keeps the established final-approved name.
const finalApprovedToggleLabel = computed(() =>
  showFinalApprovedGroups.value
    ? t('main.explorer.hide_final_approved')
    : t('main.explorer.show_final_approved'),
)

const storageKey = (pid: string) => `flowgate:show-final-approved-groups:${pid}`

function loadShowFinalApproved(pid: string | null) {
  if (!pid) {
    showFinalApprovedGroups.value = false
    return
  }
  try {
    const raw = localStorage.getItem(storageKey(pid))
    showFinalApprovedGroups.value = raw === null ? false : raw === '1'
  } catch {
    showFinalApprovedGroups.value = false
  }
}

function persistShowFinalApproved() {
  if (!props.projectId) return
  try {
    localStorage.setItem(storageKey(props.projectId), showFinalApprovedGroups.value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

function toggleFinalApproved() {
  showFinalApprovedGroups.value = !showFinalApprovedGroups.value
  persistShowFinalApproved()
}

// True when the node sits inside a hidden terminal group — final-approved (AC) or
// discarded (DC) — i.e. the node itself or any ancestor group carries either flag.
function isInsideFinalApprovedGroup(nodeId: string, list: GroupNode[]): boolean {
  let node: GroupNode | undefined = list.find((n) => n.id === nodeId)
  while (node) {
    if (node.node_type === 'group' && (node.is_final_approved === true || node.is_discarded === true)) return true
    const parentId = node.parent_id
    if (!parentId) break
    node = list.find((n) => n.id === parentId)
  }
  return false
}

// Step 1 of the filter pipeline (D0002 §6): apply the show-final-approved
// setting before the document-type filter. Hidden state removes final-approved
// group nodes and every descendant document/subgroup; project and module nodes
// are kept even when they become empty.
const baseNodes = computed<GroupNode[]>(() => {
  const hidden = new Set<string>()
  nodes.value.forEach((n) => {
    if (n.node_type !== 'group') return
    // One toggle hides/reveals BOTH terminal kinds — final-approved (AC) and
    // discarded (DC). Two separate toggles caused "show all groups → only AC
    // appears, DC stays hidden" (review r4). Default hidden, like AC.
    if (!showFinalApprovedGroups.value && (n.is_final_approved === true || n.is_discarded === true)) {
      hidden.add(n.id)
    }
  })
  if (hidden.size === 0) return nodes.value
  let changed = true
  while (changed) {
    changed = false
    nodes.value.forEach((n) => {
      if (!hidden.has(n.id) && n.parent_id && hidden.has(n.parent_id)) {
        hidden.add(n.id)
        changed = true
      }
    })
  }
  return nodes.value.filter((n) => !hidden.has(n.id))
})

const availableTypes = computed(() => {
  const types = new Set<string>()
  baseNodes.value.forEach((n) => {
    if (n.node_type === 'document' && n.type_code) types.add(n.type_code)
  })
  return Array.from(types).sort()
})

const displayTypes = computed(() =>
  availableTypes.value.length > 0 ? availableTypes.value : ['R', 'DS', 'T'],
)

const filteredNodes = computed(() => {
  const base = baseNodes.value
  if (activeFilter.value === 'all') return base
  const visibleIds = new Set<string>()
  base.forEach((n) => {
    if (n.node_type === 'document' && n.type_code === activeFilter.value) {
      visibleIds.add(n.id)
      let parentId = n.parent_id
      while (parentId) {
        visibleIds.add(parentId)
        const parent = base.find((p) => p.id === parentId)
        parentId = parent?.parent_id ?? null
      }
    }
  })
  return base.filter((n) => visibleIds.has(n.id))
})

const rootNodes = computed(() => filteredNodes.value.filter((n) => n.parent_id === null))

function expandAncestors(targetNodeId: string, nextNodes: GroupNode[]) {
  let node = nextNodes.find((n) => n.id === targetNodeId)
  let parentId = node?.parent_id ?? null
  while (parentId) {
    try {
      localStorage.setItem(`flowgate:grp-exp:${props.projectId}:${parentId}`, '1')
    } catch {
      /* ignore */
    }
    node = nextNodes.find((n) => n.id === parentId)
    parentId = node?.parent_id ?? null
  }
}

async function reload(revealNodeId?: string) {
  if (!props.projectId) return
  loading.value = true
  error.value = false
  try {
    const nextNodes = await explorerStore.fetchGroupTree(props.projectId, true)
    if (revealNodeId) {
      activeFilter.value = 'all'
      // User action wins over the hide setting: if the reveal target lives in a
      // final-approved group, force the toggle on so it is visible (D0002 §7).
      if (!showFinalApprovedGroups.value && isInsideFinalApprovedGroup(revealNodeId, nextNodes)) {
        showFinalApprovedGroups.value = true
        persistShowFinalApproved()
      }
      expandAncestors(revealNodeId, nextNodes)
    }
    nodes.value = nextNodes
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

async function handleTreeChanged(revealNodeId?: string) {
  await reload(revealNodeId)
}

// A group disposed/renamed from the document header (DocHeader ⋯ menu) lives outside
// this tree, so it can't emit 'tree-changed' up to us. It dispatches a window event
// instead; reload the tree so the change shows up without a manual refresh. (R0029.0001)
function onGroupTreeChanged() {
  void reload()
}
onMounted(() => window.addEventListener('fg:group_tree_changed', onGroupTreeChanged))
onBeforeUnmount(() => {
  window.removeEventListener('fg:group_tree_changed', onGroupTreeChanged)
  if (searchDebounce) clearTimeout(searchDebounce)
})

function openDocument(node: GroupNode) {
  if (node.node_type !== 'document') return
  if (!node.has_md) {
    tabsStore.openTab({ id: node.id, title: node.label, path: node.md_path ?? '', type: 'unsupported', typeCode: node.type_code, projectId: props.projectId })
    return
  }
  tabsStore.openTab({
    id: node.id,
    title: node.label,
    path: node.md_path ?? '',
    type: node.type_code === 'Q' ? 'qtui' : 'md',
    mdPath: node.md_path,
    typeCode: node.type_code,
    projectId: props.projectId,
  })
}

watch(() => props.projectId, async (pid) => {
  // Re-read the per-project show-final-approved setting whenever the project changes.
  loadShowFinalApproved(pid)
  // A search is scoped to one project; switching projects clears the stale query.
  clearSearch()
  if (!pid) { nodes.value = []; return }
  loading.value = true
  error.value = false
  try {
    nodes.value = await explorerStore.fetchGroupTree(pid, true)
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}, { immediate: true })
</script>

<style scoped>
/* Pressed state for the filter button while the search box is revealed. */
.sdb-act-btn.active {
  background: rgba(37, 99, 235, 0.3);
  color: #fff;
}
.sdb-search {
  padding: 6px 8px;
}
.sdb-search-row {
  position: relative;
  display: flex;
  align-items: center;
}
.sdb-search-ico {
  position: absolute;
  left: 8px;
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.45);
  pointer-events: none;
}
.sdb-search-input {
  flex: 1;
  min-width: 0;
  padding: 6px 26px 6px 26px;
  font-size: 0.82rem;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  /* The sidebar is dark navy; without an explicit light color the input text
     falls back to the near-black --text and is invisible (rev2 bug #2). */
  color: rgba(255, 255, 255, 0.92);
}
.sdb-search-input::placeholder {
  color: rgba(255, 255, 255, 0.4);
}
.sdb-search-input:focus {
  outline: none;
  border-color: var(--primary, #2563eb);
  background: rgba(255, 255, 255, 0.12);
}
.sdb-search-input:disabled {
  opacity: 0.5;
}
.sdb-search-opt {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding-left: 2px;
  font-size: 0.76rem;
  color: rgba(255, 255, 255, 0.72);
  cursor: pointer;
  user-select: none;
}
.sdb-search-opt input {
  cursor: pointer;
  accent-color: var(--primary, #2563eb);
}
.sdb-search-clear {
  position: absolute;
  right: 8px;
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  opacity: 0.8;
  cursor: pointer;
}
.sdb-search-clear:hover {
  opacity: 1;
}
.sdb-results {
  list-style: none;
  margin: 0;
  padding: 4px 0;
}
.sdb-result {
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.06));
  transition: background 0.12s ease;
  /* The sidebar is dark navy; without an explicit light color the result text
     falls back to the near-black body --text and is invisible — the same bug as
     the input box, left on the result rows (rev3). Set a light base here so the
     label and snippet both inherit a readable color. */
  color: rgba(255, 255, 255, 0.85);
}
.sdb-result:hover {
  background: var(--hover, rgba(255, 255, 255, 0.06));
  color: #fff;
}
.sdb-result-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.sdb-result-id {
  /* Full document id (e.g. flowgate.default.0123.0007-TR) — monospace so the id
     reads as an identifier, not prose. Use the app-wide 'JetBrains Mono' stack
     (the same font every other id surface uses: .doc-id-badge, DocInfoPanel,
     NextActionModal). rev4 listed only Apple fonts (ui-monospace/SFMono/Menlo)
     before the generic fallback, so on Windows it dropped to the default
     Courier-style `monospace` and looked ugly (rev5 — "폰트가 예쁘지 않다"). The
     id is a subordinate caption now: smaller, dimmed, slight letter-spacing. */
  font-size: 0.7rem;
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  letter-spacing: 0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(255, 255, 255, 0.55);
}
.sdb-result-title {
  /* Document title name — the visual lead. Inherits the body 'Inter' sans stack;
     semibold + tighter line-height so the title reads cleanly above the id
     caption and snippet (rev5 typography polish). */
  margin-top: 3px;
  font-size: 0.84rem;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: 0.005em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(255, 255, 255, 0.95);
}
.sdb-result-snippet {
  margin: 4px 0 0;
  font-size: 0.74rem;
  line-height: 1.5;
  /* Was opacity:0.7 on inherited near-black text — still black. Use an explicit
     dimmed-white instead so the snippet is legible on the dark sidebar. */
  color: rgba(255, 255, 255, 0.6);
}
</style>
