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
          <i class="fa-solid fa-plus"></i>
        </button>
        <button
          class="sdb-act-btn"
          type="button"
          :aria-pressed="!showFinalApprovedGroups"
          :aria-label="finalApprovedToggleLabel"
          :title="finalApprovedToggleLabel"
          @click="toggleFinalApproved"
        >
          <i :class="showFinalApprovedGroups ? 'fa-solid fa-eye' : 'fa-solid fa-eye-slash'"></i>
        </button>
        <button class="sdb-act-btn" :title="t('main.group_explorer.tooltip_14')">
          <i class="fa-solid fa-filter"></i>
        </button>
      </div>
    </div>
    <div class="sdb-filter">
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
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore, type GroupNode } from '../stores/explorer'
import { useTabsStore } from '../stores/tabs'
import GroupTreeNode from './GroupTreeNode.vue'

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
onBeforeUnmount(() => window.removeEventListener('fg:group_tree_changed', onGroupTreeChanged))

function openDocument(node: GroupNode) {
  if (node.node_type !== 'document') return
  if (!node.has_md) {
    tabsStore.openTab({ id: node.id, title: node.label, path: node.md_path ?? '', type: 'unsupported', typeCode: node.type_code })
    return
  }
  tabsStore.openTab({
    id: node.id,
    title: node.label,
    path: node.md_path ?? '',
    type: node.type_code === 'Q' ? 'qtui' : 'md',
    mdPath: node.md_path,
    typeCode: node.type_code,
  })
}

watch(() => props.projectId, async (pid) => {
  // Re-read the per-project show-final-approved setting whenever the project changes.
  loadShowFinalApproved(pid)
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
