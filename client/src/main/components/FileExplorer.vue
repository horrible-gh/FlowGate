<template>
  <div class="sdb-panel" role="tree" :aria-label="t('main.explorer.files')">
    <div class="sdb-ph">
      <span class="sdb-ph-title">{{ t('main.explorer.files') }}</span>
      <div class="sdb-ph-acts">
        <select
          v-if="groupSlots.length"
          v-model="selectedGroup"
          class="fx-group-select"
          :aria-label="t('main.explorer.group_select')"
          @change="onGroupChange"
        >
          <option :value="null">{{ t('main.explorer.base_branch', { branch: baseBranch }) }}</option>
          <option v-for="s in groupSlots" :key="s.group_id" :value="s.group_id">{{ groupLabel(s) }}</option>
        </select>
        <button class="sdb-act-btn" :aria-label="t('main.explorer.retry')" @click="reload">
          <AppIcon name="arrow-clockwise" />
        </button>
        <!-- Tree accordion (0245 R0001). Sits left of the panel toggle so that
             control stays the rightmost anchor it has always been. Hidden while the
             panel is folded (no tree to act on) or when the tree has no folders. -->
        <button
          v-if="!layoutStore.fileExplorerCollapsed && expandableFolderIds.length > 0"
          class="sdb-act-btn"
          type="button"
          data-test="file-explorer-accordion"
          :aria-pressed="anyFolderExpanded"
          :aria-label="accordionLabel"
          :title="accordionLabel"
          @click="toggleAllFolders"
        >
          <AppIcon :name="anyFolderExpanded ? 'caret-down' : 'caret-right'" />
        </button>
        <button
          class="sdb-act-btn sdb-frame-toggle"
          type="button"
          data-test="file-explorer-panel-toggle"
          :aria-expanded="!layoutStore.fileExplorerCollapsed"
          :aria-label="layoutStore.fileExplorerCollapsed ? t('main.explorer.expand') : t('main.explorer.collapse')"
          :title="layoutStore.fileExplorerCollapsed ? t('main.explorer.expand') : t('main.explorer.collapse')"
          @click="layoutStore.toggleFileExplorer"
        >
          <AppIcon :name="layoutStore.fileExplorerCollapsed ? 'caret-down' : 'caret-up'" />
        </button>
      </div>
    </div>
    <template v-if="!layoutStore.fileExplorerCollapsed">
      <!-- 0327 T0004 (NR0003 권고 4): the badge now says WHICH of the two group views
           this is — an editable worktree or a worktree-less, fully read-only branch —
           so "why can I not create anything here" has a visible answer. -->
      <div v-if="selectedGroup" class="fx-readonly-badge" :class="{ 'fx-readonly-badge--rw': groupWritable }">
        <AppIcon :name="groupWritable ? 'pencil-simple' : 'eye'" />
        <span>{{ groupWritable
          ? t('main.explorer.worktree_badge', { group: shortGroup(selectedGroup) })
          : t('main.explorer.readonly_badge', { group: shortGroup(selectedGroup) }) }}</span>
        <span
          v-if="groupGitState && groupGitState.ahead_count > 0"
          class="fx-git-badge"
        >{{ t('main.explorer.git_ahead', { n: groupGitState.ahead_count })
          }}<template v-if="groupGitState.status === 'awaiting_choice'"> · {{ t('main.explorer.git_awaiting') }}</template></span>
      </div>
      <div v-if="selectedGroupBusy" class="fx-readonly-badge">
        <AppIcon name="lock" />
        <span>{{ t('main.review_action_bar.ai_running_hint') }}</span>
      </div>
      <div class="sdb-scroll">
        <div v-if="loading" class="sdb-state">⏳ ...</div>
        <div v-else-if="error" class="sdb-state sdb-state--error">
          <span>{{ t('main.error.tree_load_failed') }}</span>
          <button @click="reload">{{ t('main.explorer.retry') }}</button>
        </div>
        <div v-else-if="!projectId" class="sdb-state">
          {{ t('main.state.no_project') }}
        </div>
        <ul v-else class="tree-ul">
          <li
            class="tree-node"
            @contextmenu.prevent.stop="onRootContextMenu($event)"
            @dragover.prevent="onRootDragOver"
            @dragleave="onRootDragLeave"
            @drop.capture="rootDragOver = false"
            @drop.prevent.stop="onRootDrop"
          >
            <div class="tree-row" :class="{ 'drag-over': rootDragOver }">
              <span class="tree-caret open"><span>▶</span></span>
              <span class="tree-ico proj"><AppIcon name="folder-open" /></span>
              <span class="tree-lbl tree-lbl--project">{{ projectName }}</span>
            </div>
            <ul class="tree-children">
              <FileTreeNode
                v-for="node in rootNodes"
                :key="node.id"
                :node="node"
                :all-nodes="nodes"
                :project-id="projectId ?? ''"
                :readonly="!canMutate"
                :group-id="selectedGroup"
                @open="openFile"
                @open-diff="openDiff"
                @tree-changed="reload"
              />
            </ul>
          </li>
        </ul>
      </div>
    </template>
  </div>

  <ContextMenu v-model:visible="showRootCtx" :x="rootCtxX" :y="rootCtxY">
    <template v-if="canMutate">
      <ContextMenuItem icon="folder-simple-plus" @click="openRootCreateFolder">
        {{ t('main.file_tree_node.new_folder') }}
      </ContextMenuItem>
      <ContextMenuItem icon="file-plus" @click="openRootCreateFile">
        {{ t('main.file_tree_node.new_file') }}
      </ContextMenuItem>
    </template>
    <ContextMenuItem icon="arrow-clockwise" @click="refreshFromMenu">
      {{ t('main.file_tree_node.refresh') }}
    </ContextMenuItem>
    <template v-if="canMutate">
      <ContextMenuItem icon="upload-simple" @click="triggerRootUploadFiles">
        {{ t('main.file_tree_node.upload_files') }}
      </ContextMenuItem>
      <ContextMenuItem icon="upload-simple" @click="triggerRootUploadFolder">
        {{ t('main.file_tree_node.upload_folder') }}
      </ContextMenuItem>
    </template>
  </ContextMenu>

  <input ref="rootFileInputRef" type="file" multiple style="display:none" @change="onRootFileSelected" />
  <input ref="rootFolderInputRef" type="file" multiple style="display:none" @change="onRootFolderSelected" />

  <CreateFileFolderModal
    v-if="projectId"
    v-model:visible="showModal"
    :type="modalType"
    :project-id="projectId"
    parent-path=""
    :group-id="selectedGroup"
    @saved="onRootCreated"
  />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore, type FileNode } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useLayoutStore } from '../stores/layout'
import { useTabsStore } from '../stores/tabs'
import { useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import { useFileUpload } from '../composables/useFileUpload'
import api from '@shared/api'
import FileTreeNode from './FileTreeNode.vue'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import CreateFileFolderModal from './CreateFileFolderModal.vue'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{ projectId: string | null; refreshToken?: number }>()
const { t } = useI18n()
const explorerStore = useExplorerStore()
const layoutStore = useLayoutStore()
const projectStore = useProjectStore()
const tabsStore = useTabsStore()
const aiInvokeRunsStore = useAiInvokeRunsStore()
const { collectDropFiles, uploadFiles } = useFileUpload()

const nodes = ref<FileNode[]>([])
const loading = ref(false)
const error = ref(false)
const showRootCtx = ref(false)
const rootCtxX = ref(0)
const rootCtxY = ref(0)
const showModal = ref(false)
const modalType = ref<'folder' | 'file'>('folder')
const rootFileInputRef = ref<HTMLInputElement | null>(null)
const rootFolderInputRef = ref<HTMLInputElement | null>(null)
const rootDragOver = ref(false)

// ── Group-branch (checkout-free) explorer (0186 P0005) ───────────────────────
// selectedGroup = null → base checkout (existing behaviour); a group_id → that
// group's branch is read straight from Git objects (read-only, no checkout switch).
const baseBranch = computed(() => projectStore.currentBranch || 'main')
// `writable` (0327 T0004): the server tells us whether this slot still has a live
// worktree behind it, so the explorer stops equating "a group is selected" with
// "read-only" (B0001 / NR0003 발견 3). Optional so a pre-0327 server payload simply
// reads as not-writable — the old, fully read-only behaviour.
const groupSlots = ref<Array<{ group_id: string; branch: string; status: string; writable?: boolean }>>([])
// selectedGroup restores from the store so an SSE-driven explorer remount
// (group_view_refresh → refreshAll bumps explorerRefreshKey) keeps the group
// tree in view instead of silently reverting to base (L0006 §2.4, 0186 finding 3).
// A genuine project switch clears it (watch guard + loadGroupSlots safety net).
const selectedGroup = ref<string | null>(explorerStore.activeGroupBranch)
const groupCommit = ref<string | null>(null)
// P0005 §9 — group Git status badge (reuses the finalize GET, no new field).
const groupGitState = ref<{ ahead_count: number; status: string } | null>(null)

function shortGroup(gid: string): string {
  return gid.split('.').pop() ?? gid
}

// 0327 T0004 (B0001 / NR0003 권고 1·5) — the selected group has a live worktree, so
// creating and uploading land in that worktree. A group without one stays entirely
// read-only, and base (no group selected) is unchanged.
const groupWritable = computed(() =>
  !!selectedGroup.value
  && groupSlots.value.some((s) => s.group_id === selectedGroup.value && s.writable === true),
)

const selectedGroupBusy = computed(() => {
  const groupId = selectedGroup.value
  return !!groupId && (
    aiInvokeRunsStore.isGroupRunning(groupId)
    || aiInvokeRunsStore.isGroupInlineVisible(groupId)
  )
})

// The single gate for the structural mutations (new folder / new file / upload).
const canMutate = computed(() =>
  (!selectedGroup.value || groupWritable.value) && !selectedGroupBusy.value,
)

watch(selectedGroupBusy, (busy) => {
  if (busy) {
    showModal.value = false
    rootDragOver.value = false
  }
})

function groupLabel(s: { group_id: string; status: string }): string {
  const n = shortGroup(s.group_id)
  return s.status && s.status !== 'none' ? `${n} (${s.status})` : n
}

async function loadGroupSlots(pid: string) {
  try {
    // 0282 NR0003 발견 3: fetched via the explorer store so concurrent callers
    // (header menu, status panel, SSE triggers) share one git/status request.
    const status = await explorerStore.fetchGitStatus(pid)
    groupSlots.value = Array.isArray(status?.slots)
      ? (status!.slots as Array<{ group_id: string; branch: string; status: string }>)
      : []
  } catch {
    groupSlots.value = []
  }
  if (selectedGroup.value && !groupSlots.value.some((s) => s.group_id === selectedGroup.value)) {
    selectedGroup.value = null
  }
}

// P0005 §9 — fetch the group's finalize state for the header status badge
// ("N commits ahead of base · finalize pending"). Reuses the existing finalize
// GET; failure is non-fatal (the badge simply hides).
async function loadGroupGitBadge(gid: string) {
  try {
    const res = await api.get(`/api/v1/groups/${encodeURIComponent(gid)}/git/finalize`)
    const st = (res.data as any)?.state
    groupGitState.value = st
      ? { ahead_count: Number(st.ahead_count ?? 0), status: String(st.status ?? 'none') }
      : null
  } catch {
    groupGitState.value = null
  }
}

async function onGroupChange() {
  explorerStore.activeGroupBranch = selectedGroup.value
  explorerStore.selectedFileNodeId = null
  await reload()
}

// 0192 T0005 §2-b: keep the group dropdown live off git SSE. The server
// broadcasts git_pending_changed on every slot status transition (create /
// awaiting_choice / merged&removed) and useFlowGateSse re-broadcasts it as this
// window event. Previously nothing in the explorer consumed it, so the dropdown
// only refreshed on a full remount (group_view_refresh). Re-fetching just the
// slots here updates the list in place without disturbing the current tree or
// selection. Cross-project echoes are ignored. (git_finalize_done /
// git_worktree_ready still drive a full remount via useFlowGateSse.)
function onGitSlotsMaybeChanged(e: Event) {
  const detail = (e as CustomEvent).detail as { project?: string | null } | undefined
  if (detail?.project && props.projectId && detail.project !== props.projectId) return
  if (props.projectId) loadGroupSlots(props.projectId)
}

onMounted(() => {
  if (rootFolderInputRef.value) {
    rootFolderInputRef.value.setAttribute('webkitdirectory', '')
  }
  window.addEventListener('fg:git_pending_changed', onGitSlotsMaybeChanged)
})

onBeforeUnmount(() => {
  window.removeEventListener('fg:git_pending_changed', onGitSlotsMaybeChanged)
})

const rootNodes = computed(() => nodes.value.filter((n) => n.parent_id === null))
const projectName = computed(() => projectStore.currentProject?.project_name ?? '')

// ── Tree accordion (0245 R0001 / NR0003 §2) ──────────────────────────────────
// One toggle rather than a separate expand and collapse button: the header row
// already carries three controls at 20px each. Its direction follows the tree's
// real state (anything open → collapse), so the button never lies about what a
// press will do. The static project root row is not a FileTreeNode and stays open.
const expandableFolderIds = computed(() =>
  nodes.value.filter((n) => n.type === 'folder').map((n) => n.id),
)

const anyFolderExpanded = computed(() => {
  const pid = props.projectId
  if (!pid) return false
  return expandableFolderIds.value.some((id) => explorerStore.isFileNodeExpanded(pid, id))
})

const accordionLabel = computed(() =>
  anyFolderExpanded.value ? t('main.explorer.collapse_all') : t('main.explorer.expand_all'),
)

function toggleAllFolders() {
  if (!props.projectId) return
  explorerStore.setFileNodesExpanded(props.projectId, expandableFolderIds.value, !anyFolderExpanded.value)
}

function normPath(p: string): string {
  return p.replace(/\\/g, '/')
}

async function reload() {
  if (!props.projectId) return
  // 0192 T0005 §2-a: the manual ↻ (and every tree-changed reload) must also
  // re-fetch the group slot dropdown. Previously only a mount/remount called
  // loadGroupSlots, so pressing refresh left removed/stale groups in the select
  // and never surfaced newly-created ones. loadGroupSlots also clears a
  // selectedGroup that has since vanished, so the branch/base decision below
  // reads the reconciled value.
  await loadGroupSlots(props.projectId)
  const silent = nodes.value.length > 0
  if (!silent) loading.value = true
  error.value = false
  try {
    if (selectedGroup.value) {
      const r = await explorerStore.fetchGroupBranchTree(props.projectId, selectedGroup.value)
      nodes.value = r.nodes
      groupCommit.value = r.commit
      await explorerStore.fetchGroupBranchChanges(props.projectId, selectedGroup.value)
      await loadGroupGitBadge(selectedGroup.value)
    } else {
      groupCommit.value = null
      groupGitState.value = null
      nodes.value = await explorerStore.fetchFileTree(props.projectId, true)
      // pendingSelectFilePath is only produced by base-checkout edits/creates.
      if (explorerStore.pendingSelectFilePath) {
        const target = normPath(explorerStore.pendingSelectFilePath)
        const found = nodes.value.find((n) => n.type === 'file' && normPath(n.path) === target)
        if (found) explorerStore.selectedFileNodeId = found.id
        explorerStore.pendingSelectFilePath = null
      }
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

const FILE_SIZE_LIMIT = 1024 * 1024
const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown'])

function fileExtension(path: string): string {
  const filename = normPath(path).split('/').pop() ?? path
  const dotIndex = filename.lastIndexOf('.')
  return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : ''
}

function isMarkdownFile(path: string): boolean {
  return MARKDOWN_EXTENSIONS.has(fileExtension(path))
}

function readContentLength(value: unknown): number | null {
  if (typeof value === 'number') return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number.parseInt(value, 10)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

async function openFile(node: FileNode) {
  const projectId = props.projectId
  if (!projectId) return
  let type: 'md' | 'text' | 'too_large' = isMarkdownFile(node.path) ? 'md' : 'text'
  // Group-branch read (checkout-free, read-only): the viewer loads content from
  // the blob endpoint (pinned to the tree commit) and handles binary/oversize.
  if (selectedGroup.value) {
    tabsStore.openTab({
      id: `git:${selectedGroup.value}:${node.id}`,
      title: node.label,
      path: node.path,
      type,
      mdPath: type === 'md' ? node.path : null,
      projectId,
      gitGroupId: selectedGroup.value,
      gitCommit: groupCommit.value,
      readonly: true,
    })
    return
  }
  try {
    const url = `/api/v1/projects/${encodeURIComponent(projectId)}/files/src-content?path=${encodeURIComponent(node.path)}`
    const res = await api.head(url)
    const contentLength = readContentLength(res.headers['content-length'])
    if (contentLength != null && contentLength > FILE_SIZE_LIMIT) {
      type = 'too_large'
    }
  } catch {
    // HEAD failed — keep the extension-based viewer type.
  }
  tabsStore.openTab({
    id: node.id,
    title: node.label,
    path: node.path,
    type,
    mdPath: type === 'md' ? node.path : null,
    projectId,
  })
}

// 0326 R0001 — "변경 내용 보기". The tab id is prefixed `diff:` so it can never
// collide with the editor tab for the same node (`node.id` in base mode,
// `git:${groupId}:${node.id}` in group mode): both tabs coexist, exactly as
// TR0003 §3 asked. The group-mode diff carries gitGroupId/gitCommit so the viewer
// hits the group endpoint pinned to the tree snapshot the file was opened from.
function openDiff(node: FileNode) {
  const projectId = props.projectId
  if (!projectId) return
  const group = selectedGroup.value
  tabsStore.openTab({
    id: group ? `diff:${group}:${node.id}` : `diff:${node.id}`,
    title: node.label,
    path: node.path,
    type: 'diff',
    mdPath: null,
    projectId,
    gitGroupId: group,
    gitCommit: group ? groupCommit.value : null,
    readonly: true,
  })
}

function onRootContextMenu(e: MouseEvent) {
  // 0327 T0004 (B0001 / NR0003 발견 1): this used to bail on ANY selected group, so the
  // project-root right-click produced no menu at all — no new folder, no new file, not
  // even refresh. The menu now always opens (as the per-node menu always has) and the
  // mutating entries are the part that depends on the group being writable.
  rootCtxX.value = e.clientX
  rootCtxY.value = e.clientY
  showRootCtx.value = true
}

function openRootCreateFolder() {
  showRootCtx.value = false
  modalType.value = 'folder'
  showModal.value = true
}

function openRootCreateFile() {
  showRootCtx.value = false
  modalType.value = 'file'
  showModal.value = true
}

function refreshFromMenu() {
  showRootCtx.value = false
  reload()
}

function onRootCreated(payload: { name: string; type: 'file' | 'folder' }) {
  // pendingSelectFilePath is consumed only by the base-checkout reload path; a
  // group-branch create must not leave one behind for a later base view to apply.
  if (payload.type === 'file' && !selectedGroup.value) {
    explorerStore.pendingSelectFilePath = payload.name
  }
  reload()
}

// ── Root drag and drop ───────────────────────────────────────────────────────
function onRootDragOver() {
  if (!canMutate.value) return
  rootDragOver.value = true
}

function onRootDragLeave(e: DragEvent) {
  const related = e.relatedTarget as Node | null
  if (!related || !(e.currentTarget as HTMLElement).contains(related)) {
    rootDragOver.value = false
  }
}

async function onRootDrop(e: DragEvent) {
  rootDragOver.value = false
  if (!canMutate.value) return
  if (!props.projectId || !e.dataTransfer?.items) return
  const files = await collectDropFiles(e.dataTransfer.items)
  await uploadFiles(props.projectId, '', files, reload, selectedGroup.value)
}

// ── Root upload button ───────────────────────────────────────────────────────
function triggerRootUploadFiles() {
  showRootCtx.value = false
  rootFileInputRef.value?.click()
}

function triggerRootUploadFolder() {
  showRootCtx.value = false
  rootFolderInputRef.value?.click()
}

async function onRootFileSelected(e: Event) {
  if (!props.projectId) return
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, '', files, reload, selectedGroup.value)
  input.value = ''
}

async function onRootFolderSelected(e: Event) {
  if (!props.projectId) return
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.webkitRelativePath || f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, '', files, reload, selectedGroup.value)
  input.value = ''
}

watch(() => props.projectId, async (pid, prevPid) => {
  // A genuine in-place project switch drops any group selection — group branches
  // are project-scoped. A remount with the same project (fresh instance from the
  // SSE-driven explorerRefreshKey bump → prevPid === undefined) instead PRESERVES
  // the active group so a group_view_refresh re-renders the (freshly fetched)
  // group tree rather than falling back to base (0186 finding 3). loadGroupSlots
  // is the safety net: on any project change the stale group_id is absent from the
  // new project's slots and is cleared there.
  if (prevPid !== undefined && prevPid !== pid) {
    selectedGroup.value = null
    explorerStore.activeGroupBranch = null
  }
  groupCommit.value = null
  groupGitState.value = null
  if (!pid) { nodes.value = []; groupSlots.value = []; return }
  loading.value = true
  error.value = false
  try {
    await loadGroupSlots(pid)
    if (selectedGroup.value) {
      explorerStore.activeGroupBranch = selectedGroup.value
      const r = await explorerStore.fetchGroupBranchTree(pid, selectedGroup.value)
      nodes.value = r.nodes
      groupCommit.value = r.commit
      await explorerStore.fetchGroupBranchChanges(pid, selectedGroup.value)
      await loadGroupGitBadge(selectedGroup.value)
    } else {
      explorerStore.activeGroupBranch = null
      nodes.value = await explorerStore.fetchFileTree(pid)
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}, { immediate: true })

// SSE refreshes must update tree data without replacing this component instance.
// Keeping the instance alive preserves open create dialogs and their in-progress input.
watch(() => props.refreshToken, (next, prev) => {
  if (next === prev || !props.projectId) return
  void reload()
})

</script>

<style scoped>
.tree-lbl--project {
  color: rgba(255, 255, 255, 0.85);
  font-weight: 600;
}

.fx-group-select {
  max-width: 130px;
  font-size: 0.72rem;
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.18);
}

.fx-group-select option {
  color: #1e293b;
  background: #fff;
}

.fx-readonly-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 0.72rem;
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.12);
  border-bottom: 1px solid rgba(59, 130, 246, 0.25);
}

/* 0327 T0004: the editable-worktree variant reads as "you can write here" rather
   than the blue read-only notice, so the two group views are told apart at a glance. */
.fx-readonly-badge--rw {
  color: #86efac;
  background: rgba(34, 197, 94, 0.12);
  border-bottom-color: rgba(34, 197, 94, 0.25);
}

.fx-git-badge {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 0.68rem;
  color: #fcd34d;
  background: rgba(245, 158, 11, 0.14);
  border: 1px solid rgba(245, 158, 11, 0.3);
}
</style>
